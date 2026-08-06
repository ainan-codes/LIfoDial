"""
End-to-end tests for the clinic-admin flows that were silently broken.

Covers, via the real FastAPI app against an in-memory SQLite DB:
  * POST /admin/clinics no longer seeds fake doctors (the "Dr. Sharma" bug).
  * GET /api/clinic/stats exists and is tenant-scoped (the old Dashboard called
    /api/dashboard/stats, which never existed AND is hard-404'd by middleware).
  * PUT /tenants/{id} actually persists clinic_name + language (Settings → Clinic
    Profile used to show "✓ Saved" without sending a request at all).
  * PATCH /agents/{id} accepts clinic_info as an OBJECT (it was typed `str`, so
    the natural payload 422'd and working hours could never be saved).
  * A clinic-admin token cannot read or write platform LiveKit/SIP credentials.

Run: python -m pytest backend/tests/test_clinic_admin_endpoints.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-clinic-admin-tests")
os.environ.setdefault("ENVIRONMENT", "development")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import backend.db as db_module
from backend.models.agent_config import AgentConfig
from backend.models.doctor import Doctor
from backend.models.tenant import Tenant
from backend.security import create_access_token, hash_password


@pytest_asyncio.fixture
async def app_client():
    """Real app, real routes, fresh schema."""
    from backend.db import Base, engine
    from backend.main import app

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


_ai_number_seq = iter(range(10_000, 99_999))


async def _make_clinic(clinic_name="Test Clinic", email="admin@test.com"):
    # ai_number is UNIQUE, so each fixture clinic needs its own.
    async with db_module.AsyncSessionLocal() as s:
        t = Tenant(
            clinic_name=clinic_name, admin_email=email,
            admin_password=hash_password("pw"), language="en-IN",
            ai_number=f"+91 90001 {next(_ai_number_seq)}", is_active=True,
        )
        s.add(t)
        await s.flush()
        agent = AgentConfig(
            tenant_id=t.id, agent_name="Receptionist",
            livekit_api_key="PLATFORM-KEY", livekit_api_secret="PLATFORM-SECRET",
            sip_auth_token="PLATFORM-SIP-TOKEN",
        )
        s.add(agent)
        await s.commit()
        return t.id, agent.id


def _clinic_token(tenant_id: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(tenant_id, 'clinic')}"}


def _super_token() -> dict:
    return {"Authorization": f"Bearer {create_access_token('superadmin', 'superadmin')}"}


# ── Seeded doctors ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_new_clinic_has_no_seeded_doctors(app_client):
    r = await app_client.post("/admin/clinics", headers=_super_token(), json={
        "clinic_name": "Fresh Clinic", "admin_name": "A",
        "admin_email": "fresh@clinic.com", "location": "Kochi", "language": "en-IN",
    })
    assert r.status_code == 200, r.text
    tenant_id = r.json()["tenant_id"]

    async with db_module.AsyncSessionLocal() as s:
        docs = (await s.execute(select(Doctor).where(Doctor.tenant_id == tenant_id))).scalars().all()
    assert docs == [], f"a brand-new clinic must start with zero doctors, got {[d.name for d in docs]}"


# ── Dashboard KPI endpoint ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clinic_stats_endpoint_exists_and_returns_all_kpi_keys(app_client):
    tenant_id, _ = await _make_clinic()
    r = await app_client.get("/api/clinic/stats", headers=_clinic_token(tenant_id))
    assert r.status_code == 200, r.text
    body = r.json()
    # These are exactly the keys Dashboard.tsx reads (KPI_DEFS + live/missed).
    for key in ("calls_today", "booked_today", "avg_duration",
                "resolution_rate", "missed_calls", "live_calls"):
        assert key in body, f"Dashboard reads {key!r} but the endpoint omits it"
    assert body["calls_today"] == 0
    # No calls yet must read as "no data", not as a 0% failure rate.
    assert body["resolution_rate"] == "—"


@pytest.mark.asyncio
async def test_live_calls_ignores_stranded_records(app_client):
    """A job that dies never writes a terminal status, so its row stays
    'in_progress' forever. Production had 68 such rows going back weeks and the
    dashboard reported "68 live calls" for an idle clinic."""
    from datetime import datetime, timedelta, timezone

    from backend.models.call_record import CallRecord

    tenant_id, agent_id = await _make_clinic(email="live@clinic.com")
    now = datetime.now(timezone.utc)

    async with db_module.AsyncSessionLocal() as s:
        s.add_all([
            # Stranded weeks ago — must NOT count.
            CallRecord(tenant_id=tenant_id, agent_id=agent_id, status="in_progress",
                       created_at=now - timedelta(days=14)),
            CallRecord(tenant_id=tenant_id, agent_id=agent_id, status="active",
                       created_at=now - timedelta(hours=5)),
            # Genuinely in flight right now — must count.
            CallRecord(tenant_id=tenant_id, agent_id=agent_id, status="in_progress",
                       created_at=now - timedelta(seconds=20)),
        ])
        await s.commit()

    r = await app_client.get("/api/clinic/stats", headers=_clinic_token(tenant_id))
    assert r.status_code == 200, r.text
    assert r.json()["live_calls"] == 1, "stranded records must not be reported as live"


@pytest.mark.asyncio
async def test_stats_are_tenant_scoped(app_client):
    """One clinic's calls must never appear in another clinic's KPIs."""
    from datetime import datetime, timezone

    from backend.models.call_record import CallRecord

    mine, mine_agent = await _make_clinic("Mine", "s-mine@c.com")
    theirs, theirs_agent = await _make_clinic("Theirs", "s-theirs@c.com")

    async with db_module.AsyncSessionLocal() as s:
        s.add(CallRecord(tenant_id=theirs, agent_id=theirs_agent, status="completed",
                         outcome="booked", duration_seconds=60,
                         created_at=datetime.now(timezone.utc)))
        await s.commit()

    r = await app_client.get("/api/clinic/stats", headers=_clinic_token(mine))
    assert r.json()["calls_today"] == 0, "leaked another clinic's calls"

    r = await app_client.get("/api/clinic/stats", headers=_clinic_token(theirs))
    assert r.json()["calls_today"] == 1


@pytest.mark.asyncio
async def test_old_dashboard_path_is_still_blocked(app_client):
    """Documents WHY the new path is /api/clinic/... and not /api/dashboard/..."""
    tenant_id, _ = await _make_clinic()
    r = await app_client.get("/api/dashboard/stats", headers=_clinic_token(tenant_id))
    assert r.status_code == 404


# ── Clinic profile save ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_put_tenant_persists_name_and_language(app_client):
    tenant_id, _ = await _make_clinic(clinic_name="Old Name")
    r = await app_client.put(f"/tenants/{tenant_id}", headers=_clinic_token(tenant_id),
                             json={"clinic_name": "New Name", "language": "ta-IN"})
    assert r.status_code == 200, r.text

    async with db_module.AsyncSessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    assert t.clinic_name == "New Name"
    assert t.language == "ta-IN"


@pytest.mark.asyncio
async def test_put_tenant_rejects_blank_clinic_name(app_client):
    tenant_id, _ = await _make_clinic(clinic_name="Keep Me")
    r = await app_client.put(f"/tenants/{tenant_id}", headers=_clinic_token(tenant_id),
                             json={"clinic_name": "   "})
    assert r.status_code == 422

    async with db_module.AsyncSessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one()
    assert t.clinic_name == "Keep Me"


@pytest.mark.asyncio
async def test_clinic_cannot_edit_another_clinic(app_client):
    mine, _ = await _make_clinic("Mine", "mine@c.com")
    theirs, _ = await _make_clinic("Theirs", "theirs@c.com")
    r = await app_client.put(f"/tenants/{theirs}", headers=_clinic_token(mine),
                             json={"clinic_name": "Hijacked"})
    assert r.status_code == 404  # require_owns() answers 404, not 403

    async with db_module.AsyncSessionLocal() as s:
        t = (await s.execute(select(Tenant).where(Tenant.id == theirs))).scalar_one()
    assert t.clinic_name == "Theirs"


# ── Working hours (clinic_info as an object) ───────────────────────────────────

@pytest.mark.asyncio
async def test_patch_agent_accepts_clinic_info_object(app_client):
    """clinic_info stays writable by a clinic — it is the clinic's own operational
    truth (working hours) and Settings → Clinic Profile saves it through this
    endpoint. It is the one exception to the read-only rule asserted below; see
    agents.py::_CLINIC_WRITABLE_AGENT_FIELDS."""
    tenant_id, agent_id = await _make_clinic()
    r = await app_client.patch(f"/agents/{agent_id}", headers=_clinic_token(tenant_id),
                               json={"clinic_info": {"working_hours": "10:00 AM - 6:00 PM"}})
    assert r.status_code == 200, r.text

    async with db_module.AsyncSessionLocal() as s:
        a = (await s.execute(select(AgentConfig).where(AgentConfig.id == agent_id))).scalar_one()
    info = a.clinic_info if isinstance(a.clinic_info, dict) else {}
    assert info.get("working_hours") == "10:00 AM - 6:00 PM"


# ── Credential isolation ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_clinic_admin_never_receives_platform_credentials(app_client):
    tenant_id, agent_id = await _make_clinic(email="secret@clinic.com")
    r = await app_client.get(f"/agents/{agent_id}", headers=_clinic_token(tenant_id))
    assert r.status_code == 200, r.text
    body = r.json()

    for field in ("livekit_api_key", "livekit_api_secret", "sip_auth_token"):
        assert field not in body, f"{field} leaked to a clinic-admin token"
    blob = r.text
    for secret in ("PLATFORM-KEY", "PLATFORM-SECRET", "PLATFORM-SIP-TOKEN"):
        assert secret not in blob, f"secret value {secret!r} leaked in the response body"


@pytest.mark.asyncio
async def test_superadmin_still_sees_platform_credentials(app_client):
    _, agent_id = await _make_clinic(email="sa@clinic.com")
    r = await app_client.get(f"/agents/{agent_id}", headers=_super_token())
    assert r.status_code == 200, r.text
    assert r.json().get("livekit_api_secret") == "PLATFORM-SECRET"


@pytest.mark.asyncio
async def test_clinic_admin_cannot_write_platform_credentials(app_client):
    tenant_id, agent_id = await _make_clinic(email="w@clinic.com")
    r = await app_client.patch(f"/agents/{agent_id}", headers=_clinic_token(tenant_id),
                               json={"livekit_api_secret": "HIJACKED"})
    assert r.status_code == 403, r.text

    async with db_module.AsyncSessionLocal() as s:
        a = (await s.execute(select(AgentConfig).where(AgentConfig.id == agent_id))).scalar_one()
    assert a.livekit_api_secret == "PLATFORM-SECRET", "credential was overwritten"


@pytest.mark.asyncio
async def test_clinic_admin_cannot_edit_agent_behaviour(app_client):
    """REVERSED DELIBERATELY — this test used to assert the opposite.

    It was `test_clinic_admin_can_still_edit_agent_behaviour`, and it passed: a
    clinic token could PATCH first_message / system_prompt / tts_voice /
    llm_temperature. When the credential deny-list was added, "the restriction must
    not block legitimate edits" meant only "must not block anything that is not a
    platform secret".

    The product rule changed: My Agent is a read-only view for clinic admins, and
    the fields below are precisely the ones that decide whether calls work at all
    (a voice that does not exist, or a prompt that breaks booking, is a broken
    receptionist the clinic cannot fix). They are edited by the platform team in
    Superadmin → Agents. `clinic_info` remains writable — see the test above.
    """
    tenant_id, agent_id = await _make_clinic(email="b@clinic.com")
    r = await app_client.patch(f"/agents/{agent_id}", headers=_clinic_token(tenant_id), json={
        "first_message": "Namaste, Test Clinic!",
        "system_prompt": "Be brief.",
        "tts_voice": "meera",
        "llm_temperature": 0.4,
    })
    assert r.status_code == 403, r.text
    # The 403 names what it refused rather than being opaque.
    detail = r.json()["detail"]
    assert "read-only" in detail.lower() and "tts_voice" in detail

    async with db_module.AsyncSessionLocal() as s:
        a = (await s.execute(select(AgentConfig).where(AgentConfig.id == agent_id))).scalar_one()
    assert a.first_message != "Namaste, Test Clinic!"
    assert a.tts_voice != "meera"


@pytest.mark.asyncio
async def test_a_mixed_patch_is_refused_whole(app_client):
    """clinic_info being allowed must not become a carrier for the rest.

    A payload that pairs the permitted field with a config field is refused
    entirely — no partial application, so nothing lands from a rejected request.
    """
    tenant_id, agent_id = await _make_clinic(email="mixed@clinic.com")
    r = await app_client.patch(f"/agents/{agent_id}", headers=_clinic_token(tenant_id), json={
        "clinic_info": {"working_hours": "9:00 AM - 5:00 PM"},
        "tts_voice": "meera",
    })
    assert r.status_code == 403, r.text

    async with db_module.AsyncSessionLocal() as s:
        a = (await s.execute(select(AgentConfig).where(AgentConfig.id == agent_id))).scalar_one()
    info = a.clinic_info if isinstance(a.clinic_info, dict) else {}
    assert info.get("working_hours") != "9:00 AM - 5:00 PM", "the allowed half of a refused patch was applied"
    assert a.tts_voice != "meera"
