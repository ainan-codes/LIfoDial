import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict
from datetime import datetime

from backend.auth import CurrentUser
from backend.db import get_db
from backend.models.appointment import Appointment
from backend.models.doctor import Doctor

router = APIRouter()

class AppointmentResponse(BaseModel):
    id: str
    doctor_id: str
    doctor_name: str
    specialization: str
    slot_time: datetime
    patient_phone: str
    status: str
    #: Patient's name as the agent captured it, when it captured one.
    patient_name: Optional[str] = None
    #: Which channel booked this — 'voice' | 'web_voice' | 'chat' | 'embed' |
    #: 'dashboard', or null for a row written before the column existed whose
    #: channel could not be inferred. The dashboard's "Booked Via" column reads
    #: this; it used to hardcode "Voice" for every row, which was wrong about
    #: every appointment in production.
    source: Optional[str] = None
    #: Set the moment a RESCHEDULE actually moved this row — null otherwise.
    #: `status` stays 'confirmed' either way (see the model); this is what lets
    #: the UI show a third "Rescheduled" colour without touching that.
    rescheduled_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class AppointmentUpdate(BaseModel):
    status: str  # "pending" | "confirmed" | "cancelled"

@router.get("/{tenant_id}/appointments", response_model=List[AppointmentResponse])
async def list_appointments(
    tenant_id: str,
    status: Optional[str] = None,
    # Here we would normally use datetime dates instead of strings, simplified for testing
    date: Optional[str] = None,
    user: CurrentUser = None,
    db: AsyncSession = Depends(get_db)
):
    """
    List appointments for a tenant, with the doctor's name/specialization
    joined in (the dashboard needs both, not just doctor_id).
    Returns masked patient phone number.
    Filters implicitly via tenant_id enforce Multi-Tenancy.
    """
    user.require_owns(str(tenant_id))
    stmt = (
        select(Appointment, Doctor.name, Doctor.specialization)
        .join(Doctor, Doctor.id == Appointment.doctor_id, isouter=True)
        .where(Appointment.tenant_id == tenant_id)
        .order_by(Appointment.slot_time.desc())
    )

    if status is not None:
        stmt = stmt.where(Appointment.status == status)

    result = await db.execute(stmt)
    rows = result.all()

    # Mask patient phone (e.g. +91XXXXXXXX99)
    res = []
    for r, doctor_name, specialization in rows:
        masked_phone = r.patient_phone[:-4] + "****" if len(r.patient_phone) > 4 else "****"
        res.append({
            "id": str(r.id),
            "doctor_id": str(r.doctor_id),
            "doctor_name": doctor_name or "Unknown",
            "specialization": specialization or "General",
            "slot_time": r.slot_time,
            "patient_phone": masked_phone,
            "status": r.status,
            "patient_name": r.patient_name,
            "source": r.source,
            "rescheduled_at": r.rescheduled_at,
        })

    return res

@router.patch("/{tenant_id}/appointments/{appointment_id}", response_model=AppointmentResponse)
async def update_appointment(
    tenant_id: str,
    appointment_id: str,
    payload: AppointmentUpdate,
    user: CurrentUser = None,
    db: AsyncSession = Depends(get_db)
):
    """Update an appointment's status (e.g. cancel it from the dashboard)."""
    user.require_owns(str(tenant_id))

    valid_statuses = {"pending", "confirmed", "cancelled"}
    if payload.status not in valid_statuses:
        raise HTTPException(status_code=422, detail=f"status must be one of {sorted(valid_statuses)}")

    stmt = select(Appointment).where(
        Appointment.id == appointment_id,
        Appointment.tenant_id == tenant_id,
    )
    appointment = (await db.execute(stmt)).scalar_one_or_none()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")

    appointment.status = payload.status
    await db.commit()
    await db.refresh(appointment)

    doctor = (await db.execute(select(Doctor).where(Doctor.id == appointment.doctor_id))).scalar_one_or_none()
    masked_phone = (
        appointment.patient_phone[:-4] + "****" if len(appointment.patient_phone) > 4 else "****"
    )
    return {
        "id": str(appointment.id),
        "doctor_id": str(appointment.doctor_id),
        "doctor_name": doctor.name if doctor else "Unknown",
        "specialization": doctor.specialization if doctor else "General",
        "slot_time": appointment.slot_time,
        "patient_phone": masked_phone,
        "status": appointment.status,
        "patient_name": appointment.patient_name,
        "source": appointment.source,
        "rescheduled_at": appointment.rescheduled_at,
    }
