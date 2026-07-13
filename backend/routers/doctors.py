import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import CurrentUser
from backend.db import get_db
from backend.models.doctor import Doctor
from backend.models.tenant import Tenant

router = APIRouter()

# ── Schemas ──────────────────────────────────────────────────────────

class DoctorCreate(BaseModel):
    name: str
    specialization: str

class DoctorResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    specialization: str
    his_doctor_id: str | None
    created_at: Any

    model_config = ConfigDict(from_attributes=True)

# ── Endpoints ────────────────────────────────────────────────────────
# ALL operations MUST filter by tenant_id (multi-tenant rule)

@router.post("/tenants/{tenant_id}/doctors", response_model=DoctorResponse, status_code=status.HTTP_201_CREATED)
async def add_doctor(tenant_id: uuid.UUID, payload: DoctorCreate, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    user.require_owns(str(tenant_id))
    # Verify tenant exists
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
        
    doctor = Doctor(
        tenant_id=tenant_id,
        name=payload.name,
        specialization=payload.specialization
    )
    db.add(doctor)
    await db.commit()
    await db.refresh(doctor)
    return doctor

@router.get("/tenants/{tenant_id}/doctors", response_model=list[DoctorResponse])
async def list_doctors(tenant_id: uuid.UUID, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    user.require_owns(str(tenant_id))
    result = await db.execute(
        select(Doctor).where(Doctor.tenant_id == tenant_id)
    )
    doctors = result.scalars().all()
    return list(doctors)

@router.delete("/tenants/{tenant_id}/doctors/{doctor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_doctor(tenant_id: uuid.UUID, doctor_id: uuid.UUID, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    user.require_owns(str(tenant_id))
    result = await db.execute(
        select(Doctor).where(
            Doctor.id == doctor_id,
            Doctor.tenant_id == tenant_id
        )
    )
    doctor = result.scalar_one_or_none()
    
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
        
    await db.delete(doctor)
    await db.commit()
    return None
