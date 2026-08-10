"""backend/services/availability.py

Real, Supabase-backed doctor availability — replaces the previously
hardcoded/mocked his.get_slots() and the previously-nonexistent
"is this slot actually open" check that let the voice agent offer or
confirm times with no relationship to a doctor's real schedule or existing
bookings.

Three entry points:
  - compute_available_slots: the real open 30-min slots for a doctor on a
    given IST calendar date (schedule windows minus already-booked slots).
  - is_doctor_open_at: a single yes/no + reason check for one requested
    instant, used by BookingProcessor before arming a confirmation and again
    immediately before committing the booking, and by
    his.execute_booking_action immediately before every BOOK/RESCHEDULE write.
  - availability_digest: the same slot computation for SEVERAL doctor/date
    pairs in ONE database session — used to put real open times into the
    chat path's system prompt without paying a Supabase connection handshake
    per doctor per day.
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


def floor_to_slot(dt_utc: datetime) -> datetime:
    """Floor a UTC instant to the 30-minute IST slot boundary that contains it.

    Same rule is_doctor_open_at applies before its membership test, exposed so
    callers that need to compare two instants at slot granularity (e.g. "is
    this reschedule actually a different slot?") use one definition of a slot
    rather than reimplementing the flooring.
    """
    ist_dt = to_ist(dt_utc)
    floored = ist_dt.replace(
        minute=(30 if ist_dt.minute >= 30 else 0), second=0, microsecond=0,
    )
    return floored.astimezone(timezone.utc)


def _window_candidates(windows, target_date: date_cls) -> list[datetime]:
    """Every 30-minute slot start the doctor's configured windows contain on
    target_date, as UTC instants — before any past/booked filtering. This is
    "inside the doctor's hours", which is what separates "we're closed then"
    from "someone already has that slot"."""
    candidates: list[datetime] = []
    for w in windows or []:
        slot_start = datetime.combine(target_date, w.start_time)
        window_end = datetime.combine(target_date, w.end_time)
        while slot_start + timedelta(minutes=SLOT_MINUTES) <= window_end:
            candidates.append(ist_wall_clock_to_utc(slot_start))
            slot_start += timedelta(minutes=SLOT_MINUTES)
    return candidates


def _booked_on(active_slot_times, target_date: date_cls) -> set[datetime]:
    """The doctor's already-taken slot instants on target_date.

    Filtered to the day range in Python (not a SQL WHERE range) because
    SQLite (dev/tests) compares tz-aware literal params against its
    naive-stored column lexically, silently missing rows — see _ensure_utc.
    A doctor's appointment history is small enough that fetching all active
    rows and filtering here is correct everywhere.
    """
    day_start_utc = ist_wall_clock_to_utc(datetime.combine(target_date, time_cls(0, 0)))
    day_end_utc = day_start_utc + timedelta(days=1)
    return {
        _ensure_utc(t) for t in active_slot_times
        if day_start_utc <= _ensure_utc(t) < day_end_utc
    }


def _slots_from_windows(
    windows, target_date: date_cls, active_slot_times, now_utc: datetime,
) -> list[datetime]:
    """The slot RULES, as one pure function: walk each configured window in
    30-minute steps, drop anything already past, drop anything already booked.

    Every caller below goes through this — the single-doctor path and the
    batched digest — so "what counts as an open slot" has exactly one
    definition no matter how the rows were fetched.
    """
    candidates = [c for c in _window_candidates(windows, target_date) if c > now_utc]
    if not candidates:
        return []
    booked = _booked_on(active_slot_times, target_date)
    return sorted(c for c in candidates if c not in booked)


async def _slots_in_session(
    session, tenant_id: str, doctor_id: str, target_date: date_cls,
) -> list[datetime]:
    """The real slot computation for one doctor/date, against a caller-supplied
    session — so is_doctor_open_at can do its whole check on one connection."""
    doctor = (
        await session.execute(
            select(Doctor).where(Doctor.id == doctor_id, Doctor.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if not doctor or not doctor.is_available:
        return []

    windows = (
        await session.execute(
            select(DoctorAvailability).where(
                DoctorAvailability.doctor_id == doctor_id,
                DoctorAvailability.day_of_week == target_date.weekday(),
            )
        )
    ).scalars().all()
    if not windows:
        return []

    active_slot_times = (
        await session.execute(
            select(Appointment.slot_time).where(
                Appointment.doctor_id == doctor_id,
                Appointment.status != "cancelled",
            )
        )
    ).scalars().all()

    return _slots_from_windows(
        windows, target_date, active_slot_times, datetime.now(timezone.utc),
    )


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
        return await _slots_in_session(session, tenant_id, doctor_id, target_date)


async def availability_digest(
    tenant_id: str,
    doctor_ids: list[str],
    dates: list[date_cls],
) -> dict[tuple[str, date_cls], list[datetime]]:
    """Open slots for every (doctor_id, date) pair — three queries, one session.

    Same rules and the same numbers compute_available_slots returns (both go
    through _slots_from_windows), just batched. The chat path needs several
    doctors × a couple of days per turn to answer "what times are free
    tomorrow?" with real data, and doing that one doctor/date at a time would
    put dozens of serial round-trips (and, worse, a session handshake each)
    inside the patient's reply latency.
    """
    out: dict[tuple[str, date_cls], list[datetime]] = {}
    ids = [str(d) for d in (doctor_ids or []) if d]
    if not ids or not dates:
        return out

    async with AsyncSessionLocal() as session:
        bookable = {
            str(d.id) for d in (
                await session.execute(
                    select(Doctor).where(
                        Doctor.id.in_(ids), Doctor.tenant_id == tenant_id,
                    )
                )
            ).scalars().all()
            if d.is_available
        }
        windows_by_doctor: dict[str, list] = {}
        for w in (
            await session.execute(
                select(DoctorAvailability).where(DoctorAvailability.doctor_id.in_(ids))
            )
        ).scalars().all():
            windows_by_doctor.setdefault(str(w.doctor_id), []).append(w)

        booked_by_doctor: dict[str, list] = {}
        for doctor_id, slot_time in (
            await session.execute(
                select(Appointment.doctor_id, Appointment.slot_time).where(
                    Appointment.doctor_id.in_(ids),
                    Appointment.status != "cancelled",
                )
            )
        ).all():
            booked_by_doctor.setdefault(str(doctor_id), []).append(slot_time)

    now_utc = datetime.now(timezone.utc)
    for doctor_id in ids:
        for target_date in dates:
            if doctor_id not in bookable:
                out[(doctor_id, target_date)] = []
                continue
            windows = [
                w for w in windows_by_doctor.get(doctor_id, [])
                if w.day_of_week == target_date.weekday()
            ]
            out[(doctor_id, target_date)] = _slots_from_windows(
                windows, target_date, booked_by_doctor.get(doctor_id, []), now_utc,
            )
    return out


async def is_doctor_open_at(
    tenant_id: str, doctor_id: str, requested_dt_utc: datetime | None,
) -> tuple[bool, str]:
    """Real availability check for one requested instant.

    reason is one of: "unparseable_time", "doctor_not_found",
    "doctor_unavailable", "no_schedule_configured", "outside_hours",
    "in_the_past", "slot_taken", "ok".

    "outside hours" and "already booked" used to share one bucketed reason
    ("slot_taken_or_outside_hours"). They are separated now because the agent
    reads a message built from this: "the doctor doesn't work then" and "that
    slot is taken, here are others" call for completely different replies, and
    telling a patient the clinic is closed at a time it is simply full is its
    own small lie.

    Floors requested_dt_utc to its containing 30-min boundary before the
    membership check, so a caller saying "3:07" matches the 3:00 slot.
    """
    if requested_dt_utc is None:
        return False, "unparseable_time"

    # One session for the whole check (it used to open a second one for the
    # slot computation) — every booking and reschedule now pays this check
    # immediately before its write, so the handshake count matters.
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

        floored_utc = floor_to_slot(requested_dt_utc)

        if floored_utc not in set(_window_candidates(windows, target_date)):
            return False, "outside_hours"
        if floored_utc <= datetime.now(timezone.utc):
            return False, "in_the_past"

        active_slot_times = (
            await session.execute(
                select(Appointment.slot_time).where(
                    Appointment.doctor_id == doctor_id,
                    Appointment.status != "cancelled",
                )
            )
        ).scalars().all()

    if floored_utc in _booked_on(active_slot_times, target_date):
        return False, "slot_taken"
    return True, "ok"
