# -*- coding: utf-8 -*-
"""A completed booking must be visible in BOTH dashboards, and in neither one that
should not see it.

This closes a gap: the booking path's honesty was well covered
(test_booking_processor.py, test_chat_booking_honesty.py both assert that nothing is
confirmed to a caller before a real awaited write), but nothing asserted the write
then SURFACES. "The row exists" and "the clinic can see the row" are separate
claims, served by two different endpoints with two different auth models:

    clinic admin  GET /tenants/{tenant_id}/appointments   (user.require_owns)
    superadmin    GET /admin/appointments                 (SuperAdmin, joins Tenant)

The tenant-isolation half matters more than the visibility half. A leak here shows
one clinic another clinic's patients — so the test seeds TWO clinics, books in each,
and asserts each admin sees exactly their own.

Everything DB-touching runs for real against SQLite; nothing is mocked.

Run: python -m pytest backend/tests/test_booking_reaches_both_dashboards.py -v
"""

# ── TEST SAFETY: force SQLite before importing backend.db ─────────────────────
import os

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_booking_dashboards.db"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-booking-dashboard-tests")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import backend.db as db_mod
from backend.db import AsyncSessionLocal, Base, engine
from backend.models.agent_config import AgentConfig
from backend.models.doctor import Doctor
from backend.models.tenant import Tenant
from backend.security import create_access_token, hash_password
from backend.services.his import create_appointment

CLINIC_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CLINIC_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
DOCTOR_A = "a1111111-1111-1111-1111-111111111111"
DOCTOR_B = "b1111111-1111-1111-1111-111111111111"


@pytest_asyncio.fixture
async def two_clinics():
    assert db_mod.IS_SQLITE, "TEST SAFETY: refusing to run against a non-SQLite database"
    db_mod._import_all_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as s:
        for tid, name, email, did, doc in (
            (CLINIC_A, "Clinic A", "a@example.com", DOCTOR_A, "Dr Anjali Sharma"),
            (CLINIC_B, "Clinic B", "b@example.com", DOCTOR_B, "Dr Rakesh Iyer"),
        ):
            s.add(Tenant(
                id=tid, clinic_name=name, admin_email=email,
                admin_password=hash_password("pw"), is_active=True,
            ))
            s.add(Doctor(id=did, tenant_id=tid, name=doc, specialization="Cardiologist"))
            s.add(AgentConfig(tenant_id=tid, agent_name="Receptionist", language="en-IN"))
        await s.commit()

    from backend.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def _superadmin():
    return {"Authorization": f"Bearer {create_access_token('superadmin', 'superadmin')}"}


def _clinic(tenant_id: str):
    return {"Authorization": f"Bearer {create_access_token(tenant_id, 'admin')}"}


async def _book(tenant_id: str, doctor_id: str, phone: str, call_id: str) -> dict:
    """Book through the REAL service function every booking path funnels into."""
    return await create_appointment(
        tenant_id=tenant_id, doctor_id=doctor_id,
        slot_time="3 pm", patient_phone=phone, call_id=call_id,
    )


@pytest.mark.asyncio
async def test_a_booking_appears_in_both_dashboards(two_clinics):
    client = two_clinics
    result = await _book(CLINIC_A, DOCTOR_A, "+919876543210", "call-a-1")
    assert result["appointment_id"], result
    assert result["status"] == "confirmed"

    # ── Clinic admin's own Appointments view ──────────────────────────────────
    r = await client.get(f"/tenants/{CLINIC_A}/appointments", headers=_clinic(CLINIC_A))
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["id"] == result["appointment_id"]
    assert rows[0]["doctor_name"] == "Dr Anjali Sharma"
    assert rows[0]["status"] == "confirmed"
    # The phone is masked for the clinic view — assert the masking is real, not
    # that a masked-looking string is present.
    assert rows[0]["patient_phone"].endswith("****")
    assert "3210" not in rows[0]["patient_phone"]

    # ── Superadmin's All Appointments view ────────────────────────────────────
    r = await client.get("/admin/appointments", headers=_superadmin())
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["id"] == result["appointment_id"]
    # Attributed to the right clinic — the whole point of this view.
    assert rows[0]["clinic_name"] == "Clinic A"
    assert rows[0]["doctor_name"] == "Dr Anjali Sharma"


@pytest.mark.asyncio
async def test_one_clinic_can_never_see_anothers_appointments(two_clinics):
    client = two_clinics
    a = await _book(CLINIC_A, DOCTOR_A, "+919876543210", "call-a-1")
    b = await _book(CLINIC_B, DOCTOR_B, "+919812345678", "call-b-1")

    ra = (await client.get(f"/tenants/{CLINIC_A}/appointments", headers=_clinic(CLINIC_A))).json()
    rb = (await client.get(f"/tenants/{CLINIC_B}/appointments", headers=_clinic(CLINIC_B))).json()

    assert [x["id"] for x in ra] == [a["appointment_id"]]
    assert [x["id"] for x in rb] == [b["appointment_id"]]

    # And asking for the OTHER clinic's list with your own token is refused — not
    # merely filtered to empty, which would be indistinguishable from "that clinic
    # has no appointments".
    #
    # 404 rather than 403 is deliberate on the product's part (see
    # AuthUser.require_owns): 403 would confirm that CLINIC_B exists, which is
    # itself information one clinic should not be able to extract about another.
    r = await client.get(f"/tenants/{CLINIC_B}/appointments", headers=_clinic(CLINIC_A))
    assert r.status_code == 404, r.text
    assert b["appointment_id"] not in r.text


@pytest.mark.asyncio
async def test_superadmin_sees_every_clinic_and_can_filter_to_one(two_clinics):
    client = two_clinics
    await _book(CLINIC_A, DOCTOR_A, "+919876543210", "call-a-1")
    await _book(CLINIC_B, DOCTOR_B, "+919812345678", "call-b-1")

    rows = (await client.get("/admin/appointments", headers=_superadmin())).json()
    assert {r["clinic_name"] for r in rows} == {"Clinic A", "Clinic B"}

    rows = (await client.get(
        f"/admin/appointments?clinic_id={CLINIC_A}", headers=_superadmin()
    )).json()
    assert {r["clinic_name"] for r in rows} == {"Clinic A"}


@pytest.mark.asyncio
async def test_a_clinic_admin_cannot_reach_the_global_view(two_clinics):
    """The superadmin view carries every clinic's patient data, so a clinic token
    must not open it."""
    r = await two_clinics.get("/admin/appointments", headers=_clinic(CLINIC_A))
    assert r.status_code in (401, 403), r.text


@pytest.mark.asyncio
async def test_one_call_cannot_produce_two_appointments(two_clinics):
    """Idempotency, asserted through the dashboard rather than the return value.

    A reconnect or a repeated confirm keyword re-runs the commit; if that created a
    second row, the clinic would see a duplicate booking for one call."""
    client = two_clinics
    first = await _book(CLINIC_A, DOCTOR_A, "+919876543210", "call-a-1")
    again = await _book(CLINIC_A, DOCTOR_A, "+919876543210", "call-a-1")

    assert again["appointment_id"] == first["appointment_id"]
    assert again.get("idempotent_hit") is True

    rows = (await client.get(f"/tenants/{CLINIC_A}/appointments", headers=_clinic(CLINIC_A))).json()
    assert len(rows) == 1
