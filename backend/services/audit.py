"""
backend/services/audit.py — the one place that builds AuditLog rows.

Two entry points, because callers genuinely need different failure semantics:

  * ``audit_entry()`` builds the row and hands it back WITHOUT committing, so a
    caller can write it in the same transaction as the thing being audited. Use
    this when the action must not happen unless the trail records it — clinic
    impersonation, for instance: no audit row, no token.

  * ``record_audit()`` adds and commits on its own and swallows any error. Use
    this for after-the-fact notes on an action that has already succeeded (the
    provider-key endpoints), where failing the caller's request because the
    trail write failed would be worse than a gap in the trail.

Truncation lives here so no caller can overflow a column, and so
``detail`` length limits stay identical everywhere.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


def audit_entry(actor: str | None, action: str, target: str = "", detail: str = "") -> AuditLog:
    """Build an AuditLog row. Never commits — the caller owns the transaction."""
    return AuditLog(
        actor=(actor or "unknown")[:120],
        action=action[:40],
        target=(target or "")[:120],
        detail=(detail or "")[:500],
    )


async def record_audit(
    db: AsyncSession, actor: str | None, action: str, target: str = "", detail: str = ""
) -> None:
    """Write an audit row immediately, swallowing failures (see module docstring)."""
    try:
        db.add(audit_entry(actor, action, target, detail))
        await db.commit()
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("audit log write failed (%s/%s): %s", action, target, e)
