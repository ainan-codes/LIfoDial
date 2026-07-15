"""
Tests for the honest-booking rework (audit FIX 4).

Covers:
  - No fabricated slot: a doctor match alone must NOT arm confirmation.
  - The slot comes from the caller's utterance (time + optional day word).
  - Confirm keyword only marks commit pending; the DB write is AWAITED at the
    LLMContextFrame and its REAL result is injected into the LLM context
    before generation.
  - A failed write injects success=false and re-arms for retry.

Run: python -m pytest backend/tests/test_booking_processor.py -v
"""

from unittest.mock import patch

import pytest

from pipecat.frames.frames import LLMContextFrame
from pipecat.processors.aggregators.llm_context import LLMContext

from backend.agent.processors import booking_processor as bp_mod
from backend.agent.processors.booking_processor import BookingProcessor


TENANT = {
    "id": "tenant-1",
    "clinic_name": "Apollo",
    "doctors": [
        {"id": "doc-1", "name": "Dr Sharma", "specialization": "Cardiologist"},
        {"id": "doc-2", "name": "Dr Iyer", "specialization": "Dermatologist"},
    ],
}


def _make_processor(**config_overrides) -> BookingProcessor:
    cfg = {"can_book_appointments": True, "can_check_availability": True}
    cfg.update(config_overrides)
    return BookingProcessor(
        tenant=TENANT,
        agent_config=cfg,
        call_meta={"caller_phone": "+911234567890", "call_record_id": "call-9"},
    )


def _ctx_frame() -> LLMContextFrame:
    return LLMContextFrame(context=LLMContext(messages=[]))


def _messages(ctx: LLMContext) -> list:
    getter = getattr(ctx, "get_messages", None)
    return getter() if callable(getter) else ctx.messages


# ── No fabricated slot ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_doctor_match_does_not_arm_confirmation():
    proc = _make_processor()
    await proc._handle_transcription("I want to see the cardiologist")
    assert proc.booking_state["pending_doctor_id"] == "doc-1"
    assert proc.booking_state["pending_slot"] is None          # no 11:00 AM
    assert proc.booking_state["awaiting_confirm"] is False


@pytest.mark.asyncio
async def test_confirm_without_slot_does_nothing():
    proc = _make_processor()
    await proc._handle_transcription("I want to see the cardiologist")
    await proc._handle_transcription("yes")                     # no time given yet
    assert proc._commit_pending is False
    assert proc.booking_state["confirmed"] is False


# ── Slot from the caller's own words ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_caller_time_arms_confirmation_with_that_time():
    proc = _make_processor()
    await proc._handle_transcription("I want to see the cardiologist")
    await proc._handle_transcription("tomorrow at 3:30 pm please")
    assert proc.booking_state["pending_slot"] == "Tomorrow 3:30 pm"
    assert proc.booking_state["awaiting_confirm"] is True


@pytest.mark.asyncio
async def test_doctor_and_time_in_one_utterance():
    proc = _make_processor()
    await proc._handle_transcription("book me with Dr Iyer at 5 pm")
    assert proc.booking_state["pending_doctor_id"] == "doc-2"
    assert proc.booking_state["pending_slot"] == "5 pm"
    assert proc.booking_state["awaiting_confirm"] is True


# ── Awaited commit + context injection ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_successful_commit_injects_success_before_llm():
    proc = _make_processor()
    await proc._handle_transcription("cardiologist please")
    await proc._handle_transcription("4 pm today")
    await proc._handle_transcription("yes book it")
    assert proc._commit_pending is True

    async def fake_commit(**kwargs):
        # Must receive the CALLER's slot, not a fabricated one
        assert kwargs["slot_time"] == "Today 4 pm"
        return True, {"appointment_id": "appt-42", "doctor_name": "Dr Sharma"}

    frame = _ctx_frame()
    with patch.object(bp_mod, "_commit_booking_to_db", fake_commit):
        await proc._commit_and_inject_result(frame)

    msgs = _messages(frame.context)
    assert any(
        "[BOOKING_RESULT success=true]" in str(m) and "appt-42" in str(m)
        for m in msgs
    ), f"success message not injected: {msgs}"
    assert proc.booking_state["confirmed"] is True
    assert proc._commit_pending is False


@pytest.mark.asyncio
async def test_failed_commit_injects_failure_and_rearms():
    proc = _make_processor()
    await proc._handle_transcription("cardiologist please")
    await proc._handle_transcription("4 pm")
    await proc._handle_transcription("haan")

    async def fake_commit(**kwargs):
        return False, {}

    frame = _ctx_frame()
    with patch.object(bp_mod, "_commit_booking_to_db", fake_commit):
        await proc._commit_and_inject_result(frame)

    msgs = _messages(frame.context)
    assert any("[BOOKING_RESULT success=false]" in str(m) for m in msgs)
    assert proc.booking_state["confirmed"] is False
    assert proc.booking_state["awaiting_confirm"] is True       # retry allowed


@pytest.mark.asyncio
async def test_booking_disabled_never_matches():
    proc = _make_processor(can_book_appointments=False)
    await proc._handle_transcription("cardiologist at 4 pm, yes book it")
    assert proc.booking_state["pending_doctor_id"] is None
    assert proc._commit_pending is False
