"""
backend/services/tenant_service.py — shared clinic (Tenant) creation + deletion.

Single source of truth for "insert a new Tenant row" so callers (the
POST /tenants endpoint and the inline new-clinic path in POST /agents)
don't each hand-roll their own Tenant(...) construction — and, likewise, for
"delete a clinic and everything that references it" (see delete_tenant_cascade).
"""
import logging
import uuid

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.tenant import Tenant

log = logging.getLogger(__name__)


async def create_tenant(
    session: AsyncSession,
    *,
    clinic_name: str,
    admin_name: str | None = None,
    admin_email: str | None = None,
    phone: str | None = None,
    location: str | None = None,
    language: str = "en-IN",
    admin_password: str | None = None,
) -> Tenant:
    """
    Insert a new Tenant row and flush it (does not commit — caller controls
    the transaction boundary so this can participate in a larger atomic
    operation, e.g. clinic+agent creation together).

    ``admin_password`` MUST already be hashed (see backend.security.hash_password)
    — passing it here is what makes the clinic login actually work. It used to be
    left NULL, so wizard-created clinics could never log in even though the
    success screen showed a password (audit P2).

    Raises sqlalchemy.exc.IntegrityError if a clinic with the same name
    (case-insensitive) already exists — see the unique index on
    lower(clinic_name) added by the multi-agent-per-clinic migration — or the
    same admin_email (see the unique index on lower(admin_email)).
    """
    tenant = Tenant(
        id=str(uuid.uuid4()),
        clinic_name=clinic_name.strip(),
        admin_name=admin_name,
        admin_email=admin_email.strip().lower() if admin_email else None,
        phone=phone,
        location=location,
        language=language,
        status="active",
        admin_password=admin_password,
    )
    session.add(tenant)
    await session.flush()
    return tenant


async def delete_tenant_cascade(session: AsyncSession, tenant: Tenant) -> dict[str, int]:
    """Delete a clinic and every row that references it. Does NOT commit — the
    caller owns the transaction boundary, so a failure anywhere leaves the clinic
    fully intact rather than half-deleted.

    Returns {table_name: rows_deleted} for logging/auditing.

    Why the cascade is spelled out here instead of relying on the database:
    this project's Alembic migrations are never actually applied at deploy time
    (init_db() only performs additive ADD COLUMN changes), so the live schema's
    real FK constraints cannot be trusted to match the model files. Several
    tenant_id FKs are ON DELETE NO ACTION in the live database even though the
    model declares CASCADE, and `embed_events.tenant_id` has no FK at all.

    ORDER MATTERS — children before the rows they point at:
      * appointments MUST precede doctors. appointments.doctor_id is declared
        ON DELETE SET NULL but the column is NOT NULL, so Postgres cannot satisfy
        its own rule and raises IntegrityError for any doctor with a booking.
        (Same trap handled in routers/doctors.py::delete_doctor.)
      * bulk_call_campaigns / phone_numbers / call_records all reference
        agent_configs.agent_id with NO ACTION, so they MUST precede agent_configs.
      * credit_transactions (ledger) before clinic_credits (balance).
    """
    from backend.models.agent_config import AgentConfig
    from backend.models.appointment import Appointment
    from backend.models.bulk_call import BulkCallCampaign
    from backend.models.call_log import CallLog
    from backend.models.call_record import CallRecord
    from backend.models.clinic_credits import ClinicCredits, CreditTransaction
    from backend.models.doctor import Doctor
    from backend.models.knowledge_base import KnowledgeBase
    from backend.models.phone_number import PhoneNumber

    tenant_id = tenant.id
    deleted: dict[str, int] = {}

    async def _wipe(model, label: str, column_name: str = "tenant_id") -> None:
        column = getattr(model, column_name)
        result = await session.execute(sa_delete(model).where(column == tenant_id))
        deleted[label] = result.rowcount or 0

    # 1. Leaf tables referencing tenant_id and/or agent_id.
    await _wipe(Appointment, "appointments")
    await _wipe(BulkCallCampaign, "bulk_call_campaigns")
    await _wipe(PhoneNumber, "phone_numbers")
    await _wipe(CallRecord, "call_records")
    await _wipe(CallLog, "call_logs")
    await _wipe(KnowledgeBase, "knowledge_bases")
    await _wipe(CreditTransaction, "credit_transactions")
    await _wipe(ClinicCredits, "clinic_credits")

    # 2. Widget analytics. This table has NO foreign key on tenant_id, so nothing
    # in the database protects it and it was silently missed by the previous
    # hand-written cascades — leaving orphaned rows for every deleted clinic.
    # Imported defensively: it is analytics, and losing the delete of a stale
    # counter must never be what blocks removing a clinic.
    try:
        from backend.models.embed_analytics import EmbedEvent

        await _wipe(EmbedEvent, "embed_events")
    except Exception as e:  # pragma: no cover - defensive
        log.warning("Could not delete embed_events for tenant %s: %s", tenant_id, e)

    # 2b. Superadmin impersonation sessions for this clinic. These are auth state,
    # not history — the clinic is going away, so nothing may still be holding a
    # usable session for it. The audit trail of who viewed this clinic survives in
    # audit_logs, which is deliberately not tenant-scoped and not deleted here.
    # Defensive like the two blocks around it: a stale session row must never be
    # what blocks removing a clinic (and the rows are already unusable once the
    # tenant is gone — every handler they could reach 404s without the tenant).
    try:
        from backend.models.impersonation_session import ImpersonationSession

        await _wipe(ImpersonationSession, "impersonation_sessions")
    except Exception as e:  # pragma: no cover - defensive
        log.warning("Could not delete impersonation_sessions for tenant %s: %s", tenant_id, e)

    # 3. agent_prompt_history hangs off agent_id, not tenant_id. The live FK is
    # ON DELETE CASCADE so today it would go automatically, but that is
    # undocumented luck given migrations never run — be explicit.
    try:
        from backend.models.agent_prompt_history import AgentPromptHistory

        agent_ids = (
            await session.execute(select(AgentConfig.id).where(AgentConfig.tenant_id == tenant_id))
        ).scalars().all()
        if agent_ids:
            result = await session.execute(
                sa_delete(AgentPromptHistory).where(AgentPromptHistory.agent_id.in_(agent_ids))
            )
            deleted["agent_prompt_history"] = result.rowcount or 0
    except Exception as e:  # pragma: no cover - defensive
        log.warning("Could not delete agent_prompt_history for tenant %s: %s", tenant_id, e)

    # 4. Agents and doctors — nothing references them for this tenant any more.
    await _wipe(AgentConfig, "agent_configs")
    await _wipe(Doctor, "doctors")

    # 5. Finally the clinic itself. The clinic admin's login lives ON this row
    # (Tenant.admin_email / admin_password) — there is no separate users table —
    # so removing it is what actually revokes their access.
    await session.delete(tenant)
    deleted["tenants"] = 1

    log.info("Cascade-deleted tenant %s: %s", tenant_id, deleted)
    return deleted
