import logging
from typing import List, Dict, Any
from sqlalchemy import select
from datetime import datetime
import json

from backend.config import settings
from backend.db import AsyncSessionLocal
from backend.models.doctor import Doctor
from backend.models.appointment import Appointment

logger = logging.getLogger(__name__)

# Simple in-memory cache for doctors (no Redis dependency)
_doctor_cache: dict[str, tuple[float, list]] = {}
_CACHE_TTL = 3600  # 1 hour


async def _get_cached_doctors(tenant_id: str) -> List[Dict[str, Any]] | None:
    key = f"{tenant_id}:doctors:list"
    if key in _doctor_cache:
        ts, data = _doctor_cache[key]
        import time
        if time.time() - ts < _CACHE_TTL:
            return data
        del _doctor_cache[key]
    return None


async def _set_cached_doctors(tenant_id: str, doctors: List[Dict[str, Any]]) -> None:
    import time
    key = f"{tenant_id}:doctors:list"
    _doctor_cache[key] = (time.time(), doctors)

async def get_doctors(tenant_id: str, specialization: str = None) -> List[dict]:
    # Check cache first (ignore specialization exactly in cache key, filter in memory)
    cached = await _get_cached_doctors(tenant_id)
    doctors = []

    if cached is not None:
        doctors = cached
    else:
        # Check HIS API setup in the future
        # if settings.oxzygen_base_url: ... (HTTPX Call) ...
        # Fallback to local database logic
        async with AsyncSessionLocal() as session:
            stmt = select(Doctor).where(Doctor.tenant_id == tenant_id)
            result = await session.execute(stmt)
            db_docs = result.scalars().all()
            
            doctors = [
                {
                    "id": str(d.id),
                    "name": d.name,
                    "specialization": d.specialization,
                    "his_doctor_id": d.his_doctor_id
                }
                for d in db_docs
            ]
        
        # Save to cache
        await _set_cached_doctors(tenant_id, doctors)

    # Filter specialization in-memory if requested
    if specialization:
        spec_lower = specialization.lower()
        doctors = [d for d in doctors if d.get("specialization", "").lower() == spec_lower]

    return doctors


async def get_slots(doctor_id: str, date: str = None) -> List[str]:
    # Never cache slots!
    # Mock slots for upcoming days depending on doctor schedule
    return ["9:00 AM", "11:00 AM", "2:00 PM", "4:30 PM"]


from backend.models.tenant import Tenant
import asyncio
import httpx

async def send_to_sheets_webhook(webhook_url: str | None, payload: dict):
    """Sends appointment details to a Google Sheets webhook in the background.
    Falls back to settings.google_sheets_webhook_url if no clinic-specific webhook is set.
    """
    target_url = webhook_url or settings.google_sheets_webhook_url
    if not target_url:
        logger.info("No Google Sheets webhook URL configured. Skipping sheet sync.")
        return

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                target_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=5.0
            )
            if response.status_code == 200:
                logger.info(f"Successfully pushed appointment {payload.get('appointment_id')} to Google Sheets.")
            else:
                logger.error(f"Google Sheets webhook failed with status {response.status_code}: {response.text}")
    except Exception as e:
        logger.error(f"Error pushing to Google Sheets: {e}", exc_info=True)


async def create_appointment(tenant_id: str, doctor_id: str, slot_time: str, patient_phone: str) -> dict:
    # Future HIS Integration: POST to /appointments
    # if settings.oxzygen_base_url: ...

    async with AsyncSessionLocal() as session:
        # Resolve doctor name
        stmt = select(Doctor).where(Doctor.id == doctor_id).where(Doctor.tenant_id == tenant_id)
        doctor = (await session.execute(stmt)).scalar_one_or_none()
        doc_name = doctor.name if doctor else "Unknown"
        specialization = doctor.specialization if doctor else "Specialist"
        
        # Resolve clinic name
        stmt_t = select(Tenant).where(Tenant.id == tenant_id)
        tenant = (await session.execute(stmt_t)).scalar_one_or_none()
        clinic_name = tenant.clinic_name if tenant else "Unknown Clinic"
        clinic_webhook = tenant.google_sheets_webhook_url if tenant else None

        appointment = Appointment(
            tenant_id=tenant_id,
            doctor_id=doctor_id,
            slot_time=datetime.utcnow(), # in real app parse `slot_time` string into datetime 
            patient_phone=patient_phone,
            status="confirmed"
        )
        session.add(appointment)
        await session.commit()
        await session.refresh(appointment)
        
        appointment_data = {
            "appointment_id": str(appointment.id),
            "tenant_id": tenant_id,
            "clinic_name": clinic_name,
            "doctor_name": doc_name,
            "specialization": specialization,
            "slot_time": slot_time,
            "patient_phone": patient_phone,
            "status": "confirmed"
        }

        # Fire Google Sheets sync dynamically in background to avoid blocking the voice agent
        asyncio.create_task(send_to_sheets_webhook(clinic_webhook, appointment_data))

        return appointment_data
