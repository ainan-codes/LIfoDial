"""
Tests for the conflict-safe appointment insert path (his.py::create_appointment
/ sync_appointment_to_db) — the DB-level backstop (partial unique index
uq_appointments_doctor_slot_active + IntegrityError catch) that closes the
double-booking race a check-then-insert alone cannot close.

Run: python -m pytest backend/tests/test_appointment_conflict_safety.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-conflict-tests")
os.environ.setdefault("ENVIRONMENT", "development")

import pytest
import pytest_asyncio

import backend.db as db_module
from backend.models.tenant import Tenant
from backend.models.doctor import Doctor
from backend.models.doctor_availability import DoctorAvailability  # noqa: F401 — Doctor.availability_windows needs this registered before create_all
from backend.services.his import create_appointment


@pytest_asyncio.fixture
async def db():
    from backend.db import Base, engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield db_module.AsyncSessionLocal


async def _make_tenant_and_doctor(session_factory):
    async with session_factory() as s:
        t = Tenant(clinic_name="Test Clinic", language="en-IN", is_active=True, status="active")
        s.add(t)
        await s.flush()
        doc = Doctor(tenant_id=t.id, name="Dr Test", specialization="Cardiologist", is_available=True)
        s.add(doc)
        await s.commit()
        return t.id, doc.id


@pytest.mark.asyncio
async def test_second_booking_for_same_doctor_slot_is_rejected(db):
    tenant_id, doctor_id = await _make_tenant_and_doctor(db)

    r1 = await create_appointment(
        tenant_id=tenant_id, doctor_id=doctor_id, slot_time="3 pm", slot_date="today",
        patient_phone="+91111", call_id="call-A",
    )
    assert r1.get("appointment_id") is not None

    r2 = await create_appointment(
        tenant_id=tenant_id, doctor_id=doctor_id, slot_time="3 pm", slot_date="today",
        patient_phone="+91222", call_id="call-B",
    )
    assert r2.get("appointment_id") is None
    assert r2.get("reason") == "slot_taken"


@pytest.mark.asyncio
async def test_cancel_then_rebook_same_slot_succeeds(db):
    tenant_id, doctor_id = await _make_tenant_and_doctor(db)

    r1 = await create_appointment(
        tenant_id=tenant_id, doctor_id=doctor_id, slot_time="4 pm", slot_date="today",
        patient_phone="+91111", call_id="call-A",
    )
    appt_id = r1["appointment_id"]

    from sqlalchemy import select
    from backend.models.appointment import Appointment
    async with db() as s:
        appt = (await s.execute(select(Appointment).where(Appointment.id == appt_id))).scalar_one()
        appt.status = "cancelled"
        await s.commit()

    r2 = await create_appointment(
        tenant_id=tenant_id, doctor_id=doctor_id, slot_time="4 pm", slot_date="today",
        patient_phone="+91222", call_id="call-C",
    )
    assert r2.get("appointment_id") is not None
    assert r2["appointment_id"] != appt_id


@pytest.mark.asyncio
async def test_idempotent_retry_of_same_call_returns_original_not_a_conflict(db):
    tenant_id, doctor_id = await _make_tenant_and_doctor(db)

    r1 = await create_appointment(
        tenant_id=tenant_id, doctor_id=doctor_id, slot_time="5 pm", slot_date="today",
        patient_phone="+91111", call_id="call-A",
    )
    r2 = await create_appointment(
        tenant_id=tenant_id, doctor_id=doctor_id, slot_time="5 pm", slot_date="today",
        patient_phone="+91111", call_id="call-A",
    )
    assert r2["appointment_id"] == r1["appointment_id"]
    assert r2.get("reason") is None


# No asyncio.gather() concurrent-insert test here by design. Tried it: two
# AsyncSessionLocal() sessions racing to insert the same doctor+slot via
# aiosqlite against sqlite+aiosqlite:///:memory: produced inconsistent
# results across runs — including the WINNING commit's own row vanishing —
# because a single SQLite connection cannot correctly interleave two
# logically-separate transactions the way Postgres/asyncpg can (confirmed by
# also trying poolclass=StaticPool to force one shared connection, which made
# it worse, not better). This is a genuine aiosqlite/SQLite limitation, not a
# defect in the conflict-safety logic — sequential correctness is fully
# covered by the tests above, and the actual concurrent-safety guarantee
# comes from uq_appointments_doctor_slot_active existing at all (Postgres
# enforces it at the DB level regardless of asyncio interleaving), verified
# against real production data by backend/scripts/
# find_appointment_slot_conflicts.py before this migration was applied.
