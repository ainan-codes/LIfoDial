"""
Tests for the doctor duplicate-creation guard — the actual bug behind the
"Dr. Salman / HIS 002" x3 duplicate report: nothing previously stopped
POST /tenants/{id}/doctors from creating a second row for a doctor that
already existed (whole-name collision, or a colliding his_doctor_id).

Covers:
  - Same name (case/whitespace-insensitive) => 409, with an existing_doctor
    hint; explicit allow_duplicate_name=True overrides it (two real doctors
    CAN share a common name).
  - Colliding his_doctor_id => 409 via the DB-level unique index (a real
    HIS id must be unique per clinic, unlike a free-text name).
  - Editing a doctor (PATCH) was already correct before this change and
    keeps working — this guard only touches the create path.

Run: python -m pytest backend/tests/test_doctor_dedup_guard.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-dedup-tests")
os.environ.setdefault("ENVIRONMENT", "development")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import backend.db as db_module
from backend.models.tenant import Tenant
from backend.security import create_access_token, hash_password


@pytest_asyncio.fixture
async def app_client():
    from backend.db import Base, engine
    from backend.main import app

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _make_clinic():
    async with db_module.AsyncSessionLocal() as s:
        t = Tenant(
            clinic_name="Test Clinic", admin_email="admin@test.com",
            admin_password=hash_password("pw"), language="en-IN",
            ai_number="+91 90009 00001", is_active=True,
        )
        s.add(t)
        await s.commit()
        return t.id


def _headers(tenant_id: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(tenant_id, 'clinic')}"}


@pytest.mark.asyncio
async def test_duplicate_name_is_rejected_with_existing_doctor_hint(app_client):
    tenant_id = await _make_clinic()
    headers = _headers(tenant_id)

    r1 = await app_client.post(f"/tenants/{tenant_id}/doctors", headers=headers, json={
        "name": "Dr Salman", "specialization": "Cardiologist", "his_doctor_id": "HIS 002",
    })
    assert r1.status_code == 201

    r2 = await app_client.post(f"/tenants/{tenant_id}/doctors", headers=headers, json={
        "name": "  dr salman ", "specialization": "Cardiologist",
    })
    assert r2.status_code == 409
    assert "Dr Salman" in r2.json()["detail"]

    r3 = await app_client.get(f"/tenants/{tenant_id}/doctors", headers=headers)
    assert len(r3.json()) == 1, "the rejected duplicate must not have been created"


@pytest.mark.asyncio
async def test_allow_duplicate_name_overrides_the_guard(app_client):
    tenant_id = await _make_clinic()
    headers = _headers(tenant_id)

    await app_client.post(f"/tenants/{tenant_id}/doctors", headers=headers, json={
        "name": "Dr Sharma", "specialization": "Cardiologist",
    })
    r2 = await app_client.post(f"/tenants/{tenant_id}/doctors", headers=headers, json={
        "name": "Dr Sharma", "specialization": "Orthopedics", "allow_duplicate_name": True,
    })
    assert r2.status_code == 201

    r3 = await app_client.get(f"/tenants/{tenant_id}/doctors", headers=headers)
    assert len(r3.json()) == 2, "two real doctors ARE allowed to share a common name when overridden"


@pytest.mark.asyncio
async def test_duplicate_his_doctor_id_is_rejected_even_with_a_different_name(app_client):
    tenant_id = await _make_clinic()
    headers = _headers(tenant_id)

    r1 = await app_client.post(f"/tenants/{tenant_id}/doctors", headers=headers, json={
        "name": "Dr Salman", "specialization": "Cardiologist", "his_doctor_id": "HIS 002",
    })
    assert r1.status_code == 201

    # allow_duplicate_name bypasses the NAME check but not the his_doctor_id
    # DB constraint — a real HIS id colliding is always a genuine duplicate.
    r2 = await app_client.post(f"/tenants/{tenant_id}/doctors", headers=headers, json={
        "name": "Someone Else", "specialization": "Orthopedics",
        "his_doctor_id": "HIS 002", "allow_duplicate_name": True,
    })
    assert r2.status_code == 409

    r3 = await app_client.get(f"/tenants/{tenant_id}/doctors", headers=headers)
    assert len(r3.json()) == 1


@pytest.mark.asyncio
async def test_his_doctor_id_is_unique_per_clinic_not_globally(app_client):
    """The same his_doctor_id at TWO DIFFERENT clinics must not collide —
    the unique index is scoped to (tenant_id, his_doctor_id), not global."""
    async with db_module.AsyncSessionLocal() as s:
        t1 = Tenant(clinic_name="Clinic One", language="en-IN", is_active=True, status="active", ai_number="+91 90009 00002")
        t2 = Tenant(clinic_name="Clinic Two", language="en-IN", is_active=True, status="active", ai_number="+91 90009 00003")
        s.add_all([t1, t2])
        await s.commit()
        tenant1_id, tenant2_id = t1.id, t2.id

    from backend.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r1 = await c.post(f"/tenants/{tenant1_id}/doctors", headers=_headers(tenant1_id), json={
            "name": "Dr A", "specialization": "General", "his_doctor_id": "HIS-001",
        })
        r2 = await c.post(f"/tenants/{tenant2_id}/doctors", headers=_headers(tenant2_id), json={
            "name": "Dr B", "specialization": "General", "his_doctor_id": "HIS-001",
        })
    assert r1.status_code == 201
    assert r2.status_code == 201


@pytest.mark.asyncio
async def test_edit_still_updates_in_place_not_a_new_row(app_client):
    """The PATCH path was never the bug (routers/doctors.py::update_doctor
    always updated by id) — confirm this guard didn't regress it."""
    tenant_id = await _make_clinic()
    headers = _headers(tenant_id)

    r1 = await app_client.post(f"/tenants/{tenant_id}/doctors", headers=headers, json={
        "name": "Dr Original", "specialization": "Cardiologist",
    })
    doctor_id = r1.json()["id"]

    r2 = await app_client.patch(f"/tenants/{tenant_id}/doctors/{doctor_id}", headers=headers, json={
        "name": "Dr Renamed",
    })
    assert r2.status_code == 200
    assert r2.json()["id"] == doctor_id

    r3 = await app_client.get(f"/tenants/{tenant_id}/doctors", headers=headers)
    docs = r3.json()
    assert len(docs) == 1
    assert docs[0]["name"] == "Dr Renamed"
