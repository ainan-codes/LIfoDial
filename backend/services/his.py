import logging
import re
from typing import List, Dict, Any, Optional
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from datetime import datetime, time as dt_time, timedelta, timezone
import json

from backend.config import settings
from backend.db import AsyncSessionLocal
from backend.models.doctor import Doctor
from backend.models.appointment import Appointment
from backend.services.timeutil import IST, ist_now

logger = logging.getLogger(__name__)

# ── Slot parsing ──────────────────────────────────────────────────────────────
_TIME_FORMATS = ("%I:%M %p", "%I %p", "%H:%M", "%I:%M%p", "%H.%M")

#: Date parsing (formats AND every language's relative-day words) lives in
#: services/dayref.py — see that module for why a model must never be the thing
#: that computes what "tomorrow" is. Re-exported under the old name because
#: availability_prompt.py imports `_DATE_FORMATS` from here.
from backend.services.dayref import DATE_FORMATS as _DATE_FORMATS  # noqa: E402
from backend.services.dayref import parse_day_string  # noqa: E402


#: Words that mark a bare hour as a clock time rather than a quantity. "baje"
#: is the one that mattered: booking_processor's own slot regex has always
#: ACCEPTED "11 baje", and this parser has always REJECTED it — so a caller
#: saying the time the way most of India says it had their slot extracted and
#: then thrown away ("Could not parse appointment time '11 baje'; defaulting to
#: now", followed by the availability gate refusing the fabricated time).
#: indic_text.normalise_spoken_numbers rewrites every language's o'clock word to
#: "baje" before the slot regex runs, so this one spelling covers all of them.
_OCLOCK_MARKERS = ("baje", "bajey", "baja", "o'clock", "oclock", "hrs", "hours")

#: A bare hour has no AM/PM, and the 12-hour ambiguity has to be resolved
#: somehow. Clinics in this product run 9 AM - 7 PM, so 1-7 means the afternoon
#: and 8-12 means the morning. This is a reading of what the caller said, not an
#: invention of a time they did not say: the agent repeats it back for
#: confirmation, and availability.is_doctor_open_at still refuses anything
#: outside the doctor's real schedule.
_BARE_HOUR_PM_UNTIL = 7


def _try_parse_time(time_str: str | None) -> datetime | None:
    """Parse time_str against _TIME_FORMATS, or None if empty/unparseable.

    Factored out of parse_slot_datetime so is_time_str_parseable() below can
    ask "would this actually parse" without duplicating the format list.
    """
    ts = (time_str or "").strip()
    if not ts:
        return None
    norm = ts.upper().replace(".", ":") if ("AM" in ts.upper() or "PM" in ts.upper()) else ts
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(norm.strip(), fmt)
        except ValueError:
            continue

    # A bare hour, with or without an o'clock word: "11 baje", "11 o'clock", "3".
    bare = ts.lower()
    # Longest first, or "baje" strips the front of "bajey" and leaves a stray "y".
    for marker in sorted(_OCLOCK_MARKERS, key=len, reverse=True):
        bare = bare.replace(marker, " ")
    bare = bare.strip().strip(".").strip()
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", bare)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        if 1 <= hour <= _BARE_HOUR_PM_UNTIL:
            hour += 12
        return datetime.strptime(f"{hour:02d}:{minute:02d}", "%H:%M")
    return None


def is_time_str_parseable(time_str: str | None) -> bool:
    """True if time_str carries a REAL caller-given time — i.e.
    parse_slot_datetime(date_str, time_str) would use it, not silently fall
    back to a default (now/midnight).

    parse_slot_datetime's fallback-on-unparseable behavior is correct for
    contexts where "just pick something reasonable" is fine (its docstring:
    "Falls back... if a field is missing/unparseable"), but a caller that
    needs a REAL requested time — never a fabricated one — must check this
    FIRST and refuse instead of booking whatever the fallback produces. See
    booking_processor.py's own "no fabricated slot" rule for the voice
    equivalent of this same principle, enforced there by construction
    (BookingProcessor only ever sets a pending time from a real regex match).
    """
    return _try_parse_time(time_str) is not None


def is_date_str_parseable(date_str: str | None) -> bool:
    """True if ``date_str`` names a REAL day — i.e. parse_slot_datetime will use
    it rather than silently falling back to today.

    An EMPTY string is fine (it means "the day was not specified", which the
    caller may legitimately treat as today). A non-empty string that resolves to
    nothing is not: the fallback would book today, and "today" is a day the
    patient never asked for. This is the date half of is_time_str_parseable, and
    it exists because a model writes things like "kal ke baad" or "next week"
    into the tag's Date field, none of which name a computable day.
    """
    ds = (date_str or "").strip()
    if not ds:
        return True
    return parse_day_string(ds, ist_now().date()) is not None


def parse_slot_datetime(date_str: str | None, time_str: str | None) -> datetime:
    """Best-effort parse of a requested appointment slot into a tz-aware UTC datetime.

    Accepts times like "11:00 AM", "2 PM", "14:30" and dates like "2026-07-06",
    "today", "tomorrow". Falls back to the next occurrence of the given time
    (or now) if a field is missing/unparseable — and logs when it does so the
    mis-parse is observable rather than silent.

    Every clinic is IST (confirmed with the user — no per-tenant timezone
    field exists). The caller's day/time strings are wall-clock IST, so
    parsing is rooted in ist_now() and every intermediate value carries
    tzinfo=IST; the single .astimezone(timezone.utc) conversion happens only
    at the return statements. (Previously this rooted parsing in
    datetime.now(timezone.utc) directly — i.e. treated IST wall-clock as if
    it already were UTC, mislabeling every stored slot by ~5.5 hours.)
    """
    now = ist_now()

    # ── Date ──
    # Resolved by services/dayref.py, which knows the relative-day words of every
    # language this product speaks ("कल", "നാളെ", "ನಾಳೆ", …) as well as weekday
    # names and explicit formats. It used to understand exactly "today",
    # "tomorrow" and "tmrw", so a caller's own word in any other language fell
    # through to "defaulting to today" — and a booking landed on the wrong day
    # while every log line looked healthy.
    day: datetime | None = None
    ds = (date_str or "").strip()
    if not ds:
        day = now
    else:
        resolved = parse_day_string(ds, now.date())
        if resolved is not None:
            day = datetime.combine(resolved, dt_time(0, 0)).replace(tzinfo=IST)
        else:
            logger.warning("Could not parse appointment date %r; defaulting to today", date_str)
            day = now

    # ── Time ──
    parsed_time = _try_parse_time(time_str)
    if parsed_time is None and (time_str or "").strip():
        logger.warning("Could not parse appointment time %r; defaulting to now", time_str)

    if parsed_time is None:
        return day.replace(second=0, microsecond=0).astimezone(timezone.utc)

    combined = day.replace(
        hour=parsed_time.hour, minute=parsed_time.minute, second=0, microsecond=0
    )
    # If only a time was given and it already passed today, roll to tomorrow.
    if not ds and combined < now:
        combined += timedelta(days=1)
    return combined.astimezone(timezone.utc)


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


def invalidate_doctor_cache(tenant_id: str) -> None:
    """Drop the cached doctor list for a tenant. Call this on any doctor
    add/delete/availability change — without it, a clinic marking a doctor on
    leave (or adding/removing one) would silently keep showing the stale list
    to callers and the booking flow for up to _CACHE_TTL (1 hour)."""
    _doctor_cache.pop(f"{tenant_id}:doctors:list", None)


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
                    "his_doctor_id": d.his_doctor_id,
                    "is_available": d.is_available,
                    "leave_reason": d.leave_reason,
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


async def get_slots(doctor_id: str, date: str = None, tenant_id: str | None = None) -> List[str]:
    """Real bookable 30-min slots for doctor_id on `date` (YYYY-MM-DD, IST
    calendar day; defaults to today), formatted as IST display strings.

    Never cache slots! Legacy-compatible wrapper — the FSM
    (booking_processor.py) and the admin "available-slots" endpoint call
    availability.compute_available_slots directly for the UTC instants;
    this exists for any older/simpler caller that just wants display
    strings. `tenant_id` is an optional trailing kwarg (added last) so no
    existing caller that only passed doctor_id/date breaks.
    """
    if not tenant_id:
        logger.warning("get_slots called without tenant_id — cannot compute real availability, returning [].")
        return []

    from backend.services.availability import compute_available_slots

    if date:
        try:
            target_date = datetime.strptime(date.strip(), "%Y-%m-%d").date()
        except ValueError:
            target_date = ist_now().date()
    else:
        target_date = ist_now().date()

    slots_utc = await compute_available_slots(tenant_id, doctor_id, target_date)
    from backend.services.timeutil import format_ist_clock, to_ist
    return [format_ist_clock(to_ist(s)) for s in slots_utc]


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
    from backend.services.net import is_safe_outbound_url, post_json_with_safe_redirects
    if not is_safe_outbound_url(target_url):
        logger.warning("Refusing to POST to unsafe/internal Sheets webhook URL: %s", target_url)
        return

    try:
        # Google Apps Script /exec always 302s to script.googleusercontent.com,
        # so refusing redirects outright meant this never actually reached the
        # sheet. Redirects are followed but re-validated per hop, because the
        # webhook URL is tenant-controlled (see net.py).
        response = await post_json_with_safe_redirects(target_url, payload, timeout=5.0)
        if response.status_code == 200:
            logger.info(f"Successfully pushed appointment {payload.get('appointment_id')} to Google Sheets.")
        else:
            logger.error(f"Google Sheets webhook failed with status {response.status_code}: {response.text}")
    except Exception as e:
        logger.error(f"Error pushing to Google Sheets: {e}", exc_info=True)


def normalize_source(source: str | None) -> str | None:
    """Validate a booking-channel value, or None.

    An unknown value is stored as NULL and logged, so a channel added without
    teaching the dashboards about it reads as "Unknown" instead of being
    silently mis-attributed. See backend/models/appointment.py for the
    vocabulary and why the column is nullable.
    """
    from backend.models.appointment import VALID_SOURCES

    s = (source or "").strip().lower()
    if not s:
        return None
    if s not in VALID_SOURCES:
        logger.warning(
            "Unknown booking source %r — storing NULL rather than mis-attributing the row. "
            "Add it to backend/models/appointment.py::VALID_SOURCES and to both dashboards.",
            source,
        )
        return None
    return s


async def create_appointment(
    tenant_id: str,
    doctor_id: str,
    slot_time: str,
    patient_phone: str,
    call_id: str | None = None,
    slot_date: str | None = None,
    patient_name: str | None = None,
    source: str | None = None,
) -> dict:
    """Create an appointment row and return its details.

    AWAITED by every path that books (voice pipeline via BookingProcessor, and
    the chat/embed path via execute_booking_action) — a confirmation must never
    be spoken before this returns a real appointment_id (audit FIX 4).

    ``slot_date`` and ``patient_name`` are optional so the original voice caller
    (which passes only slot_time) keeps working unchanged; the chat path passes
    a separate date and the patient's name so appointment rows carry the real
    name instead of a placeholder.

    ``source`` records WHICH channel booked it (voice / web_voice / chat /
    embed / dashboard) — the clinic's Appointments view shows it, and before this
    existed both dashboards claimed every row came from a phone call.
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
                    "source": existing.source,
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
            source=normalize_source(source),
        )
        session.add(appointment)
        try:
            await session.commit()
        except IntegrityError:
            # Race-safety backstop: uq_appointments_doctor_slot_active caught a
            # concurrent booking for this exact doctor+slot that won the race.
            # is_doctor_open_at already checks before this is ever reached, so
            # this only fires for a genuinely simultaneous second caller.
            await session.rollback()
            logger.warning(
                "create_appointment: slot conflict doctor=%s slot=%s", doctor_id, appointment.slot_time,
            )
            return {"appointment_id": None, "reason": "slot_taken"}
        await session.refresh(appointment)

        appointment_data = {
            "appointment_id": str(appointment.id),
            "tenant_id": tenant_id,
            "clinic_name": clinic_name,
            "doctor_name": doc_name,
            "specialization": specialization,
            "slot_time": slot_time,
            "patient_phone": patient_phone,
            "status": "confirmed",
            "source": appointment.source,
        }

        # Fire Google Sheets sync dynamically in background to avoid blocking the voice agent
        asyncio.create_task(send_to_sheets_webhook(clinic_webhook, appointment_data))

        return appointment_data

def normalize_phone(phone: str | None) -> str:
    """Digits only, with an Indian country code dropped, for MATCHING purposes.

    The stored value is whatever the patient gave when booking, so "+91 98450
    12345", "098450-12345" and "9845012345" are the same person but three
    different strings — and CANCEL/RESCHEDULE matched patient_phone EXACTLY,
    so any difference in punctuation or a +91 prefix made an existing
    appointment unfindable and the agent honestly (but wrongly) reported "no
    appointment found".

    Only formatting is normalized: a genuinely different number still does
    not match, which is the behaviour we want — see the reschedule flow, which
    must never touch another patient's appointment.
    """
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) > 10 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits


def names_refer_to_same_person(stored: str | None, spoken: str | None) -> bool:
    """Could ``spoken`` be the person whose appointment is stored as ``stored``?

    Script-independent, because a name is written down once and then said out
    loud many times, in whatever script the STT is running in. This is the exact
    failure that made cancellation impossible on a live call (2026-08-12): the
    row said ``आइनान``, the caller's next call transcribed as ``ऐनान`` and
    ``आइनन``, and a SQL ``ilike '%ऐनान%'`` can never match any of those — nor
    could it ever match the same name written ``Ainan``, which is how a
    transliterating model stores it. The agent therefore reported "no appointment
    found" and then asked the caller to spell their own name, four times.

    Uses the same consonant-skeleton comparison doctor_match uses for doctors —
    one matching implementation for names in this product, not two.
    """
    from backend.services.indic_text import consonant_skeleton, skeleton_contains

    a = (stored or "").strip().lower()
    b = (spoken or "").strip().lower()
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    # Either direction: the caller may give one part of a stored full name, or a
    # fuller version of a stored first name.
    if skeleton_contains(a, b) or skeleton_contains(b, a):
        return True
    sa, sb = consonant_skeleton(a), consonant_skeleton(b)
    return bool(sa) and bool(sb) and (sa == sb or sa in sb or sb in sa)


async def active_appointments_for_phone(session, tenant_id: str, phone: str) -> list:
    """Every active appointment on this tenant for ``phone``, earliest first.

    The phone is the strong identifier on a voice call — it is the number the
    caller is calling FROM — so this is the set a cancel/reschedule is chosen
    from, and it is also what the agent is shown so it can stop asking the
    caller for details the database already holds.
    """
    wanted = normalize_phone(phone)
    if not wanted:
        return []
    rows = (
        await session.execute(
            select(Appointment).where(
                Appointment.tenant_id == tenant_id,
                Appointment.status.in_(["pending", "confirmed"]),
            ).order_by(Appointment.slot_time.asc())
        )
    ).scalars().all()
    return [a for a in rows if normalize_phone(a.patient_phone) == wanted]


async def _find_active_appointment_in_session(session, tenant_id: str, name: str, phone: str):
    """The single lookup that identifies WHICH appointment a cancel/reschedule
    refers to.

    Shared so the pre-commit availability check and the write itself always
    resolve the SAME row. Two lookups with different rules would let the agent
    validate one appointment's new slot and then move another.

    Phone FIRST, then name — the reverse of the original order, and the change
    that makes cancellation work at all:

      * one active appointment on that number  -> that is the one, however the
        name was spelled or transcribed. The number is the caller's own; a
        spelling variant of their name is not evidence of a different person.
      * several on that number                 -> pick by name; failing that, the
        earliest, because they are all the same person's appointments.
      * nothing on that number (or none given) -> fall back to matching the name
        across the tenant, script-independently.

    A row is never returned unless the phone or the name identifies it, so one
    patient's appointment still cannot be cancelled by another.
    """
    name_clean = (name or "").strip()
    phone_clean = (phone or "").strip()

    by_phone = await active_appointments_for_phone(session, tenant_id, phone_clean)
    if by_phone:
        if len(by_phone) == 1:
            return by_phone[0]
        if name_clean:
            for appt in by_phone:
                if names_refer_to_same_person(appt.patient_name, name_clean):
                    return appt
        return by_phone[0]

    if not name_clean:
        return None

    rows = (
        await session.execute(
            select(Appointment).where(
                Appointment.tenant_id == tenant_id,
                Appointment.status.in_(["pending", "confirmed"]),
            ).order_by(Appointment.slot_time.asc())
        )
    ).scalars().all()
    wanted = normalize_phone(phone_clean)
    for appt in rows:
        if not names_refer_to_same_person(appt.patient_name, name_clean):
            continue
        stored = normalize_phone(appt.patient_phone)
        if wanted and stored and stored != wanted:
            # A DIFFERENT identifiable person who happens to share a name. This
            # is the case the original both-must-match rule existed to prevent,
            # and it still holds.
            continue
        # Reachable when the row carries no usable number (booked from a call
        # with no caller ID, so patient_phone is "unknown") or when the caller
        # gave no number at all. The name is then the only identity either side
        # has, which is exactly the situation a clinic receptionist works in.
        return appt
    return None


async def caller_appointments(tenant_id: str, phone: str, name: str = "") -> List[dict]:
    """This caller's active appointments, as plain dicts (doctor name + IST slot).

    Feeds the ``[CALLER'S APPOINTMENTS]`` prompt block. Matched the same way a
    cancel/reschedule is matched, so what the agent is SHOWN and what the write
    RESOLVES can never disagree — an agent reading out an appointment the write
    then cannot find is worse than showing nothing.
    """
    from backend.services.timeutil import format_ist_clock, to_ist

    out: List[dict] = []
    try:
        async with AsyncSessionLocal() as session:
            rows = await active_appointments_for_phone(session, tenant_id, phone)
            if not rows and (name or "").strip():
                appt = await _find_active_appointment_in_session(
                    session, tenant_id, name, phone,
                )
                rows = [appt] if appt else []
            if not rows:
                return []

            doctors = {
                str(d.id): d for d in (
                    await session.execute(
                        select(Doctor).where(Doctor.tenant_id == tenant_id)
                    )
                ).scalars().all()
            }
            for a in rows:
                slot_ist = to_ist(_as_utc(a.slot_time))
                doc = doctors.get(str(a.doctor_id))
                out.append({
                    "appointment_id": str(a.id),
                    "patient_name": a.patient_name or "",
                    "doctor_name": doc.name if doc else "Unknown",
                    "date": slot_ist.strftime("%d/%m/%Y"),
                    "day": slot_ist.strftime("%A"),
                    "time": format_ist_clock(slot_ist),
                    "status": a.status,
                })
    except Exception as exc:
        logger.error("caller_appointments lookup failed: %s", exc, exc_info=True)
        return []
    return out


async def find_active_appointment(tenant_id: str, name: str, phone: str) -> dict | None:
    """``_find_active_appointment_in_session`` for callers outside a session.

    Returns a plain dict (not the ORM object, which would be detached once the
    session closes) with the fields a pre-commit availability check needs.
    """
    async with AsyncSessionLocal() as session:
        appt = await _find_active_appointment_in_session(session, tenant_id, name, phone)
        if not appt:
            return None
        doctor = (
            await session.execute(select(Doctor).where(Doctor.id == appt.doctor_id))
        ).scalar_one_or_none()
        return {
            "appointment_id": str(appt.id),
            "doctor_id": str(appt.doctor_id),
            "doctor_name": doctor.name if doctor else None,
            "slot_time": appt.slot_time,
            "status": appt.status,
        }


async def sync_appointment_to_db(action: str, name: str, phone: str, date_str: str, time_str: str, doctor_name: str, tenant_id: str, notes: str = None, new_doctor_id: str | None = None, source: str | None = None) -> dict | None:
    """
    Intelligently Book, Reschedule, or Cancel an appointment in the local DB.
    `action` is one of: BOOK, RESCHEDULE, CANCEL.
    Returns a dictionary of the updated/created appointment details (id, status, notes) or None on failure.
    Requires matching BOTH name and phone number for CANCEL and RESCHEDULE.

    ``new_doctor_id`` moves a RESCHEDULE to a different doctor. It exists so
    the availability check in execute_booking_action and this write can never
    disagree about which doctor's calendar the new slot belongs to: if the
    caller named a different doctor, the slot was validated against THAT
    doctor, so the row has to move there too.

    ``source`` is the booking channel, recorded on a BOOK only. A
    CANCEL/RESCHEDULE deliberately leaves it alone: the column answers "how was
    this appointment booked", so rewriting it on a later edit would erase that.
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
                appt = await _find_active_appointment_in_session(
                    session, tenant_id, name_clean, phone_clean,
                )

                if not appt:
                    logger.warning(f"No active appointment found matching phone {phone_clean} and name '{name_clean}' to {action}.")
                    return None

                if action == "CANCEL":
                    appt.status = "cancelled"
                elif action == "RESCHEDULE":
                    appt.slot_time = parse_slot_datetime(date_str, time_str)
                    if new_doctor_id and str(new_doctor_id) != str(appt.doctor_id):
                        appt.doctor_id = new_doctor_id

                if notes_clean:
                    appt.notes = notes_clean

                try:
                    await session.commit()
                except IntegrityError:
                    # Only reachable for RESCHEDULE (CANCEL never changes
                    # slot_time) — the new slot collided with another active
                    # booking for the same doctor.
                    await session.rollback()
                    logger.warning(
                        "sync_appointment_to_db: %s slot conflict appt=%s new_slot=%s",
                        action, appt.id, appt.slot_time,
                    )
                    return {"appointment_id": None, "reason": "slot_taken"}
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
                    notes=notes_clean,
                    source=normalize_source(source),
                )
                session.add(new_appt)
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    logger.warning(
                        "sync_appointment_to_db: BOOK slot conflict doctor=%s slot=%s",
                        doctor.id, new_appt.slot_time,
                    )
                    return {"appointment_id": None, "reason": "slot_taken"}
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

#: Field values that mean "nothing was given here". The model fills unused tag
#: fields with these because the tag instructions tell it to.
_NO_VALUE_TOKENS = {"", "n/a", "na", "n.a.", "none", "null", "-", "--", "unknown"}


def _is_no_value(value: str | None) -> bool:
    return (value or "").strip().lower() in _NO_VALUE_TOKENS


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

    # Matching itself lives in services/doctor_match.py — ONE implementation,
    # shared with the voice booking FSM, and script-independent so a name the
    # LLM wrote in Devanagari/Malayalam/Kannada still resolves against a roster
    # stored in Latin. This function keeps only the DB read and the
    # "(doctor, all names)" contract its callers depend on.
    from backend.services.doctor_match import match_doctor_name

    by_id = {str(d.id): d for d in docs}
    matched = match_doctor_name(q, [
        {
            "id": str(d.id),
            "name": d.name,
            "specialization": d.specialization,
            "is_available": d.is_available,
        }
        for d in docs
    ])
    if matched is None:
        return None, names
    return by_id.get(str(matched["id"])), names


async def alternative_slots_for(
    tenant_id: str, doctor_id: str, date_str: str, time_str: str, limit: int = 4,
) -> List[str]:
    """Real open times (IST display strings) for the doctor on the day the
    caller asked about — used to answer a "that slot is taken" with actual
    alternatives instead of a dead end. Returns [] if the doctor has no
    schedule configured or nothing is left that day."""
    try:
        from backend.services.availability import compute_available_slots
        from backend.services.timeutil import format_ist_clock, to_ist

        requested = parse_slot_datetime(date_str, time_str)
        slots = await compute_available_slots(tenant_id, doctor_id, to_ist(requested).date())
        return [format_ist_clock(to_ist(s)) for s in slots[:limit]]
    except Exception as e:
        logger.error("Could not compute alternative slots: %s", e, exc_info=True)
        return []


def _as_utc(dt: datetime) -> datetime:
    """Label a naive DB-read datetime as UTC (SQLite loses tzinfo; Postgres
    does not). Every slot_time this app writes is a real UTC instant, so a
    naive value read back is always safe to label rather than reinterpret —
    same rule as availability._ensure_utc."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# is_doctor_open_at's reason vocabulary → the outcome reason callers render a
# message from. Kept as an explicit mapping so a new engine reason surfaces as
# an obvious KeyError-free fallback instead of being silently reported as a
# generic system error.
_AVAILABILITY_REASONS = {
    "unparseable_time": "invalid_time",
    "doctor_not_found": "doctor_not_found",
    "doctor_unavailable": "doctor_unavailable",
    "no_schedule_configured": "no_schedule",
    "outside_hours": "outside_hours",
    "in_the_past": "slot_in_past",
    "slot_taken": "slot_taken",
}

# Failures worth answering with the doctor's REAL remaining times rather than a
# flat "no" — the patient can act on those immediately.
_REASONS_WITH_ALTERNATIVES = frozenset({"slot_taken", "outside_hours", "slot_in_past", "slot_unavailable"})


async def _availability_gate(
    tenant_id: str,
    doctor_id: str,
    doctor_name: str | None,
    date_str: str,
    time_str: str,
) -> dict | None:
    """``None`` when the requested slot really is open; otherwise the failure
    fields to merge into the result dict.

    This is the one place BOOK and RESCHEDULE share their "is this slot
    actually available" answer, so the two flows cannot drift apart again —
    the reschedule branch had no such check at all, which is how the agent
    came to confirm a time it had never verified.
    """
    from backend.services.availability import is_doctor_open_at

    requested = parse_slot_datetime(date_str, time_str)
    is_open, why = await is_doctor_open_at(tenant_id, str(doctor_id), requested)
    if is_open:
        return None

    out = {
        "reason": _AVAILABILITY_REASONS.get(why, "slot_unavailable"),
        "doctor_name": doctor_name,
        "availability_reason": why,
    }
    if out["reason"] in _REASONS_WITH_ALTERNATIVES:
        out["alternatives"] = await alternative_slots_for(
            tenant_id, str(doctor_id), date_str, time_str,
        )
    logger.info(
        "Availability gate refused %s for doctor=%s at %s (engine reason=%s)",
        out["reason"], doctor_id, requested, why,
    )
    return out


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
    source: str | None = None,
) -> dict:
    """Perform a Book / Reschedule / Cancel and report the REAL outcome.

    Returns::

        {
          "success": bool,
          "reason": str,                 # "" on success; else why it failed
          "appointment_id": str | None,
          "doctor_name": str | None,
          "available_doctors": list[str],
          "alternatives": list[str],     # real open times, on a slot failure
          "slot": str,
        }

    ``reason`` is one of: doctor_not_found, doctor_required, doctor_unavailable,
    no_schedule, invalid_time, slot_unavailable, slot_taken,
    already_at_that_time, not_found, db_error, unknown_action. A slot failure
    also carries ``availability_reason`` (the availability engine's own,
    finer-grained reason) for logging.

    BOOK routes through create_appointment() — the same idempotent, awaited
    writer the voice pipeline uses — and only after a real doctor is resolved.
    CANCEL/RESCHEDULE go through sync_appointment_to_db(), which returns None
    when no matching appointment exists; that surfaces here as success=False so
    the caller refuses instead of fabricating a confirmation.

    BOTH BOOK and RESCHEDULE are gated on availability.is_doctor_open_at — the
    same engine, checked immediately before the write — so no slot can be
    confirmed to a patient that the doctor's real schedule and existing
    bookings do not actually have open. RESCHEDULE previously had NO
    availability check of any kind: it wrote whatever time it was handed.
    """
    act = (action or "").upper().strip()

    # "N/A" (and friends) in the Date field means "no day was given", not a day
    # named "N/A". Normalising it here rather than at each call site is what lets
    # the two branches below give it their own, correct meaning: a BOOK with no day
    # is refused, a RESCHEDULE with no day keeps the day the appointment is on.
    if _is_no_value(date_str):
        date_str = ""

    slot = " ".join(
        p.strip() for p in (date_str, time_str)
        if p and p.strip() and p.strip().lower() != "n/a"
    ).strip()
    base = {
        "success": False, "reason": "unknown_action", "appointment_id": None,
        "doctor_name": None, "available_doctors": [], "alternatives": [], "slot": slot,
    }

    if act == "BOOK":
        # A booking with no day would land on TODAY — a day the patient never
        # asked for, which is the same class of invention as a fabricated time.
        if not date_str.strip():
            base["reason"] = "invalid_date"
            return base

        doctor, available = await find_doctor_for_booking(tenant_id, doctor_name)
        base["available_doctors"] = available
        if not doctor:
            named = (doctor_name or "").strip().lower() not in _NO_DOCTOR_TOKENS
            base["reason"] = "doctor_not_found" if named else "doctor_required"
            return base

        base["doctor_name"] = doctor.name
        gate = await _availability_gate(
            tenant_id, str(doctor.id), doctor.name, date_str, time_str,
        )
        if gate is not None:
            base.update(gate)
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
                source=source,
            )
        except Exception as e:
            logger.error("execute_booking_action BOOK failed: %s", e, exc_info=True)
            result = None
        if not result or not result.get("appointment_id"):
            base["reason"] = (result or {}).get("reason") or "db_error"
            base["doctor_name"] = doctor.name
            if base["reason"] == "slot_taken":
                # A conflict is the one failure where we can be genuinely
                # useful: say what IS free rather than just "no".
                base["alternatives"] = await alternative_slots_for(
                    tenant_id, str(doctor.id), date_str, time_str,
                )
            return base
        return {
            "success": True, "reason": "", "appointment_id": result["appointment_id"],
            "doctor_name": result.get("doctor_name") or doctor.name,
            "available_doctors": available, "slot": slot,
        }

    if act in ("CANCEL", "RESCHEDULE"):
        new_doctor_id: str | None = None
        target_doctor_id: str | None = None

        if act == "RESCHEDULE":
            # Find the appointment FIRST. Its doctor is whose calendar the new
            # slot has to be free on — the tag's Doctor field is often "N/A" on
            # a reschedule, and trusting it would mean checking one doctor's
            # availability and writing to another's.
            existing = await find_active_appointment(tenant_id, name, phone)
            if not existing:
                base["reason"] = "not_found"
                return base

            target_doctor_id = existing["doctor_id"]
            target_doctor_name = existing["doctor_name"]

            # "Move it to 4 PM" means 4 PM on the day the appointment is ALREADY
            # on — not today, which is what an empty date resolves to. Without
            # this, a patient with a booking on the 15th asking for 4 PM had it
            # silently moved to this afternoon (and then usually refused as
            # already past, which reads to them as "4 PM isn't available").
            if not date_str.strip() and existing.get("slot_time") is not None:
                from backend.services.timeutil import to_ist

                date_str = to_ist(_as_utc(existing["slot_time"])).strftime("%d/%m/%Y")
                logger.info(
                    "RESCHEDULE with no day given — keeping the appointment's own day (%s).",
                    date_str,
                )
            named_doctor, available = await find_doctor_for_booking(tenant_id, doctor_name)
            base["available_doctors"] = available
            if named_doctor and str(named_doctor.id) != str(target_doctor_id):
                # The patient asked to move to a DIFFERENT doctor. Validate and
                # write against that doctor, or the confirmation would silently
                # keep the old one.
                target_doctor_id = str(named_doctor.id)
                target_doctor_name = named_doctor.name
                new_doctor_id = str(named_doctor.id)

            base["doctor_name"] = target_doctor_name

            # A reschedule with no real time would silently move the
            # appointment to midnight (parse_slot_datetime's documented
            # fallback) — refuse, exactly as the BOOK path does.
            if not is_time_str_parseable(time_str):
                base["reason"] = "invalid_time"
                return base

            from backend.services.availability import floor_to_slot

            requested_utc = parse_slot_datetime(date_str, time_str)

            current = existing.get("slot_time")
            if (
                new_doctor_id is None
                and current is not None
                and floor_to_slot(_as_utc(current)) == floor_to_slot(requested_utc)
            ):
                # The requested end state already holds, so this is a success
                # (nothing is broken, nothing was lost) — but it is NOT a
                # change, and the reason keeps callers from saying "rescheduled"
                # or writing a Sheets row for a move that never happened.
                # Without this the availability check below would report the
                # patient's OWN booking as "that slot is taken".
                base["success"] = True
                base["reason"] = "already_at_that_time"
                base["appointment_id"] = existing["appointment_id"]
                return base

            gate = await _availability_gate(
                tenant_id, target_doctor_id, target_doctor_name, date_str, time_str,
            )
            if gate is not None:
                base.update(gate)
                return base

        try:
            result = await sync_appointment_to_db(
                action=act, name=name, phone=phone, date_str=date_str,
                time_str=time_str, doctor_name=doctor_name, tenant_id=tenant_id,
                notes=notes, new_doctor_id=new_doctor_id, source=source,
            )
        except Exception as e:
            logger.error("execute_booking_action %s failed: %s", act, e, exc_info=True)
            result = None
        if not result or not result.get("appointment_id"):
            base["reason"] = (result or {}).get("reason") or "not_found"
            if base["reason"] == "slot_taken" and target_doctor_id:
                # Lost a genuine race between the check above and this write.
                base["alternatives"] = await alternative_slots_for(
                    tenant_id, target_doctor_id, date_str, time_str,
                )
            return base
        return {
            "success": True, "reason": "", "appointment_id": result["appointment_id"],
            "doctor_name": base.get("doctor_name"), "available_doctors": [],
            "alternatives": [], "slot": slot,
        }

    return base
