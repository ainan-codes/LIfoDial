"""
backend/services/tenant_service.py — shared clinic (Tenant) creation logic.

Single source of truth for "insert a new Tenant row" so callers (the
POST /tenants endpoint and the inline new-clinic path in POST /agents)
don't each hand-roll their own Tenant(...) construction.
"""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.tenant import Tenant


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
