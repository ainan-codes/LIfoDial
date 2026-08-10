"""backend/services/availability.py

Real, Supabase-backed doctor availability — replaces the previously
hardcoded/mocked his.get_slots() and the previously-nonexistent
"is this slot actually open" check that let the voice agent offer or
confirm times with no relationship to a doctor's real schedule or existing
bookings.

Two entry points:
  - compute_available_slots: the real open 30-min slots for a doctor on a
    given IST calendar date (schedule windows minus already-booked slots).
  - is_doctor_open_at: a single yes/no + reason check for one requested
    instant, used by BookingProcessor before arming a confirmation and again
    immediately before committing the booking.
"""
import logging
from datetime import date as date_cls, datetime, time as time_cls, timedelta, timezone

from sqlalchemy import select

from backend.db import AsyncSessionLocal
from backend.models.appointment import Appointment
from backend.models.doctor import Doctor
from backend.models.doctor_availability import DoctorAvailability
from backend.services.timeutil import ist_wall_clock_to_utc, to_ist

logger = logging.getLogger(__name__)

SLOT_MINUTES = 30


def _ensure_utc(dt: datetime) -> datetime:
    """Normalize a DB-read datetime to tz-aware UTC.

    SQLite (dev/tests) does not actually preserve tzinfo through
    DateTime(timezone=True) round-trips — it comes back naive — whereas
    Postgres/asyncpg returns a proper tz-aware UTC value. Every value this
    app ever writes to slot_time is already a true UTC instant (via
    parse_slot_datetime / ist_wall_clock_to_utc), so a naive value read back
    is always safe to label UTC rather than re-interpret."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def compute_available_slots(
    tenant_id: str, doctor_id: str, target_date: date_cls,
) -> list[datetime]:
    """Real bookable 30-min slot starts (UTC instants, sorted) for doctor_id
    on the IST calendar date target_date.

    Silence in the schedule is treated as "not bookable that day", not
    "always open" — a doctor with no configured windows for that day of week
    returns []. An on-leave doctor (is_available=False) always returns [],
    regardless of any configured schedule.
    """
    async with AsyncSessionLocal() as session:
        doctor = (
            await session.execute(
                select(Doctor).where(Doctor.id == doctor_id, Doctor.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if not doctor or not doctor.is_available:
            return []

        day_of_week = target_date.weekday()
        windows = (
            await session.execute(
                select(DoctorAvailability).where(
                    DoctorAvailability.doctor_id == doctor_id,
                    DoctorAvailability.day_of_week == day_of_week,
                )
            )
        ).scalars().all()
        if not windows:
            return []

        candidates: list[datetime] = []
        for w in windows:
            slot_start = datetime.combine(target_date, w.start_time)
            window_end = datetime.combine(target_date, w.end_time)
            while slot_start + timedelta(minutes=SLOT_MINUTES) <= window_end:
                candidates.append(ist_wall_clock_to_utc(slot_start))
                slot_start += timedelta(minutes=SLOT_MINUTES)

        now_utc = datetime.now(timezone.utc)
        candidates = [c for c in candidates if c > now_utc]
        if not candidates:
            return []

        day_start_utc = ist_wall_clock_to_utc(datetime.combine(target_date, time_cls(0, 0)))
        day_end_utc = day_start_utc + timedelta(days=1)
        # Filtered to the day range in Python (not a SQL WHERE range) because
        # SQLite (dev/tests) compares tz-aware literal params against its
        # naive-stored column lexically, silently missing rows — see
        # _ensure_utc. A doctor's appointment history is small enough that
        # fetching all active rows and filtering here is correct everywhere.
        all_active_slot_times = (
            await session.execute(
                select(Appointment.slot_time).where(
                    Appointment.doctor_id == doctor_id,
                    Appointment.status != "cancelled",
                )
            )
        ).scalars().all()
        booked = {
            _ensure_utc(t) for t in all_active_slot_times
            if day_start_utc <= _ensure_utc(t) < day_end_utc
        }

        return sorted(c for c in candidates if c not in booked)


async def is_doctor_open_at(
    tenant_id: str, doctor_id: str, requested_dt_utc: datetime | None,
) -> tuple[bool, str]:
    """Real availability check for one requested instant.

    reason is one of: "unparseable_time", "doctor_not_found",
    "doctor_unavailable", "no_schedule_configured",
    "slot_taken_or_outside_hours", "ok". v1 intentionally buckets "outside
    configured hours" and "already booked" into one reason — can be split
    later without changing this return shape.

    Floors requested_dt_utc to its containing 30-min boundary before the
    membership check, so a caller saying "3:07" matches the 3:00 slot.
    """
    if requested_dt_utc is None:
        return False, "unparseable_time"

    async with AsyncSessionLocal() as session:
        doctor = (
            await session.execute(
                select(Doctor).where(Doctor.id == doctor_id, Doctor.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if not doctor:
            return False, "doctor_not_found"
        if not doctor.is_available:
            return False, "doctor_unavailable"

        ist_dt = to_ist(requested_dt_utc)
        target_date = ist_dt.date()
        day_of_week = target_date.weekday()
        windows = (
            await session.execute(
                select(DoctorAvailability).where(
                    DoctorAvailability.doctor_id == doctor_id,
                    DoctorAvailability.day_of_week == day_of_week,
                )
            )
        ).scalars().all()
        if not windows:
            return False, "no_schedule_configured"

    floored_minute = 30 if ist_dt.minute >= 30 else 0
    floored_ist = ist_dt.replace(minute=floored_minute, second=0, microsecond=0)
    floored_utc = floored_ist.astimezone(timezone.utc)

    slots = await compute_available_slots(tenant_id, doctor_id, target_date)
    if floored_utc in slots:
        return True, "ok"
    return False, "slot_taken_or_outside_hours"
