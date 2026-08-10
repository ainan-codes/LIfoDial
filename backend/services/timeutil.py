"""backend/services/timeutil.py

IST (Asia/Kolkata) time helpers. All Lifodial clinics operate in IST for now
(confirmed with the user — no per-tenant timezone field exists or is needed).
A fixed UTC+5:30 offset is used rather than zoneinfo("Asia/Kolkata") — India
has observed no DST since 1945, so the offset never changes, and this avoids
a tzdata dependency.

This exists to fix a real bug: backend/services/his.py::parse_slot_datetime
used to build "now" from datetime.now(timezone.utc) and parse caller-spoken
day/time strings directly against it — i.e. it treated IST wall-clock as if
it already were UTC, mislabeling every stored slot_time by ~5.5 hours in the
wrong direction. Everything that touches a caller-spoken or doctor-schedule
wall-clock time should go through ist_now()/ist_wall_clock_to_utc() here, and
convert to true UTC only at the point of DB storage or comparison against
datetime.now(timezone.utc).
"""
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def ist_now() -> datetime:
    """Current wall-clock time in IST, tz-aware."""
    return datetime.now(timezone.utc).astimezone(IST)


def to_ist(dt: datetime) -> datetime:
    """Convert a tz-aware UTC (or any tz-aware) instant to its IST wall-clock
    representation — for display/comparison, never for storage."""
    if dt.tzinfo is None:
        raise ValueError("to_ist() requires a tz-aware datetime")
    return dt.astimezone(IST)


def ist_wall_clock_to_utc(dt: datetime) -> datetime:
    """Label a naive (or already-IST) wall-clock datetime as IST and convert
    it to the true UTC instant — for storage in slot_time columns."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    else:
        dt = dt.astimezone(IST)
    return dt.astimezone(timezone.utc)


def format_ist_clock(dt_ist: datetime) -> str:
    """Format an IST-wall-clock datetime as "3:00 PM" — portable across
    platforms (avoids strftime's non-portable %-I)."""
    hour = dt_ist.hour % 12 or 12
    ampm = "AM" if dt_ist.hour < 12 else "PM"
    return f"{hour}:{dt_ist.minute:02d} {ampm}"
