"""
Tests that call transcripts are persisted to Supabase incrementally, not only at
finalize.

The bug this pins: in production, 95 of 95 call_records had `transcript = []`,
including calls whose `turn_count` was 12. Two causes, both fixed:

  1. `_update_call_record_turns` wrote ONLY `turn_count`, so the words existed
     nowhere but process memory until `_finalize_call_record` ran — and ~88% of
     calls never finalized (ended_at was NULL), so the transcript was lost every
     time. Losing it also silently disabled post-call analysis: `call_evaluator`
     bails with "no transcript", which is why summary / sentiment /
     intent_detected / detected_language were 100% NULL.
  2. Only USER turns were ever appended. The agent's own words were accumulated
     into `_agent_utterance` purely for goodbye detection and then discarded, so a
     saved transcript was one-sided — questions with no answers.

Run: python -m pytest backend/tests/test_transcript_persistence.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import backend.db as db_module
from backend.models.call_record import CallRecord


@pytest_asyncio.fixture
async def session_factory(monkeypatch):
    """Point the module-level AsyncSessionLocal at a throwaway SQLite DB, because
    the helpers under test open their own session rather than taking one."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(db_module.Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_module, "AsyncSessionLocal", maker)
    yield maker
    await engine.dispose()


async def _make_record(maker, **kw) -> str:
    async with maker() as s:
        rec = CallRecord(tenant_id="t1", agent_id="a1", status="active",
                         turn_count=0, transcript=[], **kw)
        s.add(rec)
        await s.commit()
        return rec.id


async def _load(maker, rid) -> CallRecord:
    async with maker() as s:
        return (await s.execute(select(CallRecord).where(CallRecord.id == rid))).scalar_one()


@pytest.mark.asyncio
async def test_transcript_is_written_on_every_turn(session_factory):
    from backend.agent.processors.call_logger_processor import _update_call_record_turns

    rid = await _make_record(session_factory)
    convo = [
        {"turn": 1, "role": "user", "text": "Namaste, I need an appointment"},
        {"turn": 2, "role": "assistant", "text": "Of course — which doctor?"},
    ]

    await _update_call_record_turns(rid, 2, convo)

    rec = await _load(session_factory, rid)
    assert rec.turn_count == 2
    assert rec.transcript == convo, "transcript must be persisted mid-call, not only at finalize"


@pytest.mark.asyncio
async def test_transcript_survives_a_call_that_never_finalizes(session_factory):
    """The exact production failure: job dies, finalize never runs."""
    from backend.agent.processors.call_logger_processor import _update_call_record_turns

    rid = await _make_record(session_factory)
    for n in range(1, 4):
        await _update_call_record_turns(
            rid, n, [{"turn": i, "role": "user", "text": f"turn {i}"} for i in range(1, n + 1)]
        )

    # No finalize call at all — simulating the crashed worker.
    rec = await _load(session_factory, rid)
    assert rec.status == "active", "fixture should still be un-finalized"
    assert len(rec.transcript) == 3, "conversation was lost when the job died"


@pytest.mark.asyncio
async def test_out_of_order_updates_never_truncate_the_transcript(session_factory):
    """These updates run as detached asyncio tasks and can land out of order."""
    from backend.agent.processors.call_logger_processor import _update_call_record_turns

    rid = await _make_record(session_factory)
    long_convo = [{"turn": i, "role": "user", "text": f"t{i}"} for i in range(1, 6)]
    await _update_call_record_turns(rid, 5, long_convo)

    # A stale task from earlier in the call lands late with fewer turns.
    await _update_call_record_turns(rid, 2, long_convo[:2])

    rec = await _load(session_factory, rid)
    assert len(rec.transcript) == 5, "a late stale update clobbered the fuller transcript"


@pytest.mark.asyncio
async def test_turn_count_only_update_leaves_transcript_untouched(session_factory):
    """Callers that pass no transcript must not blank an existing one."""
    from backend.agent.processors.call_logger_processor import _update_call_record_turns

    rid = await _make_record(session_factory)
    convo = [{"turn": 1, "role": "user", "text": "hello"}]
    await _update_call_record_turns(rid, 1, convo)
    await _update_call_record_turns(rid, 2)  # transcript omitted

    rec = await _load(session_factory, rid)
    assert rec.transcript == convo
    assert rec.turn_count == 2


@pytest.mark.asyncio
async def test_missing_record_is_non_fatal(session_factory):
    """A background task must never raise into the call."""
    from backend.agent.processors.call_logger_processor import _update_call_record_turns

    await _update_call_record_turns("does-not-exist", 1, [{"turn": 1}])  # must not raise


def test_agent_turns_are_recorded_with_the_assistant_role():
    """Regression: only user turns used to reach the transcript."""
    from backend.agent.processors.call_logger_processor import CallLoggerProcessor

    proc = CallLoggerProcessor.__new__(CallLoggerProcessor)
    proc._transcript = []
    proc._turn_count = 0
    proc._call_record_id = None  # skips the DB task

    proc._record_agent_turn("Your appointment is confirmed for 4 PM.")

    assert len(proc._transcript) == 1
    entry = proc._transcript[0]
    assert entry["role"] == "assistant"
    assert entry["text"] == "Your appointment is confirmed for 4 PM."
    assert entry["turn"] == 1

    # Empty utterances must not create phantom turns.
    proc._record_agent_turn("")
    proc._record_agent_turn("   ")
    assert len(proc._transcript) == 1
