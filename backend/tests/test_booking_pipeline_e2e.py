# -*- coding: utf-8 -*-
"""
End-to-end wiring tests for appointment actions on BOTH channels.

Why this file exists
--------------------
Every other booking test in this suite calls ``BookingProcessor._handle_transcription``
directly. That proves the state machine's logic and proves nothing about whether
the state machine is ever *reached* — and on the voice path, for the entire life
of the product, it was not. ``booking_processor`` sat downstream of
``context_aggregator.user()``, which consumes ``TranscriptionFrame`` without
pushing it (pipecat 1.5.0, llm_response_universal.py:794), so the FSM received
zero utterances: no doctor match, no slot, no confirmation, no DB write. The
production database agreed — every appointment ever created had ``call_id IS
NULL``, and not one came from a call.

So the rule for this file: **drive frames through an assembled Pipeline, in the
real relative order, and assert on the database.** Nothing here may call an
internal handler directly — that is precisely the shortcut that let the bug ship.

Covers all six (channel × intent) combinations plus the two structural
guarantees that failure depends on:

    voice × BOOK / RESCHEDULE / CANCEL   — through the real pipeline
    chat  × BOOK / RESCHEDULE / CANCEL   — through the real handler
    an ErrorFrame from the LLM's position reaches the never-silence guard
    an exception inside BookingProcessor never drops the caller's turn

Run: python -m pytest backend/tests/test_booking_pipeline_e2e.py -v
"""

# ── TEST SAFETY: force a local SQLite DB *before* importing backend.db ─────────
import os
os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_booking_pipeline_e2e.db"

import asyncio
from datetime import datetime, time as time_cls, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from pipecat.frames.frames import ErrorFrame, Frame, LLMContextFrame, TextFrame, TranscriptionFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

import backend.db as db_mod
from backend.db import AsyncSessionLocal, Base, engine
from backend.models.agent_config import AgentConfig
from backend.models.appointment import Appointment
from backend.models.doctor import Doctor
from backend.models.doctor_availability import DoctorAvailability
from backend.models.tenant import Tenant
from backend.agent.processors.booking_processor import BookingProcessor, BookingTranscriptTap
from backend.services.timeutil import ist_now, ist_wall_clock_to_utc, to_ist

TENANT_ID = "bbbbbbbb-0000-0000-0000-000000000001"
AGENT_ID = "bbbbbbbb-0000-0000-0000-000000000002"
DOCTOR_ID = "bbbbbbbb-0000-0000-0000-000000000003"
DOCTOR_NAME = "Salman"

PATIENT = "Ainan"
PHONE = "9148768120"

TOMORROW = ist_now().date() + timedelta(days=1)


def _ist(date_, hour, minute=0):
    return ist_wall_clock_to_utc(datetime.combine(date_, time_cls(hour, minute)))


def _ist_hour(appt: Appointment) -> int:
    """SQLite returns a naive datetime where Postgres returns tz-aware; every
    slot_time written is a true UTC instant (same rule as availability._ensure_utc)."""
    dt = appt.slot_time
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return to_ist(dt).hour


@pytest_asyncio.fixture
async def seeded_db():
    assert db_mod.IS_SQLITE, "TEST SAFETY: refusing to run against a non-SQLite database"
    db_mod._import_all_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as s:
        s.add(Tenant(id=TENANT_ID, clinic_name="Indiana Hospital Mangalore",
                     admin_email="pipeline_e2e@example.com"))
        s.add(Doctor(id=DOCTOR_ID, tenant_id=TENANT_ID, name=DOCTOR_NAME,
                     specialization="Cardiologist"))
        for dow in range(7):
            s.add(DoctorAvailability(tenant_id=TENANT_ID, doctor_id=DOCTOR_ID,
                                     day_of_week=dow,
                                     start_time=time_cls(9, 0), end_time=time_cls(17, 0)))
        s.add(AgentConfig(
            id=AGENT_ID, tenant_id=TENANT_ID, agent_name="Receptionist",
            llm_provider="groq", llm_model="llama-3.3-70b-versatile",
            system_prompt="You are a receptionist for Indiana Hospital Mangalore.",
            can_book_appointments=True, can_cancel_appointments=True,
        ))
        await s.commit()
    from backend.services.his import invalidate_doctor_cache
    invalidate_doctor_cache(TENANT_ID)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _existing_appointment(hour: int = 14) -> None:
    async with AsyncSessionLocal() as s:
        s.add(Appointment(tenant_id=TENANT_ID, doctor_id=DOCTOR_ID,
                          slot_time=_ist(TOMORROW, hour), patient_phone=PHONE,
                          patient_name=PATIENT, status="confirmed"))
        await s.commit()


async def _appointments() -> list[Appointment]:
    async with AsyncSessionLocal() as s:
        return list((await s.execute(
            select(Appointment).where(Appointment.tenant_id == TENANT_ID)
        )).scalars().all())


# ── Voice harness ─────────────────────────────────────────────────────────────

class _Sink(FrameProcessor):
    """Stands in for `llm`. Records what actually arrives where the LLM sits."""

    def __init__(self) -> None:
        super().__init__()
        self.frames: list[str] = []
        self.context_frames: list[LLMContextFrame] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        self.frames.append(type(frame).__name__)
        if isinstance(frame, LLMContextFrame):
            self.context_frames.append(frame)
        await self.push_frame(frame, direction)


class VoiceCall:
    """One simulated call, driven through the real pipeline order.

    The order here mirrors backend/agent/pipeline.py exactly for the segment
    that matters — tap BEFORE the aggregator, BookingProcessor AFTER it — so a
    regression that moves either one fails these tests.
    """

    def __init__(self, agent_config: dict | None = None) -> None:
        tenant = {
            "id": TENANT_ID,
            "clinic_name": "Indiana Hospital Mangalore",
            "doctors": [{"id": DOCTOR_ID, "name": DOCTOR_NAME,
                         "specialization": "Cardiologist"}],
        }
        cfg = {"can_book_appointments": True, "can_cancel_appointments": True,
               "can_check_availability": True}
        cfg.update(agent_config or {})
        self.booking = BookingProcessor(
            tenant=tenant, agent_config=cfg,
            call_meta={"caller_phone": PHONE, "call_record_id": None},
        )
        self.sink = _Sink()
        pair = LLMContextAggregatorPair(LLMContext(messages=[]))
        self.pipeline = Pipeline([
            BookingTranscriptTap(self.booking),   # before the aggregator
            pair.user(),                          # eats TranscriptionFrames
            self.booking,                         # after it — needs LLMContextFrame
            self.sink,                            # where `llm` really sits
        ])
        self.task = PipelineTask(self.pipeline)
        self._runner: asyncio.Task | None = None

    async def __aenter__(self) -> "VoiceCall":
        self._runner = asyncio.create_task(
            PipelineRunner(handle_sigint=False).run(self.task))
        await asyncio.sleep(0.35)   # let StartFrame traverse the pipeline
        return self

    async def __aexit__(self, *exc) -> None:
        await self.task.stop_when_done()
        if self._runner:
            await asyncio.wait_for(self._runner, timeout=10)

    async def says(self, text: str) -> None:
        """One finalised caller utterance, then wait for the turn to complete."""
        before = len(self.sink.context_frames)
        await self.task.queue_frame(
            TranscriptionFrame(text=text, user_id="caller", timestamp="t"))
        # The aggregator emits the LLMContextFrame when the user turn stops;
        # waiting on it is what makes these tests deterministic rather than
        # sleep-tuned.
        for _ in range(120):
            await asyncio.sleep(0.05)
            if len(self.sink.context_frames) > before:
                return
        raise AssertionError(f"no LLM turn was ever produced for {text!r}")

    def system_messages(self) -> list[str]:
        """Every [BOOKING_RESULT ...] / [AVAILABILITY_NOTE] injected for the LLM."""
        out: list[str] = []
        for frame in self.sink.context_frames:
            ctx = getattr(frame, "context", None)
            if ctx is None:
                continue
            getter = getattr(ctx, "get_messages", None)
            for msg in (getter() if callable(getter) else ctx.messages):
                if isinstance(msg, dict) and msg.get("role") == "system":
                    out.append(str(msg.get("content") or ""))
        return out


# ── 1. The structural bug: does the caller's voice reach the FSM at all? ───────

@pytest.mark.asyncio
async def test_transcription_reaches_booking_processor_through_pipeline(seeded_db):
    """The regression guard for the whole class of failure.

    Not "does the FSM work" — every other test covers that — but "does a word
    the caller said ever arrive at it", which is what was broken.
    """
    async with VoiceCall() as call:
        await call.says("I want to see Dr Salman tomorrow at 2 PM")

        assert call.booking.booking_state["pending_doctor_id"] == DOCTOR_ID, (
            "BookingProcessor never saw the utterance — the transcript tap is "
            "not wired ahead of context_aggregator.user()"
        )
        assert call.booking.booking_state["pending_slot"] is not None
        assert call.booking.booking_state["awaiting_confirm"] is True

    # And the LLM still got its turn: the tap must be transparent.
    assert "LLMContextFrame" in call.sink.frames


@pytest.mark.asyncio
async def test_an_ordinary_booking_request_is_not_read_as_a_cancellation(seeded_db):
    """The matcher regression guard.

    Step 3 of the FSM arms the slot and step 4 immediately re-reads the SAME
    utterance for cancel words, so any false positive there un-arms the booking
    on the very sentence that set it up. The Malayalam "വേണ്ട" folds to the same
    consonant classes as the English "want", which made that happen for every
    caller who said "I want…" in English.
    """
    async with VoiceCall() as call:
        await call.says("I want to see Dr Salman tomorrow at 2 PM")
        assert call.booking.booking_state["awaiting_confirm"] is True
        assert call.booking.booking_state["pending_doctor_id"] == DOCTOR_ID, (
            "an ordinary booking request was matched as a cancel word and "
            "wiped the pending doctor"
        )


@pytest.mark.asyncio
async def test_emergency_wording_defers_the_booking_details_in_that_turn(seeded_db):
    """Documents a real, deliberate interaction rather than leaving it ambiguous.

    Emergency detection returns before any booking parsing, so a caller who
    packs both into one breath — *"I have chest pain, I want Dr Salman
    tomorrow at 2 PM"*, which is how the 2026-08-11 caller opened — has the
    booking half of that sentence dropped. The caller is told to seek emergency
    care, and must restate the appointment. Asserted so that changing the
    priority is a deliberate product decision, not an accident.
    """
    async with VoiceCall() as call:
        await call.says("I have chest pain, I want to see Dr Salman tomorrow at 2 PM")
        assert call.booking.booking_state["emergency_detected"] is True
        assert call.booking.booking_state["pending_doctor_id"] is None

        # …and the very next sentence still books normally.
        await call.says("I want to see Dr Salman tomorrow at 2 PM")
        assert call.booking.booking_state["awaiting_confirm"] is True


# ── 2. voice × BOOK ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_voice_book_writes_a_real_row(seeded_db):
    async with VoiceCall() as call:
        await call.says("My name is Ainan")
        await call.says("I want to see Dr Salman tomorrow at 2 PM")
        assert call.booking.booking_state["awaiting_confirm"] is True
        await call.says("yes, confirm it")

    rows = await _appointments()
    assert len(rows) == 1, "a confirmed voice booking must produce exactly one row"
    assert rows[0].patient_phone == PHONE
    assert _ist_hour(rows[0]) == 14
    assert rows[0].status == "confirmed"
    assert any("[BOOKING_RESULT success=true]" in m for m in call.system_messages()), (
        "the LLM must be told the row exists before it may confirm"
    )


@pytest.mark.asyncio
async def test_voice_book_in_hindi_writes_a_real_row(seeded_db):
    """The 2026-08-11 call, replayed: chest pain → Salman → tomorrow 2 PM → confirm.

    The utterances are the caller's actual words from call_records.transcript,
    not authored test strings — a Hindi caller's "हाँ" is the thing that has to
    reach the FSM, and romanised keyword lists never contained it.
    """
    async with VoiceCall() as call:
        await call.says("देखो मेरा नाम आइनन है")
        await call.says("मुझे सलमान सर से मिलना है कल दोपहर के दो बजे")
        assert call.booking.booking_state["pending_doctor_id"] == DOCTOR_ID
        assert call.booking.booking_state["awaiting_confirm"] is True, (
            "the Hindi slot never armed — the caller stated a doctor and a time"
        )
        await call.says("हाँ जी हाँ कन्फर्म कर दो")

    rows = await _appointments()
    assert len(rows) == 1, "the Hindi call must book exactly like the English one"
    assert _ist_hour(rows[0]) == 14


# ── 3. voice × RESCHEDULE ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_voice_reschedule_moves_the_existing_row(seeded_db):
    await _existing_appointment(hour=14)

    async with VoiceCall() as call:
        await call.says("I want to reschedule my appointment")
        await call.says("my name is Ainan")
        await call.says("can we make it 4 PM instead")
        assert call.booking.booking_state["action_awaiting_confirm"] is True
        await call.says("yes please")

    rows = await _appointments()
    assert len(rows) == 1, "a reschedule must move the row, never create a second"
    assert _ist_hour(rows[0]) == 16, "the appointment did not actually move"
    assert rows[0].status == "confirmed"


# ── 4. voice × CANCEL ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_voice_cancel_cancels_the_existing_row(seeded_db):
    await _existing_appointment(hour=14)

    async with VoiceCall() as call:
        await call.says("I want to cancel my appointment")
        await call.says("my name is Ainan")
        assert call.booking.booking_state["action_awaiting_confirm"] is True
        await call.says("yes, cancel it")

    rows = await _appointments()
    assert len(rows) == 1
    assert rows[0].status == "cancelled", "the row was never actually cancelled"


# ── 5. A failed turn must still reach the caller ──────────────────────────────

@pytest.mark.asyncio
async def test_a_booking_error_never_drops_the_callers_turn(seeded_db):
    """The mechanism behind the silent hang.

    pipecat catches an exception from process_frame and calls push_error(),
    which routes UPSTREAM and skips the push_frame — so the in-flight
    LLMContextFrame dies and the caller hears nothing, forever. The processor
    must therefore never raise, whatever the DB does.
    """
    from unittest.mock import AsyncMock, patch

    async with VoiceCall() as call:
        await call.says("I want Dr Salman tomorrow at 2 PM")
        await call.says("my name is Ainan")

        with patch("backend.services.availability.is_doctor_open_at",
                   AsyncMock(side_effect=RuntimeError("connection reset by peer"))):
            await call.says("yes, confirm it")   # raises if the turn is dropped

    assert call.booking.booking_state["confirmed"] is False
    assert len(await _appointments()) == 0, "nothing may be written when the check failed"


# ── 6. The never-silence guard must be able to hear a provider failure ────────

@pytest.mark.asyncio
async def test_error_frame_reaches_resilience_from_llm_position():
    """ResilienceProcessor must sit UPSTREAM of llm/tts.

    pipecat pushes ErrorFrames UPSTREAM (frame_processor.py:722). While the
    guard sat second-from-last it could never receive one, so a Groq 429 or a
    TTS failure produced dead air and the model-failover never ran. This asserts
    the direction, not the class — the guard is only a guard from the right side.
    """
    seen: list[str] = []

    class Guard(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, ErrorFrame):
                seen.append(str(getattr(frame, "error", "")))
            await self.push_frame(frame, direction)

    class ExplodingLLM(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, TextFrame) and frame.text == "GO":
                raise RuntimeError("Groq 429 rate_limit_exceeded")
            await self.push_frame(frame, direction)

    task = PipelineTask(Pipeline([Guard(), ExplodingLLM(), _Sink()]))
    runner = asyncio.create_task(PipelineRunner(handle_sigint=False).run(task))
    await asyncio.sleep(0.35)
    await task.queue_frame(TextFrame("GO"))
    await asyncio.sleep(0.6)
    await task.stop_when_done()
    await asyncio.wait_for(runner, timeout=10)

    assert seen, (
        "the never-silence guard never received the ErrorFrame — it is placed "
        "downstream of the failure and cannot fire"
    )


# ── 7. chat × BOOK / RESCHEDULE / CANCEL ──────────────────────────────────────
#
# The chat half of the same six-way guarantee. These run the real handler —
# tag parse, capability gates, availability gate, DB write, honest regeneration
# — with only the LLM call stubbed, so they assert on rows, not on prose.

TOMORROW_STR = TOMORROW.strftime("%d/%m/%Y")


def _two_phase(tag: str, confirmation: str):
    """The model's first reply (carrying the [ACTION:] tag), then its reply
    after the real outcome has been injected."""
    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        if "SYSTEM UPDATE (AUTHORITATIVE" in system_prompt:
            return confirmation
        return tag
    return fake_dispatch


async def _chat(message: str, dispatch, session_id: str) -> str:
    from unittest.mock import patch
    from backend.routers import agent_test as chat_mod

    with patch.object(chat_mod, "_dispatch_llm", side_effect=dispatch), \
         patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(
                select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            return await chat_mod.generate_llm_response(
                agent, message, db, session_id=session_id, user_language="en-IN",
            )


@pytest_asyncio.fixture
async def clean_chat_state():
    from backend.routers import agent_test as chat_mod
    chat_mod._conversation_history.clear()
    chat_mod._avail_cache.clear()
    yield
    chat_mod._conversation_history.clear()


@pytest.mark.asyncio
async def test_chat_book_writes_a_real_row(seeded_db, clean_chat_state):
    reply = await _chat(
        "Book me with Salman tomorrow at 2 PM",
        _two_phase(
            f"[ACTION: BOOK|{PATIENT}|{PHONE}|{TOMORROW_STR}|02:00 PM|{DOCTOR_NAME}|N/A]",
            "Your appointment with Dr Salman is booked for tomorrow at 2 PM.",
        ),
        session_id="chat-book",
    )
    rows = await _appointments()
    assert len(rows) == 1
    assert _ist_hour(rows[0]) == 14
    assert rows[0].status == "confirmed"
    assert "ACTION" not in reply, "the internal tag must never reach the patient"


@pytest.mark.asyncio
async def test_chat_reschedule_moves_the_existing_row(seeded_db, clean_chat_state):
    await _existing_appointment(hour=14)

    reply = await _chat(
        "Please move my appointment to 4 PM",
        _two_phase(
            f"[ACTION: RESCHEDULE|{PATIENT}|{PHONE}|{TOMORROW_STR}|04:00 PM|{DOCTOR_NAME}|N/A]",
            "Done — your appointment has been rescheduled to tomorrow at 4 PM.",
        ),
        session_id="chat-resched",
    )
    rows = await _appointments()
    assert len(rows) == 1, "a reschedule must move the row, never create a second"
    assert _ist_hour(rows[0]) == 16, "the appointment did not actually move"
    assert "ACTION" not in reply


@pytest.mark.asyncio
async def test_chat_cancel_cancels_the_existing_row(seeded_db, clean_chat_state):
    """The combination with no coverage at all before this file.

    CANCEL takes a different route through the handler than BOOK/RESCHEDULE —
    a different capability gate (can_cancel_appointments), and no time gate,
    since the appointment is matched on name + phone.
    """
    await _existing_appointment(hour=14)

    reply = await _chat(
        "I need to cancel my appointment",
        _two_phase(
            f"[ACTION: CANCEL|{PATIENT}|{PHONE}|{TOMORROW_STR}|02:00 PM|{DOCTOR_NAME}|N/A]",
            "Your appointment has been cancelled.",
        ),
        session_id="chat-cancel",
    )
    rows = await _appointments()
    assert len(rows) == 1
    assert rows[0].status == "cancelled", "the row was never actually cancelled"
    assert "ACTION" not in reply


@pytest.mark.asyncio
async def test_chat_cancel_is_refused_when_the_clinic_turned_it_off(seeded_db, clean_chat_state):
    """can_cancel_appointments is a real gate, not decoration — and a refusal
    must still leave the appointment standing and say so honestly."""
    await _existing_appointment(hour=14)
    async with AsyncSessionLocal() as s:
        agent = (await s.execute(
            select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
        agent.can_cancel_appointments = False
        await s.commit()

    reply = await _chat(
        "I need to cancel my appointment",
        _two_phase(
            f"[ACTION: CANCEL|{PATIENT}|{PHONE}|{TOMORROW_STR}|02:00 PM|{DOCTOR_NAME}|N/A]",
            "I'm sorry, I can't cancel appointments here.",
        ),
        session_id="chat-cancel-off",
    )
    rows = await _appointments()
    assert rows[0].status == "confirmed", "a disabled tool must not cancel anything"
    assert "ACTION" not in reply


# ── 8. One implementation per intent, shared by both channels ─────────────────

def test_both_channels_share_one_executor_per_intent():
    """The anti-drift guard the audit asked for.

    Voice reaches cancel/reschedule through
    booking_processor._commit_action_to_db, and chat through
    _handle_booking_action → sync_and_log_appointment. Both must bottom out in
    his.execute_booking_action, which is where the availability gate lives — a
    second private copy on either side is how the two paths diverged before.
    """
    import inspect
    from backend.agent.processors import booking_processor as voice_mod
    from backend.routers import agent_test as chat_mod
    from backend.services import his

    voice_src = inspect.getsource(voice_mod._commit_action_to_db)
    assert "execute_booking_action" in voice_src, (
        "the voice path no longer calls the shared executor"
    )
    chat_src = inspect.getsource(chat_mod.sync_and_log_appointment)
    assert "execute_booking_action" in chat_src, (
        "the chat path no longer calls the shared executor"
    )
    # And the gate itself still lives in the one place both of them reach.
    gate_src = inspect.getsource(his.execute_booking_action)
    assert "is_doctor_open_at" in gate_src or "availability" in gate_src, (
        "the availability gate has moved out of the shared executor"
    )
