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

    with patch.object(chat_mod, "_dispatch_llm", side_effect=boom), \
         patch.object(chat_mod, "call_openai", side_effect=ok_openai), \
         patch.dict(os.environ, {"GROQ_API_KEY": "gk", "OPENAI_API_KEY": "ok-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            reply = await chat_mod.generate_llm_response(
                agent, "9090909090", db, session_id="s-429-fallback", user_language="en-IN")

    assert reply == "Thanks — what time would you like?", \
        f"should have failed over to a working provider, got: {reply!r}"


@pytest.mark.asyncio
async def test_rate_limit_with_no_fallback_reports_the_truth(seeded_db):
    """Production's actual situation: Groq is the only configured LLM, so
    there is nothing to fail over to. The patient must still be told something
    true and useful rather than 'wait a moment'."""
    async def boom(*a, **kw):
        raise Exception(GROQ_TPD_ERROR)

    with patch.object(chat_mod, "_dispatch_llm", side_effect=boom), \
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
