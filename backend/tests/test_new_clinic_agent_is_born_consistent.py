"""
Priority 4 — proof that the language/provider fix is STRUCTURAL, not a patch
applied to the two agents that happened to exist.

This goes through ``POST /agents`` — the exact endpoint the Create Agent wizard
calls, with the exact payload shape it now sends — creating a BRAND NEW clinic and
its first agent in one request, then asserting the resulting row is already
correct with zero post-creation correction.

What this would have caught before the fix
------------------------------------------
The wizard's own defaults created broken agents:

  * ``llm_provider='openai'`` / ``llm_model='gpt-4o'`` while no ``OPENAI_API_KEY``
    is configured anywhere — every new agent was born with a dead LLM.
  * ``stt_language='en-IN'`` and ``tts_language='hi-IN'`` as separate defaults, so
    a brand-new agent started life already listening in one language and speaking
    another — the four-way mismatch, present from creation.
  * the wizard sent ``llm_provider:'gemini'`` hardcoded next to whatever model was
    picked from its Groq-inclusive model list, so provider and model could
    disagree on a fresh agent.

Run: python -m pytest backend/tests/test_new_clinic_agent_is_born_consistent.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-new-clinic-tests")
os.environ.setdefault("ENVIRONMENT", "development")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import backend.db as db_module
from backend.models.agent_config import AgentConfig
from backend.models.tenant import Tenant
from backend.security import create_access_token
from backend.services import agent_defaults


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


def _super():
    return {"Authorization": f"Bearer {create_access_token('superadmin', 'superadmin')}"}


_seq = iter(range(1000, 9999))


def _wizard_payload(language: str, voice: str = "shruti"):
    """Exactly what CreateAgent.tsx now sends: one language, one voice, and NO
    provider or model fields at all."""
    n = next(_seq)
    return {
        "clinic_selection": "new",
        "new_clinic": {
            "clinic_name": f"Brand New Clinic {n}",
            "admin_name": "New Admin",
            "admin_email": f"newclinic{n}@test.com",
            "phone": "+91 90000 00000",
            "location": "Kochi",
            "language": language,
        },
        "agent_name": "Receptionist",
        "template": "clinic_receptionist",
        "first_message": "",
        "system_prompt": "",
        "language": language,
        "tts_voice": voice,
        "tts_pitch": 0, "tts_pace": 1.0, "tts_loudness": 1.0,
        "llm_temperature": 0.3, "max_response_tokens": 150,
        "telephony_option": "skip",
    }


async def _agent_row(agent_id):
    async with db_module.AsyncSessionLocal() as s:
        return (await s.execute(
            select(AgentConfig).where(AgentConfig.id == agent_id)
        )).scalar_one()


@pytest.mark.asyncio
async def test_a_brand_new_malayalam_clinic_and_agent_are_born_consistent(app_client):
    """The headline case: a fresh clinic + fresh agent in Malayalam, correct from
    the moment of creation with no developer touching the row."""
    r = await app_client.post("/agents", headers=_super(), json=_wizard_payload("ml-IN"))
    assert r.status_code == 201, r.text
    agent_id = r.json()["agent_id"]

    row = await _agent_row(agent_id)

    # ── ONE consistent language, everywhere ───────────────────────────────────
    assert row.language == "ml-IN"
    assert row.tts_language == "ml-IN"
    # auto_detect_language defaults True, so the STT mirror is the detect sentinel.
    # It is the ONLY value allowed to differ, and only ever as "auto".
    assert row.stt_language in ("ml-IN", "auto")

    # ── Locked defaults applied automatically ─────────────────────────────────
    assert (row.llm_provider, row.llm_model) == (
        agent_defaults.LOCKED_LLM_PROVIDER, agent_defaults.LOCKED_LLM_MODEL)
    assert (row.stt_provider, row.stt_model) == (
        agent_defaults.LOCKED_STT_PROVIDER, agent_defaults.LOCKED_STT_MODEL)
    assert (row.tts_provider, row.tts_model) == (
        agent_defaults.LOCKED_TTS_PROVIDER, agent_defaults.LOCKED_TTS_MODEL)

    # ── The voice the admin chose is respected ────────────────────────────────
    assert row.tts_voice == "shruti"


@pytest.mark.asyncio
async def test_the_wizards_old_provider_model_fields_cannot_poison_a_new_agent(app_client):
    """A stale frontend build (or a scripted call) sending the OLD payload — with
    the dead OpenAI LLM pair and two separate languages — must still produce a
    consistent agent. This is the mid-deploy safety case.

    Note the two fields are handled DIFFERENTLY on purpose, because the two
    mistakes are different:

    * The LLM pair and the split languages are silently IGNORED. They are derived
      or locked, the editor auto-saves on a debounce, and an in-flight older build
      sends them on every keystroke — 422ing those would break saving mid-deploy.
    * A non-selectable STT/TTS provider is REJECTED with a 422. That is a real
      operator choice being made, not a stale field being echoed, so accepting it
      silently would put the agent on a provider the editor cannot even display.
    """
    payload = _wizard_payload("ml-IN")
    payload.update({
        "llm_provider": "openai", "llm_model": "gpt-4o",
        "stt_provider": "sarvam", "stt_model": "saaras:v3", "stt_language": "en-IN",
        "tts_language": "hi-IN",
    })

    r = await app_client.post("/agents", headers=_super(), json=payload)
    assert r.status_code == 201, r.text
    row = await _agent_row(r.json()["agent_id"])

    # Derived / locked fields: the payload's values were ignored.
    assert row.language == "ml-IN"
    assert row.tts_language == "ml-IN"
    assert row.llm_provider == agent_defaults.LOCKED_LLM_PROVIDER
    assert row.llm_model == agent_defaults.LOCKED_LLM_MODEL

    # Selected STT provider/model: HONOURED, because both are selectable. This is
    # the reversal — these used to be overwritten with the locked pair, which threw
    # away a deliberate choice on every save.
    assert row.stt_provider == "sarvam"
    assert row.stt_model == "saaras:v3"


@pytest.mark.asyncio
async def test_a_provider_that_is_not_selectable_is_rejected_not_silently_swapped(app_client):
    """ElevenLabs has a key in .env and a working pipeline branch, but it was removed
    from the dropdowns. A request naming it must fail loudly.

    Silently substituting Sarvam would be worse than either accepting or rejecting:
    the caller would believe they had configured ElevenLabs, and nothing would ever
    tell them otherwise."""
    payload = _wizard_payload("ml-IN")
    payload.update({"tts_provider": "elevenlabs", "tts_model": "eleven_turbo_v2"})

    r = await app_client.post("/agents", headers=_super(), json=payload)
    assert r.status_code == 422, r.text
    assert "not a selectable TTS provider" in r.text
    # The error names what IS available, so the fix is obvious from the message.
    assert "sarvam" in r.text


@pytest.mark.asyncio
async def test_every_supported_language_produces_a_consistent_new_agent(app_client):
    """Not just Malayalam. Any language a clinic can pick must yield a consistent
    agent — this is what makes the fix platform-level rather than per-language."""
    for opt in agent_defaults.supported_languages(agent_defaults.DEFAULT_TTS_PROVIDER):
        code = opt["code"]
        r = await app_client.post("/agents", headers=_super(), json=_wizard_payload(code))
        assert r.status_code == 201, f"{code}: {r.text}"
        row = await _agent_row(r.json()["agent_id"])

        assert row.language == code, f"{code} became {row.language}"
        assert row.tts_language == code
        assert row.stt_language in (code, "auto")
        assert row.llm_model == agent_defaults.LOCKED_LLM_MODEL


@pytest.mark.asyncio
async def test_a_new_agent_never_inherits_a_previous_agents_language(app_client):
    """Explicit guard from the brief: a brand new clinic/agent must not pick up a
    stale cached value from a previously created one."""
    first = await app_client.post("/agents", headers=_super(), json=_wizard_payload("ml-IN"))
    assert first.status_code == 201, first.text

    second = await app_client.post("/agents", headers=_super(), json=_wizard_payload("kn-IN"))
    assert second.status_code == 201, second.text

    a = await _agent_row(first.json()["agent_id"])
    b = await _agent_row(second.json()["agent_id"])
    assert a.language == "ml-IN"
    assert b.language == "kn-IN"      # not ml-IN
    assert b.tts_language == "kn-IN"


@pytest.mark.asyncio
async def test_the_new_clinics_own_language_seeds_its_first_agent(app_client):
    """Step 1 of the wizard collects the clinic's primary language. If the agent
    payload omits `language`, that is what the agent should be born with — rather
    than a hardcoded hi-IN, which is what used to happen."""
    payload = _wizard_payload("ml-IN")
    del payload["language"]                    # only the clinic states a language
    payload["new_clinic"]["language"] = "kn-IN"

    r = await app_client.post("/agents", headers=_super(), json=payload)
    assert r.status_code == 201, r.text
    row = await _agent_row(r.json()["agent_id"])
    assert row.language == "kn-IN"
    assert row.tts_language == "kn-IN"


@pytest.mark.asyncio
async def test_an_unsupported_language_is_refused_at_creation(app_client):
    """Better to refuse than to create an agent that cannot speak."""
    r = await app_client.post("/agents", headers=_super(), json=_wizard_payload("xx-XX"))
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_the_new_clinic_row_itself_is_created(app_client):
    """Sanity: the clinic really was created in the same request, so this is the
    brand-new-clinic path and not an existing-tenant one."""
    payload = _wizard_payload("ml-IN")
    r = await app_client.post("/agents", headers=_super(), json=payload)
    assert r.status_code == 201, r.text

    async with db_module.AsyncSessionLocal() as s:
        tenant = (await s.execute(
            select(Tenant).where(Tenant.id == r.json()["tenant_id"])
        )).scalar_one()
    assert tenant.clinic_name == payload["new_clinic"]["clinic_name"]
