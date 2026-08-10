"""
Tests for how an LLM rate limit / exhausted quota is reported and recovered
from in the chat path.

Production incident (2026-08-10, Indiana Hospital Mangalore): a patient gave
their phone number to finish a booking and got back "I'm currently receiving
too many requests. Please wait a moment before speaking again." The real error
was Groq's tokens-per-DAY budget being exhausted:

    Groq API error: 429 - Rate limit reached for model `llama-3.3-70b-versatile`
    ... on tokens per day (TPD): Limit 100000, Used 99463, Requested 1563.
    Please try again in 14m46.464s.

So the message was wrong twice over — it was not a burst of requests, and
"wait a moment" could not help. Worse, 429 was excluded from the provider
fallback list, so the booking dead-ended instead of failing over.

Run: python -m pytest backend/tests/test_llm_rate_limit_handling.py -v
"""
import os

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_llm_rate_limit.db"

from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select

import backend.db as db_mod
from backend.db import AsyncSessionLocal, Base, engine
from backend.models.agent_config import AgentConfig
from backend.models.tenant import Tenant
from backend.routers import agent_test as chat_mod

TENANT_ID = "55555555-5555-5555-5555-555555555555"
AGENT_ID = "66666666-6666-6666-6666-666666666666"

# Verbatim from the production log.
GROQ_TPD_ERROR = (
    "Groq API error: 429 - {\"error\":{\"message\":\"Rate limit reached for model "
    "`llama-3.3-70b-versatile` in organization `org_01k9v83k2bf1zr2nc2pzp9y3ad` service tier "
    "`on_demand` on tokens per day (TPD): Limit 100000, Used 99463, Requested 1563. "
    "Please try again in 14m46.464s.\"}}"
)


@pytest_asyncio.fixture
async def seeded_db():
    assert db_mod.IS_SQLITE, "TEST SAFETY: refusing to run against a non-SQLite database"
    db_mod._import_all_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as s:
        s.add(Tenant(id=TENANT_ID, clinic_name="Rate Limit Clinic", admin_email="rl@example.com"))
        s.add(AgentConfig(id=AGENT_ID, tenant_id=TENANT_ID, agent_name="Reception",
                          llm_provider="groq", llm_model="llama-3.3-70b-versatile",
                          system_prompt="You are a receptionist."))
        await s.commit()
    chat_mod._conversation_history.clear()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ── Classification ────────────────────────────────────────────────────────────

def test_detects_a_real_429_but_not_a_token_count():
    assert chat_mod.is_rate_limit_error(GROQ_TPD_ERROR) is True
    assert chat_mod.is_rate_limit_error("429 Too Many Requests") is True
    # The false positive the old bare-substring check had: a token COUNT that
    # merely contains "429" must not be reported to the patient as a rate limit.
    assert chat_mod.is_rate_limit_error("Bad request: Requested 4291 tokens exceeds context") is False
    assert chat_mod.is_rate_limit_error("connection reset by peer") is False


def test_parses_the_providers_retry_hint():
    assert chat_mod.retry_after_seconds(GROQ_TPD_ERROR) == pytest.approx(886.464, abs=0.01)
    assert chat_mod.retry_after_seconds("Please try again in 30s") == pytest.approx(30)
    assert chat_mod.retry_after_seconds("no hint here") is None


def test_daily_quota_message_is_honest_and_actionable():
    msg = chat_mod.describe_llm_failure(GROQ_TPD_ERROR)
    low = msg.lower()
    # The two specific lies from the production message.
    assert "too many requests" not in low
    assert "wait a moment" not in low
    # Says what is actually true, and where to go instead.
    assert "limit for today" in low or "usage limit" in low
    assert "call the clinic" in low


def test_short_burst_limit_quotes_the_real_wait():
    msg = chat_mod.describe_llm_failure("429 rate limit reached. Please try again in 20s")
    assert "20 seconds" in msg
    assert "today" not in msg.lower(), "a 20s burst limit must not be described as a daily cap"


def test_unknown_errors_are_not_labelled_as_rate_limits():
    assert chat_mod.describe_llm_failure("ValueError: something else broke") is None


# ── Recovery ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limit_fails_over_to_another_provider(seeded_db):
    """A 429 is exactly when another provider helps most. It used to be
    excluded from the fallback list, so the conversation dead-ended."""
    async def boom(*a, **kw):
        raise Exception(GROQ_TPD_ERROR)

    async def ok_openai(api_key, system_prompt, history, model, **kw):
        return "Thanks — what time would you like?"

    # call_groq must be stubbed too, or the same-provider retry (gpt-oss) runs
    # first — and, since settings.groq_api_key is read from .env before the
    # patched env var, it would fire a REAL request at the live Groq API.
    with patch.object(chat_mod, "_dispatch_llm", side_effect=boom), \
         patch.object(chat_mod, "call_groq", side_effect=boom), \
         patch.object(chat_mod, "call_openai", side_effect=ok_openai), \
         patch.dict(os.environ, {"GROQ_API_KEY": "gk", "OPENAI_API_KEY": "ok-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            reply = await chat_mod.generate_llm_response(
                agent, "9090909090", db, session_id="s-429-fallback", user_language="en-IN")

    assert reply == "Thanks — what time would you like?", \
        f"should have failed over to a working provider, got: {reply!r}"


@pytest.mark.asyncio
async def test_rate_limit_retries_another_groq_model_before_switching_provider(seeded_db):
    """Groq meters tokens-per-day PER MODEL, so a second Groq model is a whole
    extra daily budget reachable with the same key — try it first."""
    seen = {}

    async def boom(*a, **kw):
        raise Exception(GROQ_TPD_ERROR)

    async def ok_groq(api_key, system_prompt, history, model, **kw):
        seen["model"] = model
        return "Sure — what time suits you?"

    with patch.object(chat_mod, "_dispatch_llm", side_effect=boom), \
         patch.object(chat_mod, "call_groq", side_effect=ok_groq), \
         patch.dict(os.environ, {"GROQ_API_KEY": "gk"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            reply = await chat_mod.generate_llm_response(
                agent, "9090909090", db, session_id="s-429-model", user_language="en-IN")

    assert reply == "Sure — what time suits you?"
    assert seen["model"] == "openai/gpt-oss-120b", \
        f"should retry on a Groq model with its own budget, used {seen.get('model')!r}"
    # The model that ran out must never be the one retried.
    assert seen["model"] != "llama-3.3-70b-versatile"


def test_reasoning_models_get_room_to_think():
    """gpt-oss returns EMPTY content at the agents' normal max_tokens, because
    reasoning consumes the whole allowance — so the client must raise it."""
    assert chat_mod.is_groq_reasoning_model("openai/gpt-oss-120b") is True
    assert chat_mod.is_groq_reasoning_model("llama-3.3-70b-versatile") is False


@pytest.mark.asyncio
async def test_groq_reasoning_model_gets_reasoning_effort_and_more_tokens():
    captured = {}

    class _Resp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "hello"}}]}

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            captured.update(json or {})
            return _Resp()

    with patch("httpx.AsyncClient", lambda *a, **kw: _Client()):
        out = await chat_mod.call_groq("k", "sys", [{"role": "user", "content": "hi"}],
                                       "openai/gpt-oss-120b", max_tokens=150)
    assert out == "hello"
    assert captured["reasoning_effort"] == "low"
    assert captured["max_tokens"] >= 800, "150 tokens is all reasoning and yields an empty reply"

    captured.clear()
    with patch("httpx.AsyncClient", lambda *a, **kw: _Client()):
        await chat_mod.call_groq("k", "sys", [{"role": "user", "content": "hi"}],
                                 "llama-3.3-70b-versatile", max_tokens=150)
    assert "reasoning_effort" not in captured, "non-reasoning models must be left alone"
    assert captured["max_tokens"] == 150


@pytest.mark.asyncio
async def test_empty_completion_raises_rather_than_returning_blank():
    class _Resp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "   "}}]}

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None): return _Resp()

    with patch("httpx.AsyncClient", lambda *a, **kw: _Client()):
        with pytest.raises(Exception, match="empty completion"):
            await chat_mod.call_groq("k", "sys", [{"role": "user", "content": "hi"}],
                                     "openai/gpt-oss-120b")


@pytest.mark.asyncio
async def test_rate_limit_with_no_fallback_reports_the_truth(seeded_db):
    """Production's actual situation: Groq is the only configured LLM, so
    there is nothing to fail over to. The patient must still be told something
    true and useful rather than 'wait a moment'."""
    async def boom(*a, **kw):
        raise Exception(GROQ_TPD_ERROR)

    # Stub call_groq as well: the same-provider gpt-oss retry would otherwise
    # reach the real Groq API via settings.groq_api_key from .env.
    with patch.object(chat_mod, "_dispatch_llm", side_effect=boom), \
         patch.object(chat_mod, "call_groq", side_effect=boom), \
         patch.dict(os.environ, {"GROQ_API_KEY": "gk"}, clear=False):
        for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "DEEPSEEK_API_KEY"):
            os.environ.pop(k, None)
        with patch.object(chat_mod.settings, "openai_api_key", None), \
             patch.object(chat_mod.settings, "anthropic_api_key", None), \
             patch.object(chat_mod.settings, "gemini_api_key", None), \
             patch.object(chat_mod.settings, "deepseek_api_key", None):
            async with AsyncSessionLocal() as db:
                agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
                reply = await chat_mod.generate_llm_response(
                    agent, "9090909090", db, session_id="s-429-nofallback", user_language="en-IN")

    low = reply.lower()
    assert "wait a moment" not in low
    assert "too many requests" not in low
    assert "call the clinic" in low


@pytest.mark.asyncio
async def test_failed_turn_does_not_corrupt_the_conversation(seeded_db):
    """The booking must be resumable once quota returns: the error reply is
    never written into history as if the agent had said something useful."""
    async def boom(*a, **kw):
        raise Exception(GROQ_TPD_ERROR)

    with patch.object(chat_mod, "_dispatch_llm", side_effect=boom), \
         patch.dict(os.environ, {"GROQ_API_KEY": "gk"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            await chat_mod.generate_llm_response(
                agent, "9090909090", db, session_id="s-429-history", user_language="en-IN")

    history = chat_mod._conversation_history.get("s-429-history", [])
    assert not any(m["role"] == "assistant" and "usage limit" in m["content"].lower()
                   for m in history), "the failure notice must not become conversation context"
    # The patient's phone number is still there, so the flow can resume.
    assert any(m["role"] == "user" and "9090909090" in m["content"] for m in history)
