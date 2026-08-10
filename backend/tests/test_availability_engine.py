"""
Tests for backend/services/availability.py — the real, Supabase-backed doctor
availability engine that replaced the previously 100%-hardcoded his.get_slots()
and the previously-nonexistent "is this slot actually open" check.

Covers:
  - No configured schedule for a day => no slots (silence is not "always open").
  - Correct 30-minute slot boundaries within a configured window.
  - An already-booked (non-cancelled) slot is excluded; a cancelled one reopens.
  - An on-leave doctor always returns no slots, regardless of schedule.
  - Past-today slots are excluded.
  - is_doctor_open_at's reason vocabulary for each early-exit case.

Run: python -m pytest backend/tests/test_availability_engine.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-availability-tests")
os.environ.setdefault("ENVIRONMENT", "development")

from datetime import timedelta, time as time_cls

import pytest
import pytest_asyncio

import backend.db as db_module
from backend.models.tenant import Tenant
from backend.models.doctor import Doctor
from backend.models.doctor_availability import DoctorAvailability
from backend.models.appointment import Appointment
from backend.services.availability import compute_available_slots, is_doctor_open_at
from backend.services.timeutil import ist_now


@pytest_asyncio.fixture
async def db():
    from backend.db import Base, engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield db_module.AsyncSessionLocal


async def _make_tenant_and_doctor(session_factory, is_available: bool = True):
    async with session_factory() as s:
        t = Tenant(clinic_name="Test Clinic", language="en-IN", is_active=True, status="active")
        s.add(t)
        await s.flush()
        doc = Doctor(tenant_id=t.id, name="Dr Test", specialization="Cardiologist", is_available=is_available)
        s.add(doc)
        await s.commit()
        return t.id, doc.id


async def _add_window(session_factory, tenant_id, doctor_id, day_of_week, start, end):
    async with session_factory() as s:
        s.add(DoctorAvailability(tenant_id=tenant_id, doctor_id=doctor_id, day_of_week=day_of_week, start_time=start, end_time=end))
        await s.commit()


@pytest.mark.asyncio
async def test_no_schedule_configured_returns_no_slots(db):
    tenant_id, doctor_id = await _make_tenant_and_doctor(db)
    today = ist_now().date()
    slots = await compute_available_slots(tenant_id, doctor_id, today)
    assert slots == []


@pytest.mark.asyncio
async def test_slot_boundaries_within_a_window(db):
    tenant_id, doctor_id = await _make_tenant_and_doctor(db)
    now = ist_now()
    # A window that spans the rest of today, well clear of "now".
    start = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0).time()
    end = (now + timedelta(hours=3)).replace(minute=0, second=0, microsecond=0).time()
    await _add_window(db, tenant_id, doctor_id, now.weekday(), start, end)

    slots = await compute_available_slots(tenant_id, doctor_id, now.date())
    # A 2-hour window in 30-min increments gives exactly 4 slots, and the
    # last slot must END at (not start at) the window's end time.
    assert len(slots) == 4
    from backend.services.timeutil import to_ist
    last_start_ist = to_ist(slots[-1])
    assert (last_start_ist.hour, last_start_ist.minute) != (end.hour, end.minute), \
        "the window's end time itself must never be offered as a slot START"


@pytest.mark.asyncio
async def test_booked_slot_is_excluded_and_cancelled_slot_reopens(db):
    tenant_id, doctor_id = await _make_tenant_and_doctor(db)
    now = ist_now()
    await _add_window(db, tenant_id, doctor_id, now.weekday(), time_cls(0, 0), time_cls(23, 59))

    slots = await compute_available_slots(tenant_id, doctor_id, now.date())
    assert slots, "expected at least one future slot today"
    target = slots[0]

    async with db() as s:
        s.add(Appointment(tenant_id=tenant_id, doctor_id=doctor_id, slot_time=target, patient_phone="+911", status="confirmed"))
        await s.commit()

    remaining = await compute_available_slots(tenant_id, doctor_id, now.date())
    assert target not in remaining

    async with db() as s:
        appt = (await s.execute(__import__("sqlalchemy").select(Appointment).where(Appointment.doctor_id == doctor_id))).scalar_one()
        appt.status = "cancelled"
        await s.commit()

    reopened = await compute_available_slots(tenant_id, doctor_id, now.date())
    assert target in reopened


@pytest.mark.asyncio
async def test_on_leave_doctor_has_no_slots_regardless_of_schedule(db):
    tenant_id, doctor_id = await _make_tenant_and_doctor(db, is_available=False)
    now = ist_now()
    await _add_window(db, tenant_id, doctor_id, now.weekday(), time_cls(0, 0), time_cls(23, 59))
    slots = await compute_available_slots(tenant_id, doctor_id, now.date())
    assert slots == []


@pytest.mark.asyncio
async def test_past_slots_excluded_today(db):
    tenant_id, doctor_id = await _make_tenant_and_doctor(db)
    now = ist_now()
    # A window that already fully elapsed earlier today.
    if now.hour < 1:
        pytest.skip("flaky only in the first hour of the IST day; not worth working around")
    await _add_window(db, tenant_id, doctor_id, now.weekday(), time_cls(0, 0), time_cls(0, 30))
    slots = await compute_available_slots(tenant_id, doctor_id, now.date())
    assert slots == []


@pytest.mark.asyncio
async def test_is_doctor_open_at_reason_vocabulary(db):
    tenant_id, doctor_id = await _make_tenant_and_doctor(db)

    is_open, reason = await is_doctor_open_at(tenant_id, doctor_id, None)
    assert (is_open, reason) == (False, "unparseable_time")

    is_open, reason = await is_doctor_open_at(tenant_id, "not-a-real-doctor", ist_now())
    assert (is_open, reason) == (False, "doctor_not_found")

    now = ist_now()
    is_open, reason = await is_doctor_open_at(tenant_id, doctor_id, now + timedelta(hours=1))
    assert (is_open, reason) == (False, "no_schedule_configured")

    start = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0).time()
    end = (now + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0).time()
    await _add_window(db, tenant_id, doctor_id, now.weekday(), start, end)

    slots = await compute_available_slots(tenant_id, doctor_id, now.date())
    assert slots
    is_open, reason = await is_doctor_open_at(tenant_id, doctor_id, slots[0])
    assert (is_open, reason) == (True, "ok")

    # A time later the same day but outside [start, end) — asserted via a
    # bounded offset rather than a fixed +N hours, since +N could roll past
    # midnight into a day with no schedule at all (a different, also-valid
    # "closed" reason) depending on when this test happens to run.
    outside_same_day = end.hour < 22
    if outside_same_day:
        is_open, reason = await is_doctor_open_at(tenant_id, doctor_id, now.replace(hour=23, minute=0, second=0, microsecond=0))
        assert (is_open, reason) == (False, "slot_taken_or_outside_hours")
