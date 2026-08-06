"""
Agent CONFIGURATION is read-only for clinic accounts — enforced server-side.

The clinic's My Agent page shows the agent card with no edit controls. That alone
is a UI affordance, not a permission: every write below used to take CurrentUser
and only check require_owns, so a clinic token could PATCH its own agent's
provider/model/prompt straight through the API and break its own live receptionist.

Equally important is what stays OPEN: the web-call token, the outbound test call
and the text test path are the "view and test" capability a clinic keeps. A fix
that locked those down would have broken the one interactive thing on the page.

Run: python -m pytest backend/tests/test_agent_config_readonly_for_clinics.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-agent-readonly")
os.environ.setdefault("ENVIRONMENT", "development")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import backend.db as db_module
from backend.models.agent_config import AgentConfig
from backend.models.tenant import Tenant
from backend.security import create_access_token, hash_password


@pytest_asyncio.fixture
async def app_client():
    from backend.db import Base, _import_all_models, engine
    from backend.main import app

    _import_all_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


_seq = iter(range(40_000, 99_999))


async def _clinic_with_agent() -> tuple[str, str]:
    async with db_module.AsyncSessionLocal() as s:
        t = Tenant(
            clinic_name="Readonly Clinic", admin_email="ro@test.com",
            admin_password=hash_password("pw"), language="ml-IN",
            ai_number=f"+91 90004 {next(_seq)}", is_active=True,
        )
        s.add(t)
        await s.flush()
        a = AgentConfig(
            tenant_id=t.id, agent_name="Receptionist", language="ml-IN",
            tts_provider="sarvam", tts_model="bulbul:v3", tts_voice="pooja",
            llm_provider="groq", llm_model="llama-3.3-70b-versatile",
            first_message="നമസ്കാരം", system_prompt="Be helpful.",
            status="CONFIGURED",
        )
        s.add(a)
        await s.commit()
        return t.id, a.id


def _clinic(tenant_id: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(tenant_id, 'clinic')}"}


def _super() -> dict:
    return {"Authorization": f"Bearer {create_access_token('superadmin', 'superadmin')}"}


async def _impersonation_headers(app_client, tenant_id: str) -> dict:
    r = await app_client.post(f"/admin/clinics/{tenant_id}/impersonate", headers=_super())
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ── Writes are refused ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_clinic_cannot_patch_its_own_agent(app_client):
    """The core gap: this used to return 200 and change the live voice pipeline."""
    tenant_id, agent_id = await _clinic_with_agent()

    r = await app_client.patch(
        f"/agents/{agent_id}",
        headers=_clinic(tenant_id),
        json={"tts_voice": "anushka", "first_message": "hacked greeting"},
    )
    assert r.status_code == 403, r.text
    assert "read-only" in r.json()["detail"].lower()

    # And nothing changed.
    async with db_module.AsyncSessionLocal() as s:
        agent = (await s.execute(select(AgentConfig).where(AgentConfig.id == agent_id))).scalar_one()
        assert agent.tts_voice == "pooja"
        assert agent.first_message == "നമസ്കാരം"


@pytest.mark.asyncio
async def test_every_agent_config_write_refuses_a_clinic_token(app_client):
    """One narrow fix would leave the others open, so all of them are covered."""
    tenant_id, agent_id = await _clinic_with_agent()
    h = _clinic(tenant_id)

    calls = [
        ("PATCH", f"/agents/{agent_id}", {"json": {"agent_name": "nope"}}),
        ("POST", f"/agents/{agent_id}/generate-system-prompt", {}),
        ("POST", f"/agents/{agent_id}/generate-first-message", {}),
        ("POST", f"/agents/{agent_id}/prompt-history/{agent_id}/revert", {}),
        ("DELETE", f"/agents/{agent_id}/avatar", {}),
        # Creating and deleting agents were already superadmin-only; assert it so a
        # dependency swap cannot quietly open them.
        ("POST", "/agents", {"json": {"clinic_name": "x", "template": "receptionist"}}),
        ("DELETE", f"/agents/{agent_id}", {}),
    ]
    for method, path, kw in calls:
        r = await app_client.request(method, path, headers=h, **kw)
        assert r.status_code == 403, f"{method} {path} was not refused: {r.status_code} {r.text[:120]}"


@pytest.mark.asyncio
async def test_avatar_upload_refuses_a_clinic_token(app_client):
    tenant_id, agent_id = await _clinic_with_agent()
    r = await app_client.post(
        f"/agents/{agent_id}/avatar",
        headers=_clinic(tenant_id),
        files={"file": ("a.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")},
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_an_impersonated_superadmin_is_also_read_only(app_client):
    """Impersonation exists to see what the CLINIC sees. It carries clinic
    authority, so it must not be a way around this either — a superadmin who wants
    to edit exits impersonation and uses the superadmin editor."""
    tenant_id, agent_id = await _clinic_with_agent()
    h = await _impersonation_headers(app_client, tenant_id)

    r = await app_client.patch(f"/agents/{agent_id}", headers=h, json={"tts_voice": "anushka"})
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_a_clinic_cannot_reach_another_clinics_agent_either(app_client):
    """403 regardless of whose agent it is, and regardless of whether it exists —
    so this endpoint cannot be used to probe for other clinics' agent ids."""
    tenant_a, _ = await _clinic_with_agent()
    _, agent_b = await _clinic_with_agent()

    r = await app_client.patch(f"/agents/{agent_b}", headers=_clinic(tenant_a), json={"tts_voice": "x"})
    assert r.status_code == 403, r.text

    r = await app_client.patch(
        "/agents/00000000-0000-0000-0000-000000000000",
        headers=_clinic(tenant_a), json={"tts_voice": "x"},
    )
    assert r.status_code == 403, "a missing agent must answer the same as a real one"


# ── Reads and the test-call flow stay open ───────────────────────────────────

@pytest.mark.asyncio
async def test_a_clinic_can_still_read_its_agent_and_stats(app_client):
    """Read-only means read — My Agent has to be able to render."""
    tenant_id, agent_id = await _clinic_with_agent()
    h = _clinic(tenant_id)

    r = await app_client.get("/agents/mine", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == agent_id
    assert body["tts_voice"] == "pooja"
    assert body["first_message"] == "നമസ്കാരം"
    # Still no platform credentials.
    assert "livekit_api_secret" not in body

    # The stats the card shows (Calls today / Avg latency).
    r = await app_client.get(f"/agents/{agent_id}/health", headers=h)
    assert r.status_code == 200, r.text

    # And the agent-detail read used by the test modal.
    r = await app_client.get(f"/agents/{agent_id}", headers=h)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_the_test_call_flow_is_not_locked_down(app_client):
    """Web Call / Phone Call are the one interactive capability a clinic keeps.

    Asserting on "not 403/404" rather than 200: both paths need LiveKit/SIP
    configured, which the test environment has not got, so they legitimately fail
    later (503/400/500). What matters here is that they are not refused as a
    PERMISSION problem — that is what would silently kill the buttons.
    """
    tenant_id, agent_id = await _clinic_with_agent()
    h = _clinic(tenant_id)

    r = await app_client.post(f"/agents/{agent_id}/web-call-token", headers=h, json={})
    assert r.status_code not in (403, 404), f"web call refused for a clinic: {r.status_code} {r.text[:160]}"

    r = await app_client.post(
        f"/agents/{agent_id}/outbound-call", headers=h, json={"phone_number": "+919876543210"}
    )
    assert r.status_code not in (403, 404), f"phone call refused for a clinic: {r.status_code} {r.text[:160]}"

    # The text test path too (used by the chat side of the modal).
    r = await app_client.post(f"/agents/{agent_id}/test", headers=h, json={"message": "hello"})
    assert r.status_code not in (403, 404), f"text test refused for a clinic: {r.status_code}"


@pytest.mark.asyncio
async def test_superadmin_can_still_edit(app_client):
    """The whole point is that config edits move to superadmin — not vanish.

    Asserts on agent_name rather than a provider/voice field: those go through
    normalize/repair on save (a voice that is not a real bulbul:v3 speaker is
    corrected, not stored), so they would test that logic rather than this one.
    """
    _, agent_id = await _clinic_with_agent()

    r = await app_client.patch(
        f"/agents/{agent_id}", headers=_super(), json={"agent_name": "Front Desk"}
    )
    assert r.status_code == 200, r.text

    async with db_module.AsyncSessionLocal() as s:
        agent = (await s.execute(select(AgentConfig).where(AgentConfig.id == agent_id))).scalar_one()
    assert agent.agent_name == "Front Desk"
