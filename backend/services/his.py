import logging
import re
from typing import List, Dict, Any, Optional
from sqlalchemy import select
from datetime import datetime, timedelta, timezone
import json

from backend.config import settings
from backend.db import AsyncSessionLocal
from backend.models.doctor import Doctor
from backend.models.appointment import Appointment

logger = logging.getLogger(__name__)

# ── Slot parsing ──────────────────────────────────────────────────────────────
_TIME_FORMATS = ("%I:%M %p", "%I %p", "%H:%M", "%I:%M%p", "%H.%M")
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d %b %Y", "%d %B %Y")


def parse_slot_datetime(date_str: str | None, time_str: str | None) -> datetime:
    """Best-effort parse of a requested appointment slot into a tz-aware datetime.

    Accepts times like "11:00 AM", "2 PM", "14:30" and dates like "2026-07-06",
    "today", "tomorrow". Falls back to the next occurrence of the given time
    (or now) if a field is missing/unparseable — and logs when it does so the
    mis-parse is observable rather than silent.
    """
    now = datetime.now(timezone.utc)

    # ── Date ──
    day: datetime | None = None
    ds = (date_str or "").strip().lower()
    if ds in ("", "today"):
        day = now
    elif ds in ("tomorrow", "tmrw"):
        day = now + timedelta(days=1)
    else:
        for fmt in _DATE_FORMATS:
            try:
                day = datetime.strptime(date_str.strip(), fmt).replace(tzinfo=timezone.utc)
                break
            except (ValueError, AttributeError):
                continue
        if day is None:
            logger.warning("Could not parse appointment date %r; defaulting to today", date_str)
            day = now

    # ── Time ──
    ts = (time_str or "").strip()
    parsed_time = None
    if ts:
        norm = ts.upper().replace(".", ":") if ("AM" in ts.upper() or "PM" in ts.upper()) else ts
        for fmt in _TIME_FORMATS:
            try:
                parsed_time = datetime.strptime(norm.strip(), fmt)
                break
            except ValueError:
                continue
        if parsed_time is None:
            logger.warning("Could not parse appointment time %r; defaulting to now", time_str)

    if parsed_time is None:
        return day.replace(second=0, microsecond=0)

    combined = day.replace(
        hour=parsed_time.hour, minute=parsed_time.minute, second=0, microsecond=0
    )
    # If only a time was given and it already passed today, roll to tomorrow.
    if not ds and combined < now:
        combined += timedelta(days=1)
    return combined


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
    from backend.services.net import is_safe_outbound_url
    if not is_safe_outbound_url(target_url):
        logger.warning("Refusing to POST to unsafe/internal Sheets webhook URL: %s", target_url)
        return

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                target_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=5.0,
                follow_redirects=False
            )
            if response.status_code == 200:
                logger.info(f"Successfully pushed appointment {payload.get('appointment_id')} to Google Sheets.")
            else:
                logger.error(f"Google Sheets webhook failed with status {response.status_code}: {response.text}")
    except Exception as e:
        logger.error(f"Error pushing to Google Sheets: {e}", exc_info=True)


async def create_appointment(
    tenant_id: str,
    doctor_id: str,
    slot_time: str,
    patient_phone: str,
    call_id: str | None = None,
    slot_date: str | None = None,
    patient_name: str | None = None,
) -> dict:
    """Create an appointment row and return its details.

    AWAITED by every path that books (voice pipeline via BookingProcessor, and
    the chat/embed path via execute_booking_action) — a confirmation must never
    be spoken before this returns a real appointment_id (audit FIX 4).

    ``slot_date`` and ``patient_name`` are optional so the original voice caller
    (which passes only slot_time) keeps working unchanged; the chat path passes
    a separate date and the patient's name so appointment rows carry the real
    name instead of a placeholder.
    """
    # Future HIS Integration: POST to /appointments
    # if settings.oxzygen_base_url: ...

    async with AsyncSessionLocal() as session:
        # Idempotency guard: a call can only produce one booking. Retries of
        # the same confirmed call (reconnects, duplicate confirm keywords)
        # must not create a second appointment row.
        if call_id:
            existing_stmt = select(Appointment).where(
                Appointment.tenant_id == tenant_id,
                Appointment.call_id == call_id,
            )
            existing = (await session.execute(existing_stmt)).scalar_one_or_none()
            if existing:
                logger.info(
                    "create_appointment: idempotent hit for call_id=%s — returning existing appointment %s",
                    call_id, existing.id,
                )
                stmt_doc = select(Doctor).where(Doctor.id == existing.doctor_id)
                doc = (await session.execute(stmt_doc)).scalar_one_or_none()
                return {
                    "appointment_id": str(existing.id),
                    "tenant_id": tenant_id,
                    "clinic_name": "",
                    "doctor_name": doc.name if doc else "Unknown",
                    "specialization": doc.specialization if doc else "Specialist",
                    "slot_time": slot_time,
                    "patient_phone": patient_phone,
                    "status": existing.status,
                    "idempotent_hit": True,
                }

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
            slot_time=parse_slot_datetime(slot_date, slot_time),
            patient_phone=patient_phone,
            patient_name=(patient_name.strip() if patient_name and patient_name.strip() else None),
            status="confirmed",
            call_id=call_id,
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

async def sync_appointment_to_db(action: str, name: str, phone: str, date_str: str, time_str: str, doctor_name: str, tenant_id: str, notes: str = None) -> dict | None:
    """
    Intelligently Book, Reschedule, or Cancel an appointment in the local DB.
    `action` is one of: BOOK, RESCHEDULE, CANCEL.
    Returns a dictionary of the updated/created appointment details (id, status, notes) or None on failure.
    Requires matching BOTH name and phone number for CANCEL and RESCHEDULE.
    """
    try:
        async with AsyncSessionLocal() as session:
            # Clean inputs
            name_clean = name.strip()
            phone_clean = phone.strip()
            notes_clean = notes.strip() if notes else None
            if notes_clean and notes_clean.lower() == "n/a":
                notes_clean = None

            if action in ["CANCEL", "RESCHEDULE"]:
                # Match strictly with patient_phone AND patient_name (case insensitive match on name)
                stmt = select(Appointment).where(
                    Appointment.tenant_id == tenant_id,
                    Appointment.patient_phone == phone_clean,
                    Appointment.patient_name.ilike(f"%{name_clean}%"),
                    Appointment.status.in_(["pending", "confirmed"])
                ).order_by(Appointment.slot_time.asc())
                result = await session.execute(stmt)
                appt = result.scalars().first()
                
                if not appt:
                    logger.warning(f"No active appointment found matching phone {phone_clean} and name '{name_clean}' to {action}.")
                    return None
                    
                if action == "CANCEL":
                    appt.status = "cancelled"
                elif action == "RESCHEDULE":
                    appt.slot_time = parse_slot_datetime(date_str, time_str)
                
                if notes_clean:
                    appt.notes = notes_clean
                    
                await session.commit()
                await session.refresh(appt)
                return {
                    "appointment_id": str(appt.id),
                    "status": appt.status,
                    "notes": appt.notes or ""
                }
                
            elif action == "BOOK":
                stmt = select(Doctor).where(Doctor.tenant_id == tenant_id, Doctor.name.ilike(f"%{doctor_name}%"))
                result = await session.execute(stmt)
                doctor = result.scalars().first()

                if not doctor:
                    # Honest refusal: never book against an arbitrary "first"
                    # doctor or a zero-UUID placeholder just because the
                    # requested name didn't match (audit FIX — the old fallback
                    # here is exactly what let the chat path "confirm" an
                    # appointment for a doctor that doesn't exist). Callers must
                    # treat None as "not booked".
                    logger.warning(
                        "BOOK requested for doctor %r with no match in tenant %s — refusing (no fabrication).",
                        doctor_name, tenant_id,
                    )
                    return None

                new_appt = Appointment(
                    tenant_id=tenant_id,
                    doctor_id=doctor.id,
                    slot_time=parse_slot_datetime(date_str, time_str),
                    patient_phone=phone_clean,
                    patient_name=name_clean,
                    status="confirmed",
                    notes=notes_clean
                )
                session.add(new_appt)
                await session.commit()
                await session.refresh(new_appt)
                return {
                    "appointment_id": str(new_appt.id),
                    "status": new_appt.status,
                    "notes": new_appt.notes or ""
                }
    except Exception as e:
        logger.error(f"DB Sync error for {action}: {e}", exc_info=True)
        return None


# ── Unified booking service (shared by voice + chat/embed paths) ───────────────
#
# The chat/embed path previously did its own thing: fire-and-forget DB writes,
# an arbitrary/zero-UUID doctor fallback, and a hardcoded "successfully booked"
# reply that ignored whether the write worked. execute_booking_action() gives
# that path the SAME awaited, doctor-validated, honest booking behaviour the
# voice pipeline already had (audit FIX 4), so a confirmation can only ever be
# reported when a real row exists.

# Doctor-field values that mean "no specific doctor named" rather than a real name.
_NO_DOCTOR_TOKENS = {
    "", "n/a", "na", "none", "null", "-", "any", "anyone", "any doctor",
    "no preference", "not sure", "dont know", "don't know", "whoever",
}


async def find_doctor_for_booking(
    tenant_id: str, doctor_name: str | None
) -> tuple[Optional[Doctor], List[str]]:
    """Resolve a REAL doctor for this tenant by (fuzzy) name/specialization match.

    Returns ``(matched_doctor_or_None, [all doctor display names])``. Unlike the
    old BOOK fallback, this NEVER substitutes an arbitrary doctor or a zero-UUID
    placeholder — an unknown or unspecified name yields ``(None, names)`` so the
    caller can refuse/redirect honestly (audit FIX: no fabricated bookings).
    """
    async with AsyncSessionLocal() as session:
        docs = (
            await session.execute(select(Doctor).where(Doctor.tenant_id == tenant_id))
        ).scalars().all()

    names = [d.name for d in docs if d.name]
    q = (doctor_name or "").strip().lower()
    if q in _NO_DOCTOR_TOKENS:
        return None, names

    # 1. Substring match either direction ("sharma" ~ "Dr. Anjali Sharma").
    for d in docs:
        dn = (d.name or "").lower()
        if dn and (q in dn or dn in q):
            return d, names

    # 2. Significant word overlap (ignore short/filler words).
    q_words = {w for w in re.split(r"\W+", q) if len(w) > 2 and w not in {"doctor"}}
    if q_words:
        for d in docs:
            dn_words = {w for w in re.split(r"\W+", (d.name or "").lower()) if len(w) > 2}
            if q_words & dn_words:
                return d, names
        # 3. Specialization match ("cardiologist" ~ doctor whose spec is Cardiology).
        for d in docs:
            spec_words = {w for w in re.split(r"\W+", (d.specialization or "").lower()) if len(w) > 2}
            if spec_words & q_words:
                return d, names

    return None, names


async def execute_booking_action(
    *,
    action: str,
    tenant_id: str,
    name: str,
    phone: str,
    date_str: str,
    time_str: str,
    doctor_name: str,
    notes: str | None = None,
    call_id: str | None = None,
) -> dict:
    """Perform a Book / Reschedule / Cancel and report the REAL outcome.

    Returns::

        {
          "success": bool,
          "reason": str,                 # "" on success; else why it failed
          "appointment_id": str | None,
          "doctor_name": str | None,
          "available_doctors": list[str],
          "slot": str,
        }

    BOOK routes through create_appointment() — the same idempotent, awaited
    writer the voice pipeline uses — and only after a real doctor is resolved.
    CANCEL/RESCHEDULE go through sync_appointment_to_db(), which returns None
    when no matching appointment exists; that surfaces here as success=False so
    the caller refuses instead of fabricating a confirmation.
    """
    act = (action or "").upper().strip()
    slot = " ".join(
        p.strip() for p in (date_str, time_str)
        if p and p.strip() and p.strip().lower() != "n/a"
    ).strip()
    base = {
        "success": False, "reason": "unknown_action", "appointment_id": None,
        "doctor_name": None, "available_doctors": [], "slot": slot,
    }

    if act == "BOOK":
        doctor, available = await find_doctor_for_booking(tenant_id, doctor_name)
        base["available_doctors"] = available
        if not doctor:
            named = (doctor_name or "").strip().lower() not in _NO_DOCTOR_TOKENS
            base["reason"] = "doctor_not_found" if named else "doctor_required"
            return base
        try:
            result = await create_appointment(
                tenant_id=tenant_id,
                doctor_id=str(doctor.id),
                slot_time=time_str,
                slot_date=date_str,
                patient_phone=phone,
                patient_name=name,
                call_id=call_id,
            )
        except Exception as e:
            logger.error("execute_booking_action BOOK failed: %s", e, exc_info=True)
            result = None
        if not result or not result.get("appointment_id"):
            base["reason"] = "db_error"
            base["doctor_name"] = doctor.name
            return base
        return {
            "success": True, "reason": "", "appointment_id": result["appointment_id"],
            "doctor_name": result.get("doctor_name") or doctor.name,
            "available_doctors": available, "slot": slot,
        }

    if act in ("CANCEL", "RESCHEDULE"):
        try:
            result = await sync_appointment_to_db(
                action=act, name=name, phone=phone, date_str=date_str,
                time_str=time_str, doctor_name=doctor_name, tenant_id=tenant_id,
                notes=notes,
            )
        except Exception as e:
            logger.error("execute_booking_action %s failed: %s", act, e, exc_info=True)
            result = None
        if not result or not result.get("appointment_id"):
            base["reason"] = "not_found"
            return base
        return {
            "success": True, "reason": "", "appointment_id": result["appointment_id"],
            "doctor_name": None, "available_doctors": [], "slot": slot,
        }

    return base
