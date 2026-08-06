"""
backend/models/impersonation_session.py

One row per superadmin "view as this clinic" session (see
backend/services/impersonation.py and POST /admin/clinics/{id}/impersonate).

The row is BOTH the audit record and the revocation list:

  * Audit — who (actor), which clinic (tenant_id), when it started (started_at),
    when it stopped (ended_at + ended_reason). AuditLog carries the same events
    in the shared trail, but that table is append-only text; this one is
    queryable per clinic ("who looked at this clinic's dashboard, and when").

  * Revocation — the minted JWT is stateless and cannot be un-signed, so
    backend/auth.py looks this row up on EVERY request made with an
    impersonation token and rejects the token the moment ended_at is set or
    expires_at passes. Without the row the token would stay valid for its full
    TTL after the superadmin clicked "Exit", which is exactly what the feature
    promises it does not do.

`id` doubles as the JWT's `jti` claim — that is the only link between a token
and its session, and it is what makes "end this one session" possible.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, func

from backend.db import Base


class ImpersonationSession(Base):
    __tablename__ = "impersonation_sessions"

    # Also the `jti` of the issued token.
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # JWT subject of the superadmin who started it (today always "superadmin";
    # stored rather than assumed so a future multi-operator setup keeps working).
    actor = Column(String(120), nullable=False)

    # The ONE clinic this session may see. The token's `sub` must equal this.
    tenant_id = Column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )

    started_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )
    # Hard stop, independent of ended_at: even a session nobody exits dies here.
    expires_at = Column(DateTime(timezone=True), nullable=False)

    # Set when the session stops being usable. NULL = still live.
    ended_at = Column(DateTime(timezone=True), nullable=True)
    # "exit" (superadmin clicked Exit) | "expired" (TTL reached first)
    ended_reason = Column(String(20), nullable=True)
