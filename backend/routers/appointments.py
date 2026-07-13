import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, ConfigDict
from datetime import datetime

from backend.auth import CurrentUser
from backend.db import get_db
from backend.models.appointment import Appointment

router = APIRouter()

class AppointmentResponse(BaseModel):
    id: str
    doctor_id: str
    slot_time: datetime
    patient_phone: str
    status: str

    model_config = ConfigDict(from_attributes=True)

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
    List appointments for a tenant.
    Returns masked patient phone number.
    Filters implicitly via tenant_id enforce Multi-Tenancy.
    """
    user.require_owns(str(tenant_id))
    stmt = select(Appointment).where(Appointment.tenant_id == tenant_id)
    
    if status is not None:
        stmt = stmt.where(Appointment.status == status)
        
    result = await db.execute(stmt)
    records = result.scalars().all()
    
    # Mask patient phone (e.g. +91XXXXXXXX99)
    res = []
    for r in records:
        masked_phone = r.patient_phone[:-4] + "****" if len(r.patient_phone) > 4 else "****"
        # ensure Pydantic parsing correctly maps id
        res.append({
            "id": str(r.id),
            "doctor_id": str(r.doctor_id),
            "slot_time": r.slot_time,
            "patient_phone": masked_phone,
            "status": r.status
        })
        
    return res
