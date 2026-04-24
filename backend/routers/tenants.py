import uuid
import random
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import get_db
from backend.models.tenant import Tenant

router = APIRouter()

# ── Schemas ──────────────────────────────────────────────────────────

class TenantCreate(BaseModel):
    clinic_name: str
    primary_language: str = "en-IN"

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

@router.post("", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(payload: TenantCreate, db: AsyncSession = Depends(get_db)):
    tenant_uuid = uuid.uuid4()
    tenant = Tenant(
        id=tenant_uuid,
        tenant_id=tenant_uuid,
        clinic_name=payload.clinic_name,
        language=payload.primary_language
    )
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)
    return tenant

@router.get("/{id}")
async def get_tenant(id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Tenant).where(Tenant.id == id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant

@router.post("/{id}/assign-number", response_model=AssignNumberResponse)
async def assign_number(id: uuid.UUID, db: AsyncSession = Depends(get_db)):
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
async def get_forwarding_instructions(id: uuid.UUID, db: AsyncSession = Depends(get_db)):
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
async def delete_tenant(id: uuid.UUID, db: AsyncSession = Depends(get_db)):
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
