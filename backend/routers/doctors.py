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
    his_doctor_id: str | None = None
    is_available: bool = True

# NOTE: ids are str, not uuid.UUID — the DB columns are varchar(36) and
# comparing a Python UUID against them makes Postgres raise
# "operator does not exist: character varying = uuid".
class DoctorResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    specialization: str
    his_doctor_id: str | None
    is_available: bool
    leave_reason: str | None
    created_at: Any

    model_config = ConfigDict(from_attributes=True)

class DoctorUpdate(BaseModel):
    name: str | None = None
    specialization: str | None = None
    his_doctor_id: str | None = None
    is_available: bool | None = None
    leave_reason: str | None = None

# ── Endpoints ────────────────────────────────────────────────────────
# ALL operations MUST filter by tenant_id (multi-tenant rule)

@router.post("/tenants/{tenant_id}/doctors", response_model=DoctorResponse, status_code=status.HTTP_201_CREATED)
async def add_doctor(tenant_id: str, payload: DoctorCreate, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    user.require_owns(tenant_id)
    # Verify tenant exists
    tenant = await db.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
        
    doctor = Doctor(
        tenant_id=tenant_id,
        name=payload.name,
        specialization=payload.specialization,
        his_doctor_id=payload.his_doctor_id,
        is_available=payload.is_available,
    )
    db.add(doctor)
    await db.commit()
    await db.refresh(doctor)

    from backend.services.his import invalidate_doctor_cache
    invalidate_doctor_cache(tenant_id)
    return doctor

@router.get("/tenants/{tenant_id}/doctors", response_model=list[DoctorResponse])
async def list_doctors(tenant_id: str, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    user.require_owns(tenant_id)
    result = await db.execute(
        select(Doctor).where(Doctor.tenant_id == tenant_id)
    )
    doctors = result.scalars().all()
    return list(doctors)

@router.patch("/tenants/{tenant_id}/doctors/{doctor_id}", response_model=DoctorResponse)
async def update_doctor(
    tenant_id: str, doctor_id: str, payload: DoctorUpdate,
    user: CurrentUser = None, db: AsyncSession = Depends(get_db)
):
    """Update a doctor's profile and/or availability. Clinic admins and
    superadmin can both call this — CurrentUser.require_owns() already lets a
    superadmin token through for any tenant_id."""
    user.require_owns(tenant_id)
    result = await db.execute(
        select(Doctor).where(Doctor.id == doctor_id, Doctor.tenant_id == tenant_id)
    )
    doctor = result.scalar_one_or_none()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    if payload.name is not None:
        doctor.name = payload.name
    if payload.specialization is not None:
        doctor.specialization = payload.specialization
    if payload.his_doctor_id is not None:
        doctor.his_doctor_id = payload.his_doctor_id
    if payload.is_available is not None:
        doctor.is_available = payload.is_available
        doctor.leave_reason = payload.leave_reason if not payload.is_available else None

    await db.commit()
    await db.refresh(doctor)

    from backend.services.his import invalidate_doctor_cache
    invalidate_doctor_cache(tenant_id)
    return doctor

@router.delete("/tenants/{tenant_id}/doctors/{doctor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_doctor(tenant_id: str, doctor_id: str, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    user.require_owns(tenant_id)
    result = await db.execute(
        select(Doctor).where(
            Doctor.id == doctor_id,
            Doctor.tenant_id == tenant_id
        )
    )
    doctor = result.scalar_one_or_none()

    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    # Delete this doctor's appointments first — Appointment.doctor_id is
    # NOT NULL, so the DB can't satisfy its own ON DELETE SET NULL rule and
    # would otherwise raise an IntegrityError for any doctor with a booking.
    from backend.models.appointment import Appointment
    from sqlalchemy import delete as sa_delete
    await db.execute(sa_delete(Appointment).where(Appointment.doctor_id == doctor_id))

    await db.delete(doctor)
    await db.commit()

    from backend.services.his import invalidate_doctor_cache
    invalidate_doctor_cache(tenant_id)
    return None
