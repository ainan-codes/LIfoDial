"""
backend/services/impersonation.py — superadmin "view as this clinic" sessions.

WHAT THIS IS
    A superadmin support shortcut: open a clinic's own admin dashboard, scoped to
    that clinic, without knowing or using the clinic's password. The clinic's
    credentials are never read, written, or shown anywhere in this flow — the
    only thing minted is a short-lived token tied to one tenant_id.

WHY THE TOKEN IS A *CLINIC* TOKEN, NOT A SUPERADMIN ONE
    ``role="clinic"`` and ``sub=<tenant_id>`` is the whole scoping mechanism, and
    it reuses the authorisation the app already enforces rather than adding a
    parallel path:

      * backend/auth.py derives ``tenant_id`` from ``sub`` for any non-superadmin
        role, and every tenant-scoped handler calls ``user.require_owns(...)``,
        which 404s on a different clinic's id. So "cannot be used to access any
        clinic other than the one it was issued for" is not a new check written
        here — it is the check that already guards clinic logins.
      * ``is_superadmin`` is False, so a leaked impersonation token opens NO
        superadmin endpoint. Minting a superadmin token with a tenant filter
        bolted on would have been the general-purpose auth bypass this must not
        become.

    It also means the superadmin sees exactly what the clinic sees, redactions
    included (see redact_agent_for_clinic) — which is the point of "view as".

WHY THERE IS A DATABASE ROW PER SESSION
    JWTs cannot be un-signed, so "Exit" could not otherwise revoke anything and
    the token would stay live for its full TTL. auth.py checks the row on every
    request made with an impersonation token; ``end()`` sets ended_at and the
    next request 401s. The row is also the queryable audit trail — see
    backend/models/impersonation_session.py.

TTL
    30 minutes. Long enough for a real support session, short enough that a
    forgotten tab stops being an open door. There is deliberately no refresh or
    extend path: re-impersonating is one click and writes a fresh audit row.

COST
    Every request made with an impersonation token costs one extra DB round trip
    (and, under the API's NullPool, one connection handshake) for that row lookup.
    That is accepted knowingly: caching it would reintroduce a window in which an
    exited session still works, and "Exit means exited" is the promise this feature
    is built on. Only impersonated requests pay it — ordinary clinic and superadmin
    tokens carry no `imp` claim and never reach this module.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.impersonation_session import ImpersonationSession
from backend.security import IMPERSONATION_CLAIM, create_access_token

logger = logging.getLogger(__name__)

#: How long a freshly minted impersonation token is usable for.
IMPERSONATION_TTL = timedelta(minutes=30)

# IMPERSONATION_CLAIM (re-exported above) is the claim whose presence sends
# backend/auth.py to the database for the revocation check. Set it on every
# impersonation token and on nothing else.


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; treat those as UTC (that is what we wrote)."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def build_session(actor: str, tenant_id: str) -> tuple[ImpersonationSession, str]:
    """Create (unsaved) session row + its token.

    The row is NOT added to a session here: the caller adds it and commits it in
    the same transaction as the audit entry, so a token is never handed out
    without both records landing. See routers/admin.py::impersonate_clinic.
    """
    session_id = str(uuid.uuid4())
    started = _now()
    expires = started + IMPERSONATION_TTL

    row = ImpersonationSession(
        id=session_id,
        actor=actor or "superadmin",
        tenant_id=tenant_id,
        started_at=started,
        expires_at=expires,
    )

    token = create_access_token(
        subject=tenant_id,
        role="clinic",
        extra={
            IMPERSONATION_CLAIM: True,
            # Links the token to its row — the only handle "end this session" has.
            "jti": session_id,
            # Who is behind the clinic-looking session. Informational (the row is
            # authoritative), but it makes a decoded token self-explanatory.
            "act": actor or "superadmin",
        },
        ttl=IMPERSONATION_TTL,
    )
    return row, token


async def load_active(db: AsyncSession, session_id: str, tenant_id: str) -> ImpersonationSession | None:
    """Return the session iff it is live and belongs to `tenant_id`.

    Live means: exists, not ended, not past expires_at. The tenant_id argument is
    the token's `sub`, so a token whose subject does not match its own session row
    is rejected — a scoping bug in the minting path cannot widen access later.
    """
    row = (
        await db.execute(
            select(ImpersonationSession).where(ImpersonationSession.id == session_id)
        )
    ).scalar_one_or_none()

    if row is None or row.tenant_id != tenant_id:
        return None
    if row.ended_at is not None:
        return None

    expires_at = _as_utc(row.expires_at)
    if expires_at is not None and expires_at <= _now():
        return None
    return row


async def claims_still_active(claims: dict | None) -> bool:
    """Revocation check for code paths that decode a token themselves.

    The WebSocket endpoints authenticate from ``?token=`` (browsers cannot set
    headers on a WS handshake) and so never go through backend/auth.py's
    dependency. Without this they would honour an impersonation token for the full
    30 minutes of its JWT ``exp`` even after the superadmin exited — a socket that
    outlives the session it belongs to is exactly the "exit didn't really end it"
    hole this feature must not have.

    Returns True for every NON-impersonation token, so ordinary clinic and
    superadmin sockets are unaffected and pay no database round trip.
    """
    if not claims or not claims.get(IMPERSONATION_CLAIM):
        return True

    session_id = claims.get("jti")
    subject = claims.get("sub")
    # Same defence in depth as auth.py: an impersonation claim on anything other
    # than a single-clinic token is not something to interpret generously.
    if not session_id or not subject or claims.get("role", "clinic") != "clinic":
        return False

    from backend.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        return await load_active(db, session_id, subject) is not None


async def end(db: AsyncSession, session_id: str, reason: str = "exit") -> ImpersonationSession | None:
    """Mark a session finished. Idempotent; returns the row (or None if unknown).

    Does not commit — the caller commits this together with its audit entry, so
    "session ended" and "we recorded that it ended" cannot come apart.
    """
    row = (
        await db.execute(
            select(ImpersonationSession).where(ImpersonationSession.id == session_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.ended_at is None:
        row.ended_at = _now()
        row.ended_reason = reason[:20]
    return row


async def expire_stale(db: AsyncSession) -> int:
    """Stamp ended_at on sessions that ran past their TTL without an explicit exit.

    Not a security control — auth.py already refuses an expired token whether or
    not this ran. It exists so the audit trail answers "when did it end" for
    every row instead of leaving abandoned sessions looking permanently open.
    """
    now = _now()
    stale = (
        await db.execute(
            select(ImpersonationSession).where(
                ImpersonationSession.ended_at.is_(None),
                ImpersonationSession.expires_at <= now,
            )
        )
    ).scalars().all()
    for row in stale:
        row.ended_at = _as_utc(row.expires_at) or now
        row.ended_reason = "expired"
    return len(stale)
