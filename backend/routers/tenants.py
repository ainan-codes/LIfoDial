import uuid
import random
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import CurrentUser, SuperAdmin
from backend.db import get_db
from backend.models.tenant import Tenant
from backend.services.tenant_service import create_tenant as create_tenant_row

router = APIRouter()

# ── Schemas ──────────────────────────────────────────────────────────

class TenantCreate(BaseModel):
    clinic_name: str
    primary_language: str = "en-IN"

class TenantUpdate(BaseModel):
    clinic_name: str | None = None
    google_sheets_webhook_url: str | None = None

class TenantResponse(BaseModel):
    id: uuid.UUID
    clinic_name: str
    language: str
    ai_number: str | None
    created_at: Any

    model_config = ConfigDict(from_attributes=True)

class AssignNumberResponse(BaseModel):
    ai_number: str
    forwarding_instructions: str

# ── Endpoints ────────────────────────────────────────────────────────

@router.get("")
async def list_tenants(user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    """List all tenants/clinics with agent_count for the CreateAgent wizard."""
    from backend.models.agent_config import AgentConfig
    try:
        result = await db.execute(select(Tenant).order_by(Tenant.clinic_name))
        tenants = result.scalars().all()

        # Count agents per tenant_id — normalize to str
        agent_res = await db.execute(
            select(AgentConfig.tenant_id, func.count(AgentConfig.id)).group_by(AgentConfig.tenant_id)
        )
        agent_counts = {str(row[0]): row[1] for row in agent_res.fetchall()}

        return [
            {
                "id": str(t.id),
                "clinic_name": getattr(t, "clinic_name", "") or "",
                "admin_email": getattr(t, "admin_email", "") or "",
                "admin_name": getattr(t, "admin_name", "") or "",
                "language": getattr(t, "language", "en-IN") or "en-IN",
                "status": getattr(t, "status", "active") or "active",
                "agent_count": agent_counts.get(str(t.id), 0),
                # Kept for any older frontend code still reading this field.
                "has_agent": agent_counts.get(str(t.id), 0) > 0,
            }
            for t in tenants
        ]
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).error("list_tenants error: %s", e, exc_info=True)
        # Return empty list instead of 500 so the UI doesn't crash
        return []


@router.get("/search")
async def search_tenants(q: str = "", limit: int = 8, user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    """
    Type-ahead clinic search for the Create Agent picker.
    Superadmin only. Case-insensitive match on clinic_name.
    """
    from backend.models.agent_config import AgentConfig

    limit = max(1, min(limit, 25))
    stmt = select(Tenant).order_by(Tenant.clinic_name).limit(limit)
    q = q.strip()
    if q:
        stmt = stmt.where(Tenant.clinic_name.ilike(f"%{q}%"))
    result = await db.execute(stmt)
    tenants = result.scalars().all()
    if not tenants:
        return []

    tenant_ids = [t.id for t in tenants]
    agent_res = await db.execute(
        select(AgentConfig.tenant_id, func.count(AgentConfig.id))
        .where(AgentConfig.tenant_id.in_(tenant_ids))
        .group_by(AgentConfig.tenant_id)
    )
    agent_counts = {str(row[0]): row[1] for row in agent_res.fetchall()}

    return [
        {
            "id": str(t.id),
            "clinic_name": t.clinic_name,
            "admin_email": t.admin_email or "",
            "language": t.language,
            "agent_count": agent_counts.get(str(t.id), 0),
        }
        for t in tenants
    ]


@router.post("", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(payload: TenantCreate, user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    try:
        tenant = await create_tenant_row(
            db,
            clinic_name=payload.clinic_name,
            language=payload.primary_language,
        )
        await db.commit()
        await db.refresh(tenant)
        return tenant
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A clinic named '{payload.clinic_name}' already exists.",
        )

@router.get("/{id}")
async def get_tenant(id: uuid.UUID, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    user.require_owns(str(id))
    result = await db.execute(select(Tenant).where(Tenant.id == id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant

@router.post("/{id}/assign-number", response_model=AssignNumberResponse)
async def assign_number(id: uuid.UUID, user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Tenant).where(Tenant.id == id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    
    # Generate mock AI number
    mock_number = f"+919000{random.randint(100000, 999999)}"
    tenant.ai_number = mock_number
    await db.commit()
    
    instructions = (
        f"To forward calls: Dial *21*{mock_number}# from your clinic landline.\n"
        f"To stop forwarding: Dial ##21#\n"
        f"Your AI number: {mock_number}"
    )
    
    return AssignNumberResponse(
        ai_number=mock_number,
        forwarding_instructions=instructions
    )

@router.get("/{id}/forwarding-instructions")
async def get_forwarding_instructions(id: uuid.UUID, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    user.require_owns(str(id))
    result = await db.execute(select(Tenant).where(Tenant.id == id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
        
    if not tenant.ai_number:
        raise HTTPException(status_code=400, detail="No AI number assigned yet")
        
    instructions = (
        f"To forward calls: Dial *21*{tenant.ai_number}# from your clinic landline.\n"
        f"To stop forwarding: Dial ##21#\n"
        f"Your AI number: {tenant.ai_number}"
    )
    
    return {"instructions": instructions}


@router.delete("/{id}", status_code=204)
async def delete_tenant(id: uuid.UUID, user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    """Delete a clinic and all associated agents."""
    result = await db.execute(select(Tenant).where(Tenant.id == id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Delete associated agents first (FK constraint)
    from backend.models.agent_config import AgentConfig
    from sqlalchemy import delete as sa_delete
    await db.execute(sa_delete(AgentConfig).where(AgentConfig.tenant_id == str(id)))

    await db.delete(tenant)
    await db.commit()


@router.put("/{id}")
async def update_tenant(id: uuid.UUID, payload: TenantUpdate, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    """Update a tenant/clinic's profile details including webhook settings."""
    user.require_owns(str(id))
    result = await db.execute(select(Tenant).where(Tenant.id == id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
        
    if payload.clinic_name is not None:
        tenant.clinic_name = payload.clinic_name
    if payload.google_sheets_webhook_url is not None:
        tenant.google_sheets_webhook_url = payload.google_sheets_webhook_url
        
    await db.commit()
    await db.refresh(tenant)
    return {
        "id": str(tenant.id),
        "clinic_name": tenant.clinic_name,
        "google_sheets_webhook_url": tenant.google_sheets_webhook_url,
        "language": tenant.language,
        "ai_number": tenant.ai_number,
    }
