"""
Tests for cancel/reschedule support in the voice pipeline's BookingProcessor.

Mirrors the style of test_booking_processor.py (which covers the NEW-booking
flow only): the DB write for an EXISTING appointment is AWAITED at the
LLMContextFrame and its REAL result is injected into the LLM context before
generation — the agent must never say "cancelled"/"rescheduled" unless a
system message starting with [BOOKING_RESULT success=true] appears.

Run: python -m pytest backend/tests/test_booking_processor_cancel_reschedule.py -v
"""

# ── TEST SAFETY: force a local SQLite DB *before* importing anything backend ───
# The reschedule flow now looks the existing appointment up (to check the new
# time against that doctor's real schedule), so this module DOES reach the
# database — without this it would query the production Supabase.
import os
os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_booking_processor_cancel_reschedule.db"

from unittest.mock import AsyncMock, patch

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


def _slot_is_open(open_: bool = True):
    """Patch the reschedule pre-check, for tests about the state machine rather
    than about availability itself. The availability behaviour has its own
    tests at the bottom of this file."""
    return patch.object(
        BookingProcessor, "_reschedule_slot_is_open", AsyncMock(return_value=open_),
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
    with _slot_is_open():
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
    with _slot_is_open():
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
async def test_reschedule_commit_raises_action_in_progress_while_awaited():
    """pipeline.py's _enforce_silence_timeout pins its idle clock to "now"
    while call_logger.action_in_progress is True, so the caller's own
    reschedule/cancel round trip can never outrun the silence timeout and get
    the call hung up before they hear whether it succeeded. Before this,
    BookingProcessor's own commit never touched that flag at all — only
    VoiceActionProcessor did — so a call driven through the keyword-confirm
    FSM (this file) had no such protection."""

    class FakeCallLogger:
        def __init__(self):
            self.action_in_progress = False

    call_logger = FakeCallLogger()
    proc = _make_processor()
    proc._call_logger = call_logger
    with _slot_is_open():
        await proc._handle_transcription("I want to reschedule my appointment")
        await proc._handle_transcription("my name is Priya, tomorrow at 5 pm")
        await proc._handle_transcription("yes")

    busy_during_commit = None

    async def fake_commit(**kwargs):
        nonlocal busy_during_commit
        busy_during_commit = call_logger.action_in_progress
        return True, {"appointment_id": "appt-99"}

    frame = _ctx_frame()
    with patch.object(bp_mod, "_commit_action_to_db", fake_commit):
        await proc._commit_and_inject_action_result(frame)

    assert busy_during_commit is True, "action_in_progress must be True while the DB write is in flight"
    assert call_logger.action_in_progress is False, "action_in_progress must be lowered again once the commit finishes"


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


# ── Check-before-confirm: the reschedule branch's availability gate ───────────
#
# Production 2026-08-11: the agent never checked anything for a reschedule. It
# admitted it had no slot data for the doctor, then confirmed a specific time
# anyway and tried to write it. Confirmation is now armed only after the new
# time is verified against the real schedule of the doctor the EXISTING
# appointment is with.

@pytest.mark.asyncio
async def test_reschedule_does_not_arm_when_the_new_slot_is_not_open():
    proc = _make_processor()
    existing = {"appointment_id": "appt-1", "doctor_id": "doc-1",
                "doctor_name": "Dr Sharma", "slot_time": None, "status": "confirmed"}

    with patch("backend.services.his.find_active_appointment",
               AsyncMock(return_value=existing)), \
         patch("backend.services.availability.is_doctor_open_at",
               AsyncMock(return_value=(False, "slot_taken"))), \
         patch.object(bp_mod, "_build_availability_note",
                      AsyncMock(return_value="[AVAILABILITY_NOTE] Dr Sharma is only open at 4:00 PM.")):
        await proc._handle_transcription("I want to reschedule my appointment")
        await proc._handle_transcription("my name is Priya, tomorrow at 5 pm")

    assert proc.booking_state["action_awaiting_confirm"] is False, \
        "must not ask the caller to confirm a time it never verified"
    # The rejected time is cleared, so a later "yes" cannot resurrect it...
    assert proc.booking_state["new_slot_time"] is None
    # ...and the agent is handed the doctor's REAL open times to offer instead.
    assert "AVAILABILITY_NOTE" in (proc._info_message or "")


@pytest.mark.asyncio
async def test_reschedule_arms_when_the_new_slot_really_is_open():
    proc = _make_processor()
    existing = {"appointment_id": "appt-1", "doctor_id": "doc-1",
                "doctor_name": "Dr Sharma", "slot_time": None, "status": "confirmed"}

    with patch("backend.services.his.find_active_appointment",
               AsyncMock(return_value=existing)), \
         patch("backend.services.availability.is_doctor_open_at",
               AsyncMock(return_value=(True, "ok"))):
        await proc._handle_transcription("I want to reschedule my appointment")
        await proc._handle_transcription("my name is Priya, tomorrow at 5 pm")

    assert proc.booking_state["action_awaiting_confirm"] is True
    assert proc._info_message is None


@pytest.mark.asyncio
async def test_reschedule_checks_the_doctor_the_appointment_is_actually_with():
    """The caller rarely names a doctor when rescheduling, so the calendar that
    matters is the one their existing appointment sits on."""
    proc = _make_processor()
    existing = {"appointment_id": "appt-1", "doctor_id": "doc-XYZ",
                "doctor_name": "Dr Other", "slot_time": None, "status": "confirmed"}
    checked = {}

    async def fake_open(tenant_id, doctor_id, slot_utc):
        checked["doctor_id"] = doctor_id
        checked["slot_utc"] = slot_utc
        return True, "ok"

    with patch("backend.services.his.find_active_appointment",
               AsyncMock(return_value=existing)), \
         patch("backend.services.availability.is_doctor_open_at", fake_open):
        await proc._handle_transcription("I want to reschedule my appointment")
        await proc._handle_transcription("my name is Priya, tomorrow at 5 pm")

    assert checked["doctor_id"] == "doc-XYZ"
    assert checked["slot_utc"] is not None


@pytest.mark.asyncio
async def test_unavailable_slot_at_commit_stands_the_flow_down():
    """Even if a slot passes the pre-check, the awaited commit is the authority.
    A refusal there must clear the time — re-arming it would let a repeated
    'yes' retry a time that can never succeed."""
    proc = _make_processor()
    with _slot_is_open():
        await proc._handle_transcription("I want to reschedule my appointment")
        await proc._handle_transcription("my name is Priya, tomorrow at 5 pm")
        await proc._handle_transcription("yes")

    async def fake_commit(**kwargs):
        return False, {"reason": "slot_taken", "alternatives": ["4:00 PM", "4:30 PM"]}

    frame = _ctx_frame()
    with patch.object(bp_mod, "_commit_action_to_db", fake_commit):
        await proc._commit_and_inject_action_result(frame)

    msgs = _messages(frame.context)
    injected = " ".join(str(m) for m in msgs)
    assert "[BOOKING_RESULT success=false]" in injected
    assert "still stands at its original time" in injected
    assert "4:00 PM" in injected, "the caller must be offered the doctor's real free times"
    assert proc.booking_state["action_awaiting_confirm"] is False
    assert proc.booking_state["new_slot_time"] is None


@pytest.mark.asyncio
async def test_reschedule_to_the_same_time_is_reported_as_no_change():
    proc = _make_processor()
    with _slot_is_open():
        await proc._handle_transcription("I want to reschedule my appointment")
        await proc._handle_transcription("my name is Priya, tomorrow at 5 pm")
        await proc._handle_transcription("yes")

    async def fake_commit(**kwargs):
        return True, {"appointment_id": "appt-1", "reason": "already_at_that_time"}

    frame = _ctx_frame()
    with patch.object(bp_mod, "_commit_action_to_db", fake_commit):
        await proc._commit_and_inject_action_result(frame)

    injected = " ".join(str(m) for m in _messages(frame.context))
    assert "[BOOKING_RESULT success=true]" in injected
    assert "ALREADY at exactly that time" in injected
    assert proc.booking_state["action_confirmed"] is True


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
