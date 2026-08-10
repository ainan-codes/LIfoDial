"""
Tests for cancel/reschedule support in the voice pipeline's BookingProcessor.

Mirrors the style of test_booking_processor.py (which covers the NEW-booking
flow only): the DB write for an EXISTING appointment is AWAITED at the
LLMContextFrame and its REAL result is injected into the LLM context before
generation — the agent must never say "cancelled"/"rescheduled" unless a
system message starting with [BOOKING_RESULT success=true] appears.

Run: python -m pytest backend/tests/test_booking_processor_cancel_reschedule.py -v
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
    ],
}


def _make_processor(**config_overrides) -> BookingProcessor:
    cfg = {"can_book_appointments": True, "can_cancel_appointments": True, "can_check_availability": True}
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


# ── Intent detection + entity collection ───────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_intent_sets_mode_and_waits_for_name():
    proc = _make_processor()
    await proc._handle_transcription("I want to cancel my appointment")
    assert proc.booking_state["mode"] == "cancel"
    assert proc.booking_state["action_awaiting_confirm"] is False


@pytest.mark.asyncio
async def test_cancel_arms_confirmation_once_name_known():
    proc = _make_processor()
    await proc._handle_transcription("I want to cancel my appointment")
    await proc._handle_transcription("my name is Rajesh")
    assert proc.booking_state["patient_name"] == "Rajesh"
    assert proc.booking_state["action_awaiting_confirm"] is True


@pytest.mark.asyncio
async def test_reschedule_needs_both_name_and_new_slot():
    proc = _make_processor()
    await proc._handle_transcription("I want to reschedule my appointment")
    await proc._handle_transcription("my name is Priya")
    # Name known, but no new time yet — must not arm confirmation.
    assert proc.booking_state["action_awaiting_confirm"] is False

    await proc._handle_transcription("tomorrow at 5 pm please")
    assert proc.booking_state["new_slot_time"] == "5 pm"
    assert proc.booking_state["new_slot_day"] == "Tomorrow"
    assert proc.booking_state["action_awaiting_confirm"] is True


@pytest.mark.asyncio
async def test_active_action_flow_blocks_new_booking_keywords():
    """While a cancel/reschedule flow is active, doctor/slot keyword matching
    for a brand NEW booking must not run on the same utterances."""
    proc = _make_processor()
    await proc._handle_transcription("I want to cancel my appointment")
    await proc._handle_transcription("cardiologist at 4 pm")
    assert proc.booking_state["pending_doctor_id"] is None
    assert proc.booking_state["pending_slot"] is None


@pytest.mark.asyncio
async def test_can_cancel_appointments_disabled_never_matches():
    proc = _make_processor(can_cancel_appointments=False)
    await proc._handle_transcription("I want to cancel my appointment, my name is Rajesh, yes")
    assert proc.booking_state["mode"] is None
    assert proc._action_commit_pending is False


@pytest.mark.asyncio
async def test_abort_before_confirm_resets_action_state():
    proc = _make_processor()
    await proc._handle_transcription("I want to cancel my appointment")
    await proc._handle_transcription("my name is Rajesh")
    assert proc.booking_state["action_awaiting_confirm"] is True

    await proc._handle_transcription("no, never mind")
    assert proc.booking_state["mode"] is None
    assert proc.booking_state["action_awaiting_confirm"] is False
    assert proc._action_commit_pending is False


# ── Confirm keyword → commit pending ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_confirm_keyword_marks_action_commit_pending():
    proc = _make_processor()
    await proc._handle_transcription("I want to cancel my appointment")
    await proc._handle_transcription("my name is Rajesh")
    await proc._handle_transcription("yes")
    assert proc._action_commit_pending is True


# ── Awaited commit + context injection ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_successful_cancel_commit_injects_success_before_llm():
    proc = _make_processor()
    await proc._handle_transcription("I want to cancel my appointment")
    await proc._handle_transcription("my name is Rajesh")
    await proc._handle_transcription("yes cancel it")
    assert proc._action_commit_pending is True

    async def fake_commit(**kwargs):
        assert kwargs["action"] == "CANCEL"
        assert kwargs["patient_name"] == "Rajesh"
        assert kwargs["patient_phone"] == "+911234567890"
        return True, {"appointment_id": "appt-77"}

    frame = _ctx_frame()
    with patch.object(bp_mod, "_commit_action_to_db", fake_commit):
        await proc._commit_and_inject_action_result(frame)

    msgs = _messages(frame.context)
    assert any(
        "[BOOKING_RESULT success=true]" in str(m) and "appt-77" in str(m)
        for m in msgs
    ), f"success message not injected: {msgs}"
    assert proc.booking_state["action_confirmed"] is True
    assert proc._action_commit_pending is False


@pytest.mark.asyncio
async def test_successful_reschedule_commit_passes_new_slot_separately():
    proc = _make_processor()
    await proc._handle_transcription("I want to reschedule my appointment")
    await proc._handle_transcription("my name is Priya, tomorrow at 5 pm")
    await proc._handle_transcription("yes")
    assert proc._action_commit_pending is True

    async def fake_commit(**kwargs):
        assert kwargs["action"] == "RESCHEDULE"
        assert kwargs["new_slot_day"] == "Tomorrow"
        assert kwargs["new_slot_time"] == "5 pm"
        return True, {"appointment_id": "appt-88"}

    frame = _ctx_frame()
    with patch.object(bp_mod, "_commit_action_to_db", fake_commit):
        await proc._commit_and_inject_action_result(frame)

    msgs = _messages(frame.context)
    assert any("[BOOKING_RESULT success=true]" in str(m) for m in msgs)
    assert proc.booking_state["action_confirmed"] is True


@pytest.mark.asyncio
async def test_not_found_injects_honest_failure_and_allows_retry():
    proc = _make_processor()
    await proc._handle_transcription("I want to cancel my appointment")
    await proc._handle_transcription("my name is Ghost")
    await proc._handle_transcription("yes")

    async def fake_commit(**kwargs):
        return False, {"reason": "not_found"}

    frame = _ctx_frame()
    with patch.object(bp_mod, "_commit_action_to_db", fake_commit):
        await proc._commit_and_inject_action_result(frame)

    msgs = _messages(frame.context)
    assert any(
        "[BOOKING_RESULT success=false]" in str(m) and "No appointment was found" in str(m)
        for m in msgs
    ), f"honest not-found message not injected: {msgs}"
    assert proc.booking_state["action_confirmed"] is False
    assert proc.booking_state["action_awaiting_confirm"] is True  # retry allowed


@pytest.mark.asyncio
async def test_system_error_injects_generic_failure_and_rearms():
    proc = _make_processor()
    await proc._handle_transcription("I want to cancel my appointment")
    await proc._handle_transcription("my name is Rajesh")
    await proc._handle_transcription("yes")

    async def fake_commit(**kwargs):
        return False, {}

    frame = _ctx_frame()
    with patch.object(bp_mod, "_commit_action_to_db", fake_commit):
        await proc._commit_and_inject_action_result(frame)

    msgs = _messages(frame.context)
    assert any("[BOOKING_RESULT success=false]" in str(m) for m in msgs)
    assert proc.booking_state["action_confirmed"] is False
    assert proc.booking_state["action_awaiting_confirm"] is True
