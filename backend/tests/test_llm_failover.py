"""
Tests for the shared Groq rate-limit failover (backend/services/llm_failover.py) and
its two consumers: the chat path (backend/routers/agent_test.py) and the VOICE path
(backend/agent/resilience.py).

The gap these cover
-------------------
The 2026-08-10 incident was fixed in the chat path only. The voice path picked its
model at call setup by probing ``GET /v1/models`` — which costs no tokens and so
answers 200 for a key whose daily budget is fully spent. Every voice call therefore
started on the exhausted model, hit 429 on the caller's first question, and answered
"I'm having a little trouble right now" for the rest of the call.

What is asserted here, in the order it matters:
  1. A BURST limit (RPM/TPM, refills in seconds) is waited out on the clinic's own
     model, not failed over — and a tokens-per-DAY exhaustion is never slept on.
  2. A model that returned 429 is benched, so the NEXT turn starts somewhere with
     budget instead of re-hitting the same wall (each rejected request still spends
     one of the 1,000/day RPD slots, and on voice it costs the caller a turn).
  3. On voice, a rate limit switches the live LLM's model and RE-ASKS the question,
     and only speaks the apology when no model is left.
  4. A 429 from the SPEECH vendor never triggers an LLM model switch.

Run: python -m pytest backend/tests/test_llm_failover.py -v
"""
import os

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_llm_failover.db"

from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from pipecat.frames.frames import (
    ErrorFrame,
    LLMRunFrame,
    LLMUpdateSettingsFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.groq.llm import GroqLLMService

import backend.db as db_mod
from backend.db import AsyncSessionLocal, Base, engine
from backend.models.agent_config import AgentConfig
from backend.models.tenant import Tenant
from backend.agent.resilience import ResilienceProcessor
from backend.routers import agent_test as chat_mod
from backend.services import llm_failover as F

TENANT_ID = "77777777-7777-7777-7777-777777777777"
AGENT_ID = "88888888-8888-8888-8888-888888888888"

PRIMARY = "llama-3.3-70b-versatile"
FALLBACK = "openai/gpt-oss-120b"

# Verbatim from the production log: a tokens-per-DAY exhaustion.
TPD_429 = (
    "Groq API error: 429 - Rate limit reached for model `llama-3.3-70b-versatile` in "
    "organization `org_01k9v83k2bf1zr2nc2pzp9y3ad` service tier `on_demand` on tokens "
    "per day (TPD): Limit 100000, Used 99463, Requested 1563. "
    "Please try again in 14m46.464s."
)

# A per-MINUTE token limit. Measured on the live key, TPM 12,000 refills at 200
# tokens/sec, so the provider's own hint for one request is a second or two.
TPM_429 = (
    "Groq API error: 429 - Rate limit reached for model `llama-3.3-70b-versatile` on "
    "tokens per minute (TPM): Limit 12000, Used 11800, Requested 1563. "
    "Please try again in 2.1s."
)

# VERBATIM from a real 429, forced from the production key on 2026-08-11 by bursting
# 40 one-token requests at llama-3.1-8b-instant (RPM 30). Kept exactly as the wire
# returned it — including the trailing upgrade link, which is the part that broke the
# classifier and which no authored test string had.
REAL_RPM_429 = (
    'Groq API error: 429 - {"error":{"message":"Rate limit reached for model '
    '`llama-3.1-8b-instant` in organization `org_01k9v83k2bf1zr2nc2pzp9y3ad` service '
    'tier `on_demand` on requests per minute (RPM): Limit 30, Used 30, Requested 1. '
    'Please try again in 2s. Need more tokens? Upgrade to Dev Tier today at '
    'https://console.groq.com/settings/billing","type":"requests",'
    '"code":"rate_limit_exceeded"}}'
)


@pytest.fixture(autouse=True)
def _clean_cooldowns():
    F.reset_cooldowns()
    yield
    F.reset_cooldowns()


@pytest_asyncio.fixture
async def seeded_db():
    assert db_mod.IS_SQLITE, "TEST SAFETY: refusing to run against a non-SQLite database"
    db_mod._import_all_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as s:
        s.add(Tenant(id=TENANT_ID, clinic_name="Failover Clinic", admin_email="fo@example.com"))
        s.add(AgentConfig(id=AGENT_ID, tenant_id=TENANT_ID, agent_name="Reception",
                          llm_provider="groq", llm_model=PRIMARY,
                          system_prompt="You are a receptionist."))
        await s.commit()
    chat_mod._conversation_history.clear()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ── 1. Burst vs daily budget ──────────────────────────────────────────────────


def test_a_daily_budget_is_never_treated_as_a_burst():
    """Sleeping through a TPD exhaustion is the "please wait a moment" lie."""
    assert F.is_rate_limit_error(TPD_429) is True
    assert F.is_burst_limit(TPD_429) is False


def test_a_short_wait_is_a_burst_and_is_slept_through():
    assert F.is_burst_limit(TPM_429) is True
    assert F.burst_sleep_seconds(TPM_429) == pytest.approx(2.35, abs=0.01)


def test_a_burst_sleep_is_capped_so_nobody_waits_on_the_phone():
    long_hint = "429 rate limit reached. Please try again in 45s"
    assert F.is_burst_limit(long_hint) is False, "45s is not a burst — fail over instead"
    # And even a hint just inside the burst window can't exceed the cap.
    assert F.burst_sleep_seconds("429 try again in 4.9s") <= F.BURST_RETRY_SLEEP_CAP_SECONDS


def test_a_429_with_no_hint_at_all_is_treated_as_recoverable():
    """Being wrong here costs one short pause before the real failover runs."""
    assert F.is_burst_limit("429 Too Many Requests") is True
    assert F.burst_sleep_seconds("429 Too Many Requests") == pytest.approx(1.0)


def test_the_real_wire_message_is_classified_from_its_wait_not_its_prose():
    """The bug this exists for: Groq appends "Upgrade to Dev Tier today at
    .../settings/billing" to EVERY rate-limit message, so keyword-matching
    "billing"/"today" made a 2-second RPM burst look like an exhausted daily budget.

    Real 429 text, captured from the live API rather than authored here — the whole
    point is that the real thing carries prose an invented string would not."""
    assert F.is_rate_limit_error(REAL_RPM_429) is True
    assert F.retry_after_seconds(REAL_RPM_429) == pytest.approx(2.0)
    assert F.is_burst_limit(REAL_RPM_429) is True, \
        "a 2-second requests-per-minute burst is recoverable, billing link or not"
    assert F.names_a_long_budget(REAL_RPM_429) is False


def test_the_patient_is_not_told_a_two_second_burst_is_a_daily_cap():
    """The user-visible half of the same bug: this message sent a patient away to
    phone the clinic over a limit that cleared before they finished reading it."""
    msg = chat_mod.describe_llm_failure(REAL_RPM_429)
    low = msg.lower()
    assert "today" not in low, "a 2s burst is not a daily cap"
    assert "call the clinic" not in low, "nobody should be sent away over 2 seconds"
    assert "2 seconds" in msg, "quote the provider's own wait"

    # And the genuine daily exhaustion must still be reported as one.
    daily = chat_mod.describe_llm_failure(TPD_429).lower()
    assert "limit for today" in daily or "usage limit" in daily
    assert "call the clinic" in daily


def test_an_unrelated_failure_is_not_a_rate_limit():
    assert F.is_burst_limit("connection reset by peer") is False
    assert F.is_rate_limit_error("Bad request: Requested 4291 tokens exceeds context") is False


# ── 2. The cooldown registry ──────────────────────────────────────────────────


def test_the_chain_excludes_the_model_that_fabricated_a_booking():
    """llama-3.1-8b-instant has 5x the daily budget of the primary and is STILL
    excluded: measured 1/3 on [ACTION:] tag emission, and its failure mode was
    telling the patient "your appointment is booked" with no tag — i.e. confirming
    a row that was never written."""
    assert "llama-3.1-8b-instant" not in F.GROQ_MODEL_CHAIN
    assert F.GROQ_FREE_TIER_TPD["llama-3.1-8b-instant"] > F.GROQ_FREE_TIER_TPD[PRIMARY]


def test_a_benched_model_is_skipped_until_its_cooldown_expires():
    assert F.preferred_model(PRIMARY) == (PRIMARY, None)

    F.mark_rate_limited(PRIMARY, TPD_429)
    assert F.is_cooling_down(PRIMARY) is True
    chosen, reason = F.preferred_model(PRIMARY)
    assert chosen == FALLBACK
    assert reason and "rate limited" in reason

    F.reset_cooldowns()
    assert F.preferred_model(PRIMARY) == (PRIMARY, None)


def test_the_provider_s_own_wait_hint_sets_the_cooldown():
    """886s is what Groq itself asked for ("14m46.464s") — a better number than any
    guess, because it is computed from the real refill rate."""
    applied = F.mark_rate_limited(PRIMARY, TPD_429)
    assert applied == pytest.approx(886.464, abs=0.01)
    assert F.cooldown_remaining(PRIMARY) > 800


def test_a_cooldown_cannot_bench_a_model_for_a_whole_day():
    applied = F.mark_rate_limited(PRIMARY, retry_after=99_999)
    assert applied == F.MAX_COOLDOWN_SECONDS


def test_everything_benched_still_tries_the_configured_model():
    """By then the cooldowns are stale guesses and the budget may have refilled;
    honouring the operator's choice beats refusing to try."""
    for m in F.GROQ_MODEL_CHAIN:
        F.mark_rate_limited(m, TPD_429)
    assert F.next_available_model(PRIMARY) is None
    assert F.preferred_model(PRIMARY) == (PRIMARY, None)


# ── 3. The chat path ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_burst_limit_retries_the_same_model_after_a_short_sleep(seeded_db):
    """The clinic's configured model is kept: the wait is shorter than the switch,
    and switching would spend a second model's daily budget for nothing."""
    slept: list[float] = []
    calls: list[str] = []

    async def fake_sleep(secs):
        slept.append(secs)

    async def dispatch(provider, api_key, system_prompt, history, model, max_tokens=None):
        calls.append(model)
        if len(calls) == 1:
            raise Exception(TPM_429)
        return "Sure — what time suits you?"

    with patch.object(chat_mod, "_dispatch_llm", side_effect=dispatch), \
         patch.object(chat_mod.asyncio, "sleep", fake_sleep), \
         patch.dict(os.environ, {"GROQ_API_KEY": "gk"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            reply = await chat_mod.generate_llm_response(
                agent, "book me in", db, session_id="s-burst", user_language="en-IN")

    assert reply == "Sure — what time suits you?"
    assert calls == [PRIMARY, PRIMARY], f"must retry the SAME model, got {calls}"
    assert len(slept) == 1 and slept[0] <= F.BURST_RETRY_SLEEP_CAP_SECONDS
    assert not F.is_cooling_down(PRIMARY), "a recovered burst must not bench the model"


@pytest.mark.asyncio
async def test_a_daily_budget_does_not_sleep_and_switches_model(seeded_db):
    slept: list[float] = []
    used: list[str] = []

    async def fake_sleep(secs):
        slept.append(secs)

    async def boom(*a, **kw):
        raise Exception(TPD_429)

    async def ok_groq(api_key, system_prompt, history, model, **kw):
        used.append(model)
        return "Thanks — what time would you like?"

    with patch.object(chat_mod, "_dispatch_llm", side_effect=boom), \
         patch.object(chat_mod, "call_groq", side_effect=ok_groq), \
         patch.object(chat_mod.asyncio, "sleep", fake_sleep), \
         patch.dict(os.environ, {"GROQ_API_KEY": "gk"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            reply = await chat_mod.generate_llm_response(
                agent, "book me in", db, session_id="s-tpd", user_language="en-IN")

    assert reply == "Thanks — what time would you like?"
    assert slept == [], "a daily budget must never be slept on"
    assert used == [FALLBACK]


@pytest.mark.asyncio
async def test_the_next_turn_starts_on_a_model_with_budget(seeded_db):
    """Without the cooldown, every turn in the ~15-minute window spends a request
    on the exhausted model before failing over."""
    F.mark_rate_limited(PRIMARY, TPD_429)
    dispatched: list[str] = []

    async def dispatch(provider, api_key, system_prompt, history, model, max_tokens=None):
        dispatched.append(model)
        return "What time works for you?"

    with patch.object(chat_mod, "_dispatch_llm", side_effect=dispatch), \
         patch.dict(os.environ, {"GROQ_API_KEY": "gk"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            await chat_mod.generate_llm_response(
                agent, "book me in", db, session_id="s-cooled", user_language="en-IN")

    assert dispatched == [FALLBACK], "the exhausted model must not be tried at all"


# ── 4. The voice path ─────────────────────────────────────────────────────────


class _FakeLLM:
    """Stands in for the built GroqLLMService, carrying the REAL Settings type so the
    delta this code constructs is validated against the schema pipecat will apply."""

    def __init__(self, model: str = PRIMARY):
        self._settings = GroqLLMService.Settings(model=model)


class _SpyTask:
    def __init__(self):
        self.frames = []

    async def queue_frames(self, frames):
        self.frames.extend(frames)

    @property
    def spoken(self):
        return [f.text for f in self.frames if isinstance(f, TTSSpeakFrame)]

    @property
    def model_updates(self):
        return [f.delta.model for f in self.frames if isinstance(f, LLMUpdateSettingsFrame)]

    @property
    def reruns(self):
        return [f for f in self.frames if isinstance(f, LLMRunFrame)]


async def _noop(*args, **kwargs):
    return None


def _proc(**kw):
    return ResilienceProcessor(
        language="en-IN", llm=_FakeLLM(), llm_provider="groq", llm_model=PRIMARY, **kw
    )


@pytest.mark.asyncio
async def test_voice_switches_model_and_re_asks_instead_of_apologising():
    """The caller asked a real question; they can still get a real answer."""
    proc = _proc()
    task = _SpyTask()
    proc.bind_task(task)

    with patch.object(proc, "push_frame", new=_noop):
        await proc.process_frame(ErrorFrame(error=TPD_429), FrameDirection.DOWNSTREAM)

    assert task.model_updates == [FALLBACK], "must move to a model with budget"
    assert len(task.reruns) == 1, "must re-run the turn on the new model"
    assert task.spoken == [], "no apology is owed when the turn is being recovered"
    # The settings frame is targeted at this service, not broadcast on the hope that
    # it is the only LLM in the pipeline.
    upd = [f for f in task.frames if isinstance(f, LLMUpdateSettingsFrame)][0]
    assert upd.service is proc._llm
    # And the ordering the recovery depends on: change the model, THEN re-ask.
    assert task.frames.index(upd) < task.frames.index(task.reruns[0])


@pytest.mark.asyncio
async def test_voice_speaks_rather_than_going_silent_once_no_model_is_left():
    for m in F.GROQ_MODEL_CHAIN:
        F.mark_rate_limited(m, TPD_429)

    proc = _proc()
    task = _SpyTask()
    proc.bind_task(task)
    with patch.object(proc, "push_frame", new=_noop):
        await proc.process_frame(ErrorFrame(error=TPD_429), FrameDirection.DOWNSTREAM)

    assert task.model_updates == []
    assert task.spoken == ["I'm having a little trouble right now, one moment please."]


@pytest.mark.asyncio
async def test_a_speech_vendor_rate_limit_does_not_switch_the_llm_model():
    """Sarvam throttling the VOICE cannot be fixed by changing the LLM's model —
    that would be a pointless change made at the worst possible moment."""
    proc = _proc()
    task = _SpyTask()
    proc.bind_task(task)
    with patch.object(proc, "push_frame", new=_noop):
        await proc.process_frame(
            ErrorFrame(error="Sarvam TTS error: 429 Too Many Requests"),
            FrameDirection.DOWNSTREAM,
        )

    assert task.model_updates == [], "the LLM's model is not the problem here"
    assert len(task.spoken) == 1


@pytest.mark.asyncio
async def test_a_non_rate_limit_error_still_just_speaks():
    proc = _proc()
    task = _SpyTask()
    proc.bind_task(task)
    with patch.object(proc, "push_frame", new=_noop):
        await proc.process_frame(ErrorFrame(error="connection reset"), FrameDirection.DOWNSTREAM)

    assert task.model_updates == []
    assert len(task.spoken) == 1


@pytest.mark.asyncio
async def test_model_switching_is_bounded_per_call():
    """A systematically failing chain must not walk the caller through every model
    on the account."""
    proc = _proc(max_model_switches=1)
    task = _SpyTask()
    proc.bind_task(task)
    with patch.object(proc, "push_frame", new=_noop):
        for _ in range(4):
            await proc.process_frame(ErrorFrame(error=TPD_429), FrameDirection.DOWNSTREAM)

    assert len(task.model_updates) == 1
    assert len(task.spoken) >= 1, "once switching stops, the caller must still hear something"


# ── 5. What the Model dropdown is told ────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_model_list_flags_models_not_verified_for_booking():
    """Groq's own `tools` flag says nothing about whether booking works here —
    booking is a regex FSM over the reply text, not function calling — so the
    dropdown carries OUR measurement instead. Getting this backwards would remove
    the warning from the model that fabricated a confirmation."""
    from backend.routers import platform as P

    async def fake_fetch(key, force=False):
        return [
            {"id": PRIMARY, "name": PRIMARY, "context_window": 131072,
             "max_completion_tokens": 32768, "reasoning": False, "supports_tools": True,
             "owned_by": "Meta"},
            {"id": "llama-3.1-8b-instant", "name": "llama-3.1-8b-instant",
             "context_window": 131072, "max_completion_tokens": 131072,
             "reasoning": False, "supports_tools": True, "owned_by": "Meta"},
        ]

    async def fake_key(provider, db, category=None):
        return "gk"

    # The router imports groq_catalog inside the handler, so the SOURCE module is
    # what has to be patched — it is resolved at call time.
    from backend.services import groq_catalog

    with patch.object(groq_catalog, "fetch_models", fake_fetch), \
         patch.object(P, "_get_raw_key", fake_key):
        out = await P.llm_models(refresh=False, user=None, db=None)

    by_id = {m["id"]: m for m in out["models"]}
    assert by_id[PRIMARY]["booking_verified"] is True
    assert by_id["llama-3.1-8b-instant"]["booking_verified"] is False, \
        "the model that confirmed a booking it never wrote must be flagged"
    # The per-model daily budget, which no Groq response header reports, is what
    # makes the choice legible: 5x the tokens, but unverified on booking.
    assert by_id["llama-3.1-8b-instant"]["daily_token_budget"] == 500_000
    assert by_id[PRIMARY]["daily_token_budget"] == 100_000
    assert out["provider"] == "groq"
    assert out["default"] == PRIMARY, "existing agents must default to their current model"


@pytest.mark.asyncio
async def test_the_old_two_argument_construction_still_speaks():
    """pipeline.py passes the LLM now, but the speak-only form must keep working —
    it is what the never-silence tests exercise."""
    proc = ResilienceProcessor(language="en-IN")
    task = _SpyTask()
    proc.bind_task(task)
    with patch.object(proc, "push_frame", new=_noop):
        await proc.process_frame(ErrorFrame(error=TPD_429), FrameDirection.DOWNSTREAM)

    assert task.model_updates == []
    assert len(task.spoken) == 1
