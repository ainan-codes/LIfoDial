# -*- coding: utf-8 -*-
"""
Verifies generate_llm_response()'s cross-worker chat-history fix: REST/embed
callers (websocket=None) read-through/write-through backend.redis_client so a
follow-up message landing on a DIFFERENT process still sees prior turns, while
the WS voice/text path (websocket=<ws>) is untouched — it stays on the local
_conversation_history dict only, with zero added Redis calls.

No REDIS_URL is set in this test environment, so redis_client resolves to its
own in-memory fallback dict (redis_client._chat_histories) — a process-global
that is deliberately SEPARATE from agent_test._conversation_history. Reading
from that fallback directly is how this test simulates "another worker already
wrote this session's history".

Run:
    python -m pytest backend/tests/test_chat_history_redis.py -v
"""
import os
os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_chat_history_redis.db"

import pytest
import pytest_asyncio
from unittest.mock import patch
from sqlalchemy import select

import backend.db as db_mod
from backend.db import AsyncSessionLocal, engine, Base
from backend.models.tenant import Tenant
from backend.models.agent_config import AgentConfig
from backend.routers import agent_test as chat_mod
from backend import redis_client

TENANT_ID = "44444444-4444-4444-4444-444444444444"
AGENT_ID = "55555555-5555-5555-5555-555555555555"


@pytest_asyncio.fixture
async def seeded_db():
    assert db_mod.IS_SQLITE, "TEST SAFETY: refusing to run against a non-SQLite database"
    db_mod._import_all_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as s:
        s.add(Tenant(id=TENANT_ID, clinic_name="Redis Test Clinic", admin_email="redis_test@example.com"))
        s.add(AgentConfig(
            id=AGENT_ID, tenant_id=TENANT_ID, agent_name="Redis Bot",
            llm_provider="gemini", llm_model="gemini-2.5-flash",
            system_prompt="You are a receptionist for Redis Test Clinic.",
        ))
        await s.commit()
    chat_mod._conversation_history.clear()
    redis_client._chat_histories.clear()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _plain_reply(agent, message, session_id, websocket=None):
    """Call generate_llm_response with a stubbed LLM that never triggers the
    booking-action path — just a plain conversational echo-style reply."""
    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        return f"reply to: {message}"

    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            return await chat_mod.generate_llm_response(
                agent, message, db, session_id=session_id,
                user_language="en-IN", websocket=websocket,
            )


@pytest.mark.asyncio
async def test_rest_call_writes_through_to_redis_fallback(seeded_db):
    """A REST/embed-shaped call (no websocket) must persist history into the
    shared store, not just the local L1 cache."""
    async with AsyncSessionLocal() as db:
        agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()

    await _plain_reply(agent, "hello there", session_id="s-write")

    stored = redis_client._chat_histories.get("s-write")
    assert stored is not None, "write-through never reached the shared store"
    assert stored[-1] == {"role": "assistant", "content": "reply to: hello there"}


@pytest.mark.asyncio
async def test_rest_call_on_fresh_worker_reads_through_prior_turn(seeded_db):
    """Simulates a follow-up message landing on a DIFFERENT worker than the one
    that handled turn 1: the local L1 cache has no entry for this session_key,
    but the shared store (redis_client's fallback here) does — the read-through
    at generate_llm_response's init block must seed from it rather than
    silently starting a brand-new, context-free history."""
    async with AsyncSessionLocal() as db:
        agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()

    # Seed the shared store directly — simulating "worker A already ran turn 1"
    # — WITHOUT touching agent_test._conversation_history (simulating that this
    # request lands on a worker whose local L1 cache has never seen this key).
    prior_history = [
        {"role": "user", "content": "my name is Priya"},
        {"role": "assistant", "content": "reply to: my name is Priya"},
    ]
    redis_client._chat_histories["s-crossworker"] = prior_history
    assert "s-crossworker" not in chat_mod._conversation_history

    reply = await _plain_reply(agent, "what is my name?", session_id="s-crossworker")

    assert reply == "reply to: what is my name?"
    # The local cache should now hold prior turns PLUS this turn — proving the
    # read-through actually seeded from the shared store, not from nothing.
    local_history = chat_mod._conversation_history["s-crossworker"]
    assert local_history[0] == prior_history[0]
    assert local_history[1] == prior_history[1]
    assert local_history[-1]["content"] == "reply to: what is my name?"


@pytest.mark.asyncio
async def test_ws_voice_path_never_touches_redis(seeded_db):
    """The WS voice/text turn loop always passes websocket=<ws>, and must skip
    every new Redis call entirely — no added latency, no behavior change."""
    async with AsyncSessionLocal() as db:
        agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()

    fake_ws = object()  # any truthy, non-None sentinel — generate_llm_response
                         # only ever checks `websocket is None`, never calls methods on it
    await _plain_reply(agent, "hi", session_id="s-ws", websocket=fake_ws)

    assert "s-ws" not in redis_client._chat_histories, (
        "WS-path call wrote to the shared chat-history store — it must stay local-only"
    )
    assert chat_mod._conversation_history["s-ws"][-1]["content"] == "reply to: hi"


@pytest.mark.asyncio
async def test_clear_session_endpoint_clears_shared_store_too(seeded_db):
    """DELETE /agent-chat/{agent_id}/session/{session_id} must clear BOTH the
    local L1 cache and the shared store, not just the local one."""
    async with AsyncSessionLocal() as db:
        agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()

    await _plain_reply(agent, "hello", session_id="s-clear")
    assert "s-clear" in chat_mod._conversation_history
    assert "s-clear" in redis_client._chat_histories

    from unittest.mock import MagicMock
    fake_user = MagicMock()
    fake_user.require_owns = MagicMock(return_value=None)

    async with AsyncSessionLocal() as db:
        result = await chat_mod.clear_agent_session(
            agent_id=AGENT_ID, session_id="s-clear", user=fake_user, db=db,
        )
    assert result == {"status": "cleared"}
    assert "s-clear" not in chat_mod._conversation_history
    assert "s-clear" not in redis_client._chat_histories
