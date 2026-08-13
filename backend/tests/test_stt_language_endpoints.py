"""
End-to-end tests for the ONE-LANGUAGE contract, through the real FastAPI app
against an in-memory SQLite DB.

What this file used to test, and why it changed
-----------------------------------------------
It used to test the ``stt_language`` PATCH contract: that a transcriber language
was validated against a separately chosen STT provider/model before being stored.
That contract has been REMOVED, because it was itself the bug. Being able to save
a transcriber language independently of the voice language is exactly what let
agent ``f367e0e2-4e31-41fd-8a4a-df0f6ebbd8d7`` end up with
``stt_language='ta-IN'`` and ``tts_language='ml-IN'`` — transcribing the caller as
Tamil while answering in Malayalam, and showing four disagreeing languages in the
UI at once.

So the tests below assert the replacement contract instead:

  * ``language`` is the only writable language field.
  * ``stt_language`` / ``tts_language`` are DERIVED mirrors that cannot be written
    to and cannot disagree.
  * the LLM provider/model pair is LOCKED and cannot be written to.
  * the STT and TTS provider/model pairs ARE writable, but only to a provider on
    the selectable whitelist, and an incoherent pair is repaired rather than stored.
  * the voice/speaker choice is still freely writable (explicitly preserved).

The original production incident is still covered — see
``test_a_description_label_can_never_reach_the_language_column``.

Run: python -m pytest backend/tests/test_stt_language_endpoints.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-stt-language-tests")
os.environ.setdefault("ENVIRONMENT", "development")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import backend.db as db_module
from backend.models.agent_config import AgentConfig
from backend.models.tenant import Tenant
from backend.security import create_access_token, hash_password
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


_seq = iter(range(10_000, 99_999))


async def _make_agent(**overrides):
    """Create an agent row DIRECTLY, bypassing the API.

    Deliberately direct: several tests need to plant a legacy row whose columns
    disagree — a state the API can no longer produce — and then prove the API
    heals it.
    """
    fields = {
        "agent_name": "Receptionist",
        "stt_provider": "deepgram",
        "stt_model": "nova-3",
        "stt_language": "en-IN",
        "tts_language": "en-IN",
        "language": "en-IN",
        **overrides,
    }
    async with db_module.AsyncSessionLocal() as s:
        t = Tenant(
            clinic_name="STT Clinic", admin_email=f"stt{next(_seq)}@test.com",
            admin_password=hash_password("pw"), language="en-IN",
            ai_number=f"+91 90001 {next(_seq)}", is_active=True,
        )
        s.add(t)
        await s.flush()
        a = AgentConfig(tenant_id=t.id, **fields)
        s.add(a)
        await s.commit()
        return str(t.id), str(a.id)


def _super():
    return {"Authorization": f"Bearer {create_access_token('superadmin', 'superadmin')}"}


async def _row(agent_id):
    async with db_module.AsyncSessionLocal() as s:
        return (await s.execute(
            select(AgentConfig).where(AgentConfig.id == agent_id)
        )).scalar_one()


# ── The one language is the only writable one ────────────────────────────────
@pytest.mark.asyncio
async def test_setting_language_updates_every_derived_mirror_at_once(app_client):
    """The whole point: ONE write, and nothing can disagree afterwards."""
    _, agent_id = await _make_agent(auto_detect_language=False)

    r = await app_client.patch(
        f"/agents/{agent_id}", headers=_super(), json={"language": "ml-IN"},
    )
    assert r.status_code == 200, r.text

    row = await _row(agent_id)
    assert row.language == "ml-IN"
    assert row.tts_language == "ml-IN"      # derived mirror
    assert row.stt_language == "ml-IN"      # derived mirror (pinned, not auto)


@pytest.mark.asyncio
async def test_auto_detect_is_the_only_thing_that_makes_stt_differ(app_client):
    """stt_language may hold "auto" — that is a detection MODE, not a language.

    It is the one legitimate reason the STT mirror differs from `language`, and it
    is driven by the pre-existing auto_detect_language boolean rather than by a
    second language field.
    """
    _, agent_id = await _make_agent(auto_detect_language=True)

    r = await app_client.patch(
        f"/agents/{agent_id}", headers=_super(), json={"language": "ml-IN"},
    )
    assert r.status_code == 200, r.text

    row = await _row(agent_id)
    assert row.language == "ml-IN"
    assert row.tts_language == "ml-IN"   # TTS can never be "auto" — it must speak
    assert row.stt_language == "auto"


@pytest.mark.asyncio
async def test_every_supported_language_actually_saves(app_client):
    """Nothing the UI offers may be unsaveable. This is the generalised form of
    the original truncation incident: the dropdown offered a value the column
    could not hold."""
    _, agent_id = await _make_agent(auto_detect_language=False)

    for opt in agent_defaults.supported_languages():
        r = await app_client.patch(
            f"/agents/{agent_id}", headers=_super(), json={"language": opt["code"]},
        )
        assert r.status_code == 200, f"{opt['code']} failed: {r.text}"
        row = await _row(agent_id)
        assert row.language == opt["code"]
        assert row.tts_language == opt["code"]
        assert row.stt_language == opt["code"]


@pytest.mark.asyncio
async def test_malayalam_is_selectable_even_though_deepgram_cannot_hear_it(app_client):
    """Deepgram (the LOCKED STT provider) supports no Malayalam on any tier, but
    Malayalam must still be a fully selectable agent language — the pipeline
    routes it to Sarvam STT automatically. Locking a provider must remove a
    CHOICE, never a capability.

    This is the stakeholder's "voice dropdown doesn't show malayalam" complaint,
    pinned as a test.
    """
    _, agent_id = await _make_agent()

    for code in ("ml-IN", "pa-IN", "od-IN"):
        r = await app_client.patch(
            f"/agents/{agent_id}", headers=_super(), json={"language": code},
        )
        assert r.status_code == 200, f"{code} was rejected: {r.text}"
        assert (await _row(agent_id)).language == code


# ── The original production incident ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_description_label_can_never_reach_the_language_column(app_client):
    """The 37-character label that caused

        StringDataRightTruncationError: value too long for varchar(20)

    must fail as a clean 422, not a 500. It is no longer offered by any dropdown,
    but a stale browser tab or a scripted call could still send it.
    """
    _, agent_id = await _make_agent()
    r = await app_client.patch(
        f"/agents/{agent_id}", headers=_super(),
        json={"language": "Multilingual (English/Hindi/Regional)"},
    )
    assert r.status_code == 422, r.text
    assert (await _row(agent_id)).language == "en-IN"  # unchanged


@pytest.mark.asyncio
async def test_the_exact_original_failing_save_no_longer_errors(app_client):
    """The incident precisely as it happened, replayed.

    The save that failed in production was the STT Language dropdown writing its own
    description label to ``stt_language`` — not ``language``, which did not exist
    yet. That column is varchar(20) and the label is 37 characters, so asyncpg
    raised StringDataRightTruncationError and the dashboard could only say "failed
    to save".

    It cannot happen now for a structural reason rather than a validation one:
    ``stt_language`` is a derived mirror, so the value is dropped before it can
    reach the column at all. Asserting the SAVE SUCCEEDS (200, not 422) is the
    point — the operator's other edits in the same request must still land.
    """
    # auto_detect_language=False so the mirror holds the LANGUAGE itself rather than
    # "auto" — that makes the assertion below about the label actually being
    # replaced by the derived value, not merely about it being absent.
    _, agent_id = await _make_agent(auto_detect_language=False)

    r = await app_client.patch(
        f"/agents/{agent_id}", headers=_super(),
        json={
            "stt_language": "Multilingual (English/Hindi/Regional)",
            # A real edit riding along in the same request, as the auto-saving
            # editor would send it.
            "agent_name": "Front Desk",
        },
    )
    assert r.status_code == 200, r.text

    row = await _row(agent_id)
    assert row.agent_name == "Front Desk"          # the real edit landed
    assert row.stt_language == "en-IN"             # the label never got near it
    assert len(row.stt_language) <= 20             # and the column is still narrow


@pytest.mark.asyncio
async def test_an_unsupported_language_is_refused_with_the_real_list(app_client):
    _, agent_id = await _make_agent()
    r = await app_client.patch(
        f"/agents/{agent_id}", headers=_super(), json={"language": "sat-IN"},
    )
    assert r.status_code == 422, r.text
    # The error must name codes the caller can actually pick.
    assert "ml-IN" in r.text and "hi-IN" in r.text


# ── Derived fields are not writable ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_client_supplied_language_mirrors_are_ignored_not_honoured(app_client):
    """The four-way-mismatch mechanism, pinned.

    A client sending stt_language/tts_language directly must not be able to drive
    them apart. Silently ignored rather than rejected, because the editor
    auto-saves and an older in-flight build still sends these.
    """
    _, agent_id = await _make_agent(auto_detect_language=False)

    r = await app_client.patch(
        f"/agents/{agent_id}", headers=_super(),
        json={"language": "ml-IN", "stt_language": "ta-IN", "tts_language": "hi-IN"},
    )
    assert r.status_code == 200, r.text

    row = await _row(agent_id)
    assert row.language == "ml-IN"
    assert row.stt_language == "ml-IN"   # NOT ta-IN
    assert row.tts_language == "ml-IN"   # NOT hi-IN


@pytest.mark.asyncio
async def test_a_dead_model_is_refused_against_its_own_provider(app_client, monkeypatch):
    """The LLM PROVIDER was unlocked on 2026-08-13 so Gemini can be chosen. The
    model half is still refused loudly when the vendor says it does not exist.

    The check must run against the provider the model is being saved WITH. Asking
    Groq about a Gemini id would return DEAD for every Gemini model and refuse
    every legitimate save with a message naming the wrong vendor's models.

    ``gemini-2.5-flash-8b`` is the id from the real incident — it sat next to
    ``llm_provider='groq'`` on a live agent and 404'd every call.
    """
    from backend.services import gemini_catalog

    _, agent_id = await _make_agent()

    async def _dead(_key, _model):
        return gemini_catalog.DEAD

    monkeypatch.setattr(gemini_catalog, "check_model", _dead)

    r = await app_client.patch(
        f"/agents/{agent_id}", headers=_super(),
        json={"llm_provider": "gemini", "llm_model": "gemini-2.5-flash-8b"},
    )
    assert r.status_code == 422, r.text
    assert "gemini-2.5-flash-8b" in r.json()["detail"]
    assert "gemini" in r.json()["detail"], "the error named the wrong vendor"

    row = await _row(agent_id)
    assert row.llm_model != "gemini-2.5-flash-8b"


@pytest.mark.asyncio
async def test_an_unselectable_llm_provider_is_still_refused(app_client):
    """Unlocking the provider widened the whitelist; it did not remove it.

    While the provider was locked this value was silently discarded, so the
    registry's own guard never ran on it. Now that the field is honoured, that
    guard is what stands between a typo and an agent that cannot be constructed —
    which on this product means dead air on a live call, not an error page.

    Refused with a 422 naming the real reason (no SDK in the agent worker), NOT
    normalised to a working provider: a silent swap is how a deliberate choice
    became a different vendor without anyone being told.
    """
    _, agent_id = await _make_agent()

    r = await app_client.patch(
        f"/agents/{agent_id}", headers=_super(), json={"llm_provider": "anthropic"},
    )
    assert r.status_code == 422, r.text
    assert "anthropic" in r.json()["detail"]

    row = await _row(agent_id)
    assert row.llm_provider in agent_defaults.SELECTABLE_LLM_PROVIDERS
    assert row.llm_provider != "anthropic"


@pytest.mark.asyncio
async def test_switching_provider_does_not_keep_the_old_vendors_model(app_client):
    """A model is meaningless without its provider.

    Unlocking the provider re-opened the 404 pair from the opposite direction:
    choose Gemini, send no model, and the row would keep its Llama id. The model
    must move to the new provider's default.
    """
    _, agent_id = await _make_agent(llm_model=agent_defaults.DEFAULT_LLM_MODEL)

    r = await app_client.patch(
        f"/agents/{agent_id}", headers=_super(), json={"llm_provider": "gemini"},
    )
    assert r.status_code == 200, r.text

    row = await _row(agent_id)
    assert row.llm_provider == "gemini"
    assert row.llm_model == agent_defaults.DEFAULT_LLM_MODEL_BY_PROVIDER["gemini"]
    assert not row.llm_model.startswith("llama"), (
        "the Groq model survived a move to Gemini — this is the 404 pair"
    )


@pytest.mark.asyncio
async def test_a_groq_outage_does_not_make_agents_read_only(app_client, monkeypatch):
    """When Groq cannot be reached we cannot tell a live model from a dead one — and
    that must NOT fail the save.

    The alternative was a 503, which reads as safe and is not: it means one upstream
    outage makes every agent on the platform uneditable, so nobody can fix a prompt
    or a phone number until Groq comes back. Refusing is reserved for Groq positively
    telling us the model does not exist.

    The unverifiable model is dropped, so the row keeps the model it already had —
    which was verified live at the time it was set.
    """
    from backend.services import groq_catalog

    _, agent_id = await _make_agent(llm_model=agent_defaults.DEFAULT_LLM_MODEL)

    async def _down(*a, **kw):
        raise groq_catalog.GroqModelsUnavailable("simulated outage")

    # Both halves of the lookup must be dark: an empty cache so there is no hit to
    # short-circuit on, and a fetch that fails so the escalation cannot rescue it.
    monkeypatch.setattr(groq_catalog, "fetch_models", _down)
    monkeypatch.setitem(groq_catalog._cache, "models", None)

    r = await app_client.patch(
        f"/agents/{agent_id}", headers=_super(),
        json={"agent_name": "Front Desk", "llm_model": "some-unverifiable-model"},
    )
    assert r.status_code == 200, r.text

    row = await _row(agent_id)
    # The rest of the request landed...
    assert row.agent_name == "Front Desk"
    # ...the unverifiable model did not, and the working one was left alone.
    assert row.llm_model == agent_defaults.DEFAULT_LLM_MODEL


@pytest.mark.asyncio
async def test_a_selectable_provider_sent_alone_is_honoured(app_client):
    """The reversal of the old lock, asserted directly: sending only
    ``llm_provider`` used to be silently discarded, and now it is a real choice.

    It still must not 422 — the editor auto-saves on a debounce, so an older
    in-flight build sending the field on every keystroke must not break saving
    mid-deploy."""
    _, agent_id = await _make_agent()

    r = await app_client.patch(
        f"/agents/{agent_id}", headers=_super(), json={"llm_provider": "gemini"},
    )
    assert r.status_code == 200, r.text
    assert (await _row(agent_id)).llm_provider == "gemini"


@pytest.mark.asyncio
async def test_a_selectable_stt_pair_is_honoured_not_overwritten(app_client):
    """The reversal, asserted directly. These used to be overwritten with the locked
    pair on every save, which silently discarded the operator's choice — and the
    choice matters: Sarvam is the only selectable transcriber that can hear
    Malayalam."""
    _, agent_id = await _make_agent()

    r = await app_client.patch(
        f"/agents/{agent_id}", headers=_super(),
        json={"stt_provider": "sarvam", "stt_model": "saarika:v2.5"},
    )
    assert r.status_code == 200, r.text

    row = await _row(agent_id)
    assert (row.stt_provider, row.stt_model) == ("sarvam", "saarika:v2.5")


@pytest.mark.asyncio
async def test_a_non_selectable_provider_is_rejected(app_client):
    """ElevenLabs is buildable and has a key, but was removed from the dropdowns.
    Rejected rather than silently swapped for Sarvam, so nobody ends up believing
    they configured a provider they did not."""
    _, agent_id = await _make_agent()

    r = await app_client.patch(
        f"/agents/{agent_id}", headers=_super(),
        json={"tts_provider": "elevenlabs", "tts_model": "eleven_turbo_v2"},
    )
    assert r.status_code == 422, r.text
    assert "not a selectable TTS provider" in r.text

    # And the row is untouched — a rejected save must change nothing at all.
    row = await _row(agent_id)
    assert row.tts_provider == agent_defaults.DEFAULT_TTS_PROVIDER


@pytest.mark.asyncio
async def test_switching_stt_provider_repairs_the_model_rather_than_pairing_badly(app_client):
    """Changing provider without a model must not leave the other vendor's model
    behind. `deepgram` + `saaras:v3` is the same class of invalid pair as
    `groq` + `gemini-2.5-flash-8b`, which is what broke a live agent's LLM."""
    _, agent_id = await _make_agent()

    # Land on Sarvam first, then switch provider and ask for its default model.
    await app_client.patch(
        f"/agents/{agent_id}", headers=_super(),
        json={"stt_provider": "sarvam", "stt_model": "saaras:v3"},
    )
    r = await app_client.patch(
        f"/agents/{agent_id}", headers=_super(),
        json={"stt_provider": "deepgram", "stt_model": ""},
    )
    assert r.status_code == 200, r.text

    row = await _row(agent_id)
    assert row.stt_provider == "deepgram"
    assert row.stt_model in agent_defaults.models_for("stt", "deepgram")
    assert row.stt_model != "saaras:v3"


@pytest.mark.asyncio
async def test_the_voice_choice_is_still_freely_editable(app_client):
    """Explicitly preserved by the stakeholder: "let it be there. no problem."
    Only the provider/model selectors were removed, never the voice/speaker."""
    _, agent_id = await _make_agent()

    r = await app_client.patch(
        f"/agents/{agent_id}", headers=_super(), json={"tts_voice": "shruti"},
    )
    assert r.status_code == 200, r.text
    assert (await _row(agent_id)).tts_voice == "shruti"


# ── The migration's conflict-resolution rule ─────────────────────────────────
# Tested against the resolver directly rather than through the API, because the
# state it resolves — `language` unset while the two legacy mirrors disagree — is
# unrepresentable in the post-migration schema (the column is NOT NULL). The
# Alembic migration and the API both call this same function, so testing it here
# covers both.
def test_the_kmct_conflict_resolves_to_the_documented_winner():
    """The exact stored state behind the stakeholder's screenshot."""
    language, conflicting = agent_defaults.resolve_language(
        tts_language="ml-IN", stt_language="ta-IN",
    )
    # tts_language wins: it is what the header and the "LANGUAGE" field displayed,
    # so it is the best record of configured intent — and the stakeholder's own
    # complaint was that Malayalam was missing, which corroborates it.
    assert language == "ml-IN"
    # ...and the row is flagged ambiguous, which is what sends it to auto-detect
    # instead of hard-pinning the microphone to the value that lost the tie-break.
    assert conflicting is True


def test_agreeing_columns_are_not_flagged_as_conflicting():
    assert agent_defaults.resolve_language(
        tts_language="kn-IN", stt_language="kn-IN",
    ) == ("kn-IN", False)


def test_auto_is_a_mode_not_a_language_so_it_never_wins():
    assert agent_defaults.resolve_language(
        tts_language="ml-IN", stt_language="auto",
    ) == ("ml-IN", False)
    # ...and when TTS is the one that is unset, STT still supplies a real language.
    assert agent_defaults.resolve_language(
        tts_language=None, stt_language="ta-IN",
    ) == ("ta-IN", False)


def test_a_row_with_nothing_set_falls_back_to_the_default():
    assert agent_defaults.resolve_language() == (agent_defaults.DEFAULT_LANGUAGE, False)


def test_an_already_canonical_language_is_never_overridden_by_a_stale_mirror():
    """After the migration, `language` is authoritative. If an older backend
    revision still in flight writes tts_language directly, that write must be
    DISCARDED, not promoted back over the canonical value."""
    assert agent_defaults.resolve_language(
        language="hi-IN", tts_language="ml-IN", stt_language="ta-IN",
    ) == ("hi-IN", False)


# ── Self-healing through the API ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_drifted_mirrors_and_a_broken_provider_pair_heal_on_the_next_save(app_client):
    """A row whose mirrors have drifted from `language` — e.g. written by an older
    backend revision mid-deploy — is corrected the first time anything is saved,
    without the caller having to mention language at all."""
    _, agent_id = await _make_agent(
        language="ml-IN",
        # Drifted mirrors + the broken groq+gemini pair the kmct row really had.
        stt_language="ta-IN", tts_language="hi-IN",
        auto_detect_language=False,
        llm_provider="groq", llm_model="gemini-2.5-flash-8b",
    )

    # Touch something completely unrelated to language.
    r = await app_client.patch(
        f"/agents/{agent_id}", headers=_super(), json={"agent_name": "Receptionist"},
    )
    assert r.status_code == 200, r.text

    row = await _row(agent_id)
    assert row.language == "ml-IN"
    assert row.tts_language == "ml-IN"   # re-derived, no longer hi-IN
    assert row.stt_language == "ml-IN"   # re-derived, no longer ta-IN
    # And the model Groq answers 404 for is gone.
    assert row.llm_model == agent_defaults.LOCKED_LLM_MODEL
    assert row.llm_provider == agent_defaults.LOCKED_LLM_PROVIDER
