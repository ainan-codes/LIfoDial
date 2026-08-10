# -*- coding: utf-8 -*-
"""
Regression coverage for handle_audio_turn()'s TTS-stage pipelining — the first
tests to exercise this function at all (previously zero coverage).

A multi-sentence reply is split into chunks and synthesized CONCURRENTLY, each
sent to the client as its own binary WS message as soon as it's ready, instead
of one blocking call over the whole reply. A single-sentence reply must take
the exact unchanged single-call path. Both must leave the booking-honesty
guarantee (agent_test._handle_booking_action / _ACTION_RE) completely
untouched — chunking only ever happens on the FINAL, already safety-checked
response text.

Run:
    python -m pytest backend/tests/test_audio_turn_tts_pipelining.py -v
"""
import os
os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_audio_turn_tts_pipelining.db"

from datetime import time as time_cls, timedelta

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from sqlalchemy import select

import backend.db as db_mod
from backend.db import AsyncSessionLocal, engine, Base
from backend.models.tenant import Tenant
from backend.models.doctor import Doctor
from backend.models.doctor_availability import DoctorAvailability
from backend.models.appointment import Appointment
from backend.models.agent_config import AgentConfig
from backend.agent.booking_rules import BOOKING_RESULT_TRUE
from backend.routers import agent_test as chat_mod
from backend import redis_client
from backend.services.timeutil import ist_now

TENANT_ID = "66666666-6666-6666-6666-666666666666"
AGENT_ID = "77777777-7777-7777-7777-777777777777"
REAL_DOCTOR_ID = "88888888-8888-8888-8888-888888888888"
REAL_DOCTOR_NAME = "Dr Kavita Rao"

# A booking is only written if the slot is really open on the doctor's real
# schedule (his._availability_gate), so the date used below has to be a future
# one the seeded doctor actually consults on.
BOOK_DATE_STR = (ist_now().date() + timedelta(days=1)).strftime("%d/%m/%Y")


class FakeWebSocket:
    """Minimal WebSocket stand-in — records every send_json/send_bytes call in
    arrival order so tests can assert on ordering, not just final state."""

    def __init__(self):
        self.json_messages: list[dict] = []
        self.sent_bytes: list[bytes] = []

    async def send_json(self, data):
        self.json_messages.append(data)

    async def send_bytes(self, data):
        self.sent_bytes.append(data)


@pytest_asyncio.fixture
async def seeded_db():
    assert db_mod.IS_SQLITE, "TEST SAFETY: refusing to run against a non-SQLite database"
    db_mod._import_all_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as s:
        s.add(Tenant(id=TENANT_ID, clinic_name="Pipelining Test Clinic", admin_email="pipeline_test@example.com"))
        s.add(Doctor(id=REAL_DOCTOR_ID, tenant_id=TENANT_ID, name=REAL_DOCTOR_NAME, specialization="Dermatologist"))
        for dow in range(7):
            s.add(DoctorAvailability(tenant_id=TENANT_ID, doctor_id=REAL_DOCTOR_ID,
                                     day_of_week=dow,
                                     start_time=time_cls(0, 0), end_time=time_cls(23, 30)))
        s.add(AgentConfig(
            id=AGENT_ID, tenant_id=TENANT_ID, agent_name="Pipeline Bot",
            llm_provider="gemini", llm_model="gemini-2.5-flash",
            tts_provider="sarvam", tts_language="en-IN",
            system_prompt="You are a receptionist for Pipelining Test Clinic.",
            can_book_appointments=True,
        ))
        await s.commit()
    chat_mod._conversation_history.clear()
    redis_client._chat_histories.clear()
    chat_mod._session_cancelled.clear()
    chat_mod._agent_speaking.clear()
    chat_mod._agent_speaking_until.clear()
    chat_mod._session_turn_count.clear()
    chat_mod._language_tracker.clear()
    chat_mod._session_language_override.clear()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def _fake_tts(call_log: list):
    """Returns an async TTS stub that logs each call's input text and returns
    audio bytes derived from it (>=512 bytes to pass the sanity check),
    letting tests assert both call count/order and send order/content."""
    async def _tts(agent, text, language_override=""):
        call_log.append(text)
        return f"AUDIO[{text}]".encode() + b"\x00" * 600
    return _tts


# ── Sentence-splitting unit tests ────────────────────────────────────────────

def test_split_empty_text_returns_no_chunks():
    assert chat_mod._split_into_speakable_chunks("") == []
    assert chat_mod._split_into_speakable_chunks("   ") == []


def test_split_single_sentence_is_not_split():
    assert chat_mod._split_into_speakable_chunks("Hello there.") == ["Hello there."]


def test_split_multi_sentence_english():
    chunks = chat_mod._split_into_speakable_chunks("Hello there. How are you today?")
    assert chunks == ["Hello there.", "How are you today?"]


def test_split_merges_short_leading_fragment():
    # "Dr." alone is well under the default min_chunk_len — must fold forward
    # into the sentence that follows rather than becoming its own TTS call.
    chunks = chat_mod._split_into_speakable_chunks(
        "Dr. Smith is available. He can see you at 3 PM."
    )
    assert chunks == ["Dr. Smith is available.", "He can see you at 3 PM."]
    assert all(len(c) >= 12 for c in chunks)


def test_split_hindi_danda_boundary():
    # Each half must be long enough on its own to clear min_chunk_len, or the
    # merge-short-fragments rule correctly folds them into one chunk instead
    # (that's exercised by test_split_merges_short_leading_fragment).
    chunks = chat_mod._split_into_speakable_chunks(
        "आपका स्वागत है, हमारे क्लिनिक में। आपकी अपॉइंटमेंट कल दोपहर तीन बजे है।"
    )
    assert len(chunks) == 2


# ── handle_audio_turn integration tests ──────────────────────────────────────

@pytest.mark.asyncio
async def test_multi_sentence_reply_pipelines_chunks_in_order(seeded_db):
    reply = "Hello there. How can I help you today? Let me know what you need."

    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        return reply

    tts_calls: list[str] = []
    ws = FakeWebSocket()

    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
         patch.object(chat_mod, "transcribe_audio", new=AsyncMock(return_value=("what are your clinic hours", "en-IN"))), \
         patch.object(chat_mod, "sarvam_synthesize_with_retry", side_effect=_fake_tts(tts_calls)), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            await chat_mod.handle_audio_turn(ws, agent, b"fake-audio-bytes", db, session_id="s-multi")

    expected_chunks = chat_mod._split_into_speakable_chunks(reply)
    assert len(expected_chunks) >= 2, "test fixture reply must actually split into multiple chunks"

    # Every chunk was synthesized, and sent to the client in the SAME order —
    # concurrency in synthesis must never reorder delivery.
    assert tts_calls == expected_chunks
    assert len(ws.sent_bytes) == len(expected_chunks)
    for chunk_text, sent in zip(expected_chunks, ws.sent_bytes):
        assert sent == f"AUDIO[{chunk_text}]".encode() + b"\x00" * 600

    # Echo suppression must extend to cover the WHOLE multi-chunk utterance,
    # not just the last chunk's duration.
    assert chat_mod._agent_speaking_until["s-multi"] > 0

    # A tts_failed event must NOT have been sent — every chunk succeeded.
    assert not any(m.get("type") == "tts_failed" for m in ws.json_messages)

    # The timing message must report a first-audio time strictly less than the
    # total TTS time whenever more than one chunk was actually pipelined —
    # this is the metric the whole change exists to improve.
    timing = next(m for m in ws.json_messages if m.get("type") == "timing")
    assert timing["ttfa_ms"] <= timing["tts_ms"]


@pytest.mark.asyncio
async def test_single_sentence_reply_uses_unchanged_single_call_path(seeded_db):
    reply = "Sure thing!"

    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        return reply

    tts_calls: list[str] = []
    ws = FakeWebSocket()

    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
         patch.object(chat_mod, "transcribe_audio", new=AsyncMock(return_value=("what are your clinic hours", "en-IN"))), \
         patch.object(chat_mod, "sarvam_synthesize_with_retry", side_effect=_fake_tts(tts_calls)), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            await chat_mod.handle_audio_turn(ws, agent, b"fake-audio-bytes", db, session_id="s-single")

    # Exactly ONE TTS call, over the whole (unsplit) reply — the pipelined
    # multi-task branch must never be exercised for a single-sentence reply.
    assert tts_calls == [reply]
    assert len(ws.sent_bytes) == 1


@pytest.mark.asyncio
async def test_booking_action_reply_is_never_chunked_before_safety_check(seeded_db):
    """A reply containing [ACTION: BOOK|...] must go through the FULL honest
    booking flow (regeneration on the verified DB outcome) exactly as before —
    proving the TTS-pipelining change never sees or speaks the raw, unsafety-
    checked model output."""

    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        if "SYSTEM UPDATE (AUTHORITATIVE" in system_prompt:
            assert BOOKING_RESULT_TRUE in system_prompt, system_prompt[-400:]
            return f"You're all set. Your appointment with {REAL_DOCTOR_NAME} is confirmed."
        return ("One moment while I book that.\n"
                f"[ACTION: BOOK|Jane Doe|+919812345678|{BOOK_DATE_STR}|4 PM|{REAL_DOCTOR_NAME}|N/A]")

    tts_calls: list[str] = []
    ws = FakeWebSocket()

    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
         patch.object(chat_mod, "transcribe_audio", new=AsyncMock(return_value=(
             "Book me with Dr Kavita Rao tomorrow at 4 PM, I'm Jane Doe 9812345678", "en-IN"))), \
         patch.object(chat_mod, "sarvam_synthesize_with_retry", side_effect=_fake_tts(tts_calls)), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            await chat_mod.handle_audio_turn(ws, agent, b"fake-audio-bytes", db, session_id="s-booking")

    # The real appointment was actually written — the booking flow ran fully.
    async with AsyncSessionLocal() as s:
        appts = (await s.execute(select(Appointment).where(Appointment.tenant_id == TENANT_ID))).scalars().all()
    assert len(appts) == 1
    assert appts[0].doctor_id == REAL_DOCTOR_ID

    # Nothing spoken ever contains the raw [ACTION: ...] tag — only the final,
    # DB-verified confirmation text was ever passed to TTS.
    assert all("[ACTION" not in c for c in tts_calls)
    assert any("confirmed" in c.lower() for c in tts_calls)
