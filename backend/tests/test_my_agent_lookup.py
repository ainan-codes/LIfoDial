"""
GET /agents/mine must resolve by the session's clinic, never by a supplied email.

The bug: kmct's clinic dashboard showed "No agent configured for
admin@lifodial.com" while superadmin showed the agent perfectly. The endpoint
matched a client-supplied ?email= against Tenant.admin_email and never looked at
the session's clinic at all, so the answer depended on whatever email the browser
happened to be holding — for an impersonated session, the superadmin's own.

kmct's DATA was never wrong (verified directly against the live database), so
every test here is about the lookup key.

Run: python -m pytest backend/tests/test_my_agent_lookup.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-my-agent-lookup")
os.environ.setdefault("ENVIRONMENT", "development")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import backend.db as db_module
from backend.models.agent_config import AgentConfig
from backend.models.tenant import Tenant
from backend.security import create_access_token, hash_password


@pytest_asyncio.fixture
async def app_client():
    from backend.db import Base, _import_all_models, engine
    from backend.main import app

    # create_all only creates tables for models that have been IMPORTED, so a
    # fixture that relies on this module's imports silently omits whatever it does
    # not mention — here audit_logs, which made impersonation 500 rather than fail
    # visibly. Registering via the app's own helper keeps the test schema identical
    # to the app's.
    _import_all_models()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


_seq = iter(range(30_000, 99_999))


async def _clinic_with_agent(clinic_name: str, admin_email: str | None, **agent_kw) -> tuple[str, str]:
    async with db_module.AsyncSessionLocal() as s:
        t = Tenant(
            clinic_name=clinic_name, admin_email=admin_email,
            admin_password=hash_password("pw"), language=agent_kw.get("language", "en-IN"),
            ai_number=f"+91 90003 {next(_seq)}", is_active=True,
        )
        s.add(t)
        await s.flush()
        a = AgentConfig(tenant_id=t.id, agent_name=agent_kw.pop("agent_name", "Receptionist"), **agent_kw)
        s.add(a)
        await s.commit()
        return t.id, a.id


def _clinic(tenant_id: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(tenant_id, 'clinic')}"}


def _super() -> dict:
    return {"Authorization": f"Bearer {create_access_token('superadmin', 'superadmin')}"}


# The two live clinics, reproduced from the real rows.
KMCT = dict(clinic_name="kmct", admin_email="ainan@gmail.com", language="ml-IN",
            tts_provider="sarvam", tts_model="bulbul:v3", tts_voice="pooja",
            llm_provider="groq", llm_model="llama-3.3-70b-versatile", status="CONFIGURED")
ASTER = dict(clinic_name="aster clnic kochi", admin_email="mohammedainan3@gmail.com",
             language="hi-IN", tts_provider="sarvam", tts_model="bulbul:v3",
             tts_voice="shubh", llm_provider="groq", llm_model="llama-3.3-70b-versatile",
             status="ACTIVE")


async def _both(app_client):
    kmct_id, kmct_agent = await _clinic_with_agent(**KMCT)
    aster_id, aster_agent = await _clinic_with_agent(**ASTER)
    return (kmct_id, kmct_agent), (aster_id, aster_agent)


# ── The reported bug ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_clinic_session_gets_its_own_agent_with_no_email_at_all(app_client):
    """The fix: no query param, no stored identity — just the token."""
    (kmct_id, kmct_agent), _ = await _both(app_client)

    r = await app_client.get("/agents/mine", headers=_clinic(kmct_id))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == kmct_agent
    assert body["tenant_id"] == kmct_id
    assert body["agent_name"] == "Receptionist"
    # The exact configuration the dashboard must render for kmct.
    assert body["language"] == "ml-IN"
    assert (body["tts_provider"], body["tts_model"], body["tts_voice"]) == (
        "sarvam", "bulbul:v3", "pooja")
    assert (body["llm_provider"], body["llm_model"]) == ("groq", "llama-3.3-70b-versatile")


@pytest.mark.asyncio
async def test_the_superadmins_email_can_no_longer_break_a_clinic_lookup(app_client):
    """Reproduces the exact failing call: a clinic session carrying the
    superadmin's email. It used to 404 "No agent found for email:
    admin@lifodial.com"; the email must now be ignored outright."""
    (kmct_id, kmct_agent), _ = await _both(app_client)

    r = await app_client.get(
        "/agents/mine?email=admin@lifodial.com", headers=_clinic(kmct_id)
    )
    assert r.status_code == 200, r.text
    assert r.json()["id"] == kmct_agent


@pytest.mark.asyncio
async def test_an_impersonation_session_resolves_to_the_impersonated_clinic(app_client):
    """The path the bug was reported through: superadmin -> impersonate kmct ->
    My Agent. The impersonation token is a clinic token, so it resolves like one."""
    (kmct_id, kmct_agent), _ = await _both(app_client)

    r = await app_client.post(f"/admin/clinics/{kmct_id}/impersonate", headers=_super())
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]

    r = await app_client.get("/agents/mine", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == kmct_agent
    assert r.json()["language"] == "ml-IN"
    # An impersonated session sees what the clinic sees: no platform credentials.
    assert not r.json().get("livekit_api_secret")


# ── Scoping: a supplied email must not be able to steer the lookup ────────────

@pytest.mark.asyncio
async def test_a_clinic_cannot_reach_another_clinic_by_passing_its_email(app_client):
    """The old key was client-supplied, so this is the shape of the leak it
    invited. Aster's own email must not fetch aster's agent for kmct."""
    (kmct_id, kmct_agent), (aster_id, aster_agent) = await _both(app_client)

    r = await app_client.get(
        "/agents/mine?email=mohammedainan3@gmail.com", headers=_clinic(kmct_id)
    )
    assert r.status_code == 200, r.text
    assert r.json()["id"] == kmct_agent, "a supplied email steered the lookup"
    assert r.json()["tenant_id"] == kmct_id

    # Same via tenant_id.
    r = await app_client.get(f"/agents/mine?tenant_id={aster_id}", headers=_clinic(kmct_id))
    assert r.status_code == 200, r.text
    assert r.json()["id"] == kmct_agent, "a supplied tenant_id steered the lookup"


@pytest.mark.asyncio
async def test_each_clinic_gets_its_own_agent(app_client):
    """No regression for aster: same endpoint, different session, its own agent."""
    (kmct_id, kmct_agent), (aster_id, aster_agent) = await _both(app_client)

    kmct_body = (await app_client.get("/agents/mine", headers=_clinic(kmct_id))).json()
    aster_body = (await app_client.get("/agents/mine", headers=_clinic(aster_id))).json()

    assert kmct_body["id"] == kmct_agent and kmct_body["language"] == "ml-IN"
    assert aster_body["id"] == aster_agent and aster_body["language"] == "hi-IN"
    assert aster_body["tts_voice"] == "shubh"


# ── The superadmin path ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_superadmin_must_name_a_clinic(app_client):
    """A superadmin token has no clinic of its own, so there is nothing to
    resolve — say so instead of guessing or 404ing."""
    await _both(app_client)

    r = await app_client.get("/agents/mine", headers=_super())
    assert r.status_code == 400, r.text
    assert "tenant_id" in r.json()["detail"]


@pytest.mark.asyncio
async def test_a_superadmin_may_look_up_any_clinic_by_id_or_email(app_client):
    (kmct_id, kmct_agent), (aster_id, aster_agent) = await _both(app_client)

    r = await app_client.get(f"/agents/mine?tenant_id={kmct_id}", headers=_super())
    assert r.status_code == 200, r.text
    assert r.json()["id"] == kmct_agent
    # Superadmin keeps the unredacted view.
    assert "livekit_api_secret" in r.json()

    r = await app_client.get("/agents/mine?email=mohammedainan3@gmail.com", headers=_super())
    assert r.status_code == 200, r.text
    assert r.json()["id"] == aster_agent


# ── Cases the old email key could not express ────────────────────────────────

@pytest.mark.asyncio
async def test_a_clinic_with_no_admin_email_still_finds_its_agent(app_client):
    """The old lookup made a clinic's dashboard depend on a column that has
    nothing to do with agent ownership: blank the email and the clinic lost its
    agent entirely, despite the FK being intact."""
    tenant_id, agent_id = await _clinic_with_agent("No Email Clinic", None)

    r = await app_client.get("/agents/mine", headers=_clinic(tenant_id))
    assert r.status_code == 200, r.text
    assert r.json()["id"] == agent_id


@pytest.mark.asyncio
async def test_a_clinic_with_no_agent_gets_a_clear_404(app_client):
    async with db_module.AsyncSessionLocal() as s:
        t = Tenant(clinic_name="Agentless", admin_email="none@test.com",
                   admin_password=hash_password("pw"), language="en-IN",
                   ai_number=f"+91 90003 {next(_seq)}", is_active=True)
        s.add(t)
        await s.commit()
        tenant_id = t.id

    r = await app_client.get("/agents/mine", headers=_clinic(tenant_id))
    assert r.status_code == 404, r.text
    # The message must not name an email — that sent readers after the wrong thing.
    assert "@" not in r.json()["detail"]


@pytest.mark.asyncio
async def test_unauthenticated_gets_nothing(app_client):
    await _both(app_client)
    r = await app_client.get("/agents/mine?email=ainan@gmail.com")
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_multiple_agents_resolve_deterministically(app_client):
    """A clinic may have several agents; the pick must not be planner-dependent."""
    kmct_id, first_agent = await _clinic_with_agent(**KMCT)
    async with db_module.AsyncSessionLocal() as s:
        s.add(AgentConfig(tenant_id=kmct_id, agent_name="Second"))
        await s.commit()

    seen = {
        (await app_client.get("/agents/mine", headers=_clinic(kmct_id))).json()["id"]
        for _ in range(4)
    }
    assert seen == {first_agent}, f"unstable agent selection: {seen}"
