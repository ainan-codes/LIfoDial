# -*- coding: utf-8 -*-
"""
The voice agent's spoken promise must correspond to a database row.

Why this file exists
--------------------
On 2026-08-12 production held three appointments, every one of them with
``call_id IS NULL`` — i.e. every one from the chat channel, and not a single
booking from a phone call in the product's lifetime. The two calls made that
morning both ended with the agent telling the caller, in Hindi, that their
appointment was booked. Nothing had been written.

The cause was structural, not a bug in the state machine: the FSM
(``booking_processor.py``) can only commit when the CALLER names the doctor and
then says a confirm word in a turn of its own, and a real call does neither —
the AGENT proposes the doctor, and the caller answers "tomorrow 2 PM, I'll come".
Meanwhile nothing stopped the model from claiming success.

``processors/voice_action.py`` fixes that by giving voice the mechanism chat has
always had: the model emits an ``[ACTION: …]`` tag, the system executes it, and
only then is a confirmation spoken.

The rule for this file is the rule of test_booking_pipeline_e2e.py: **drive
frames through an assembled Pipeline, in the real relative order, and assert on
the database and on what would have been SPOKEN.** Nothing may call an internal
handler directly — that shortcut is exactly what let the original bug ship.

Run: python -m pytest backend/tests/test_voice_action_tag_e2e.py -v
"""

# ── TEST SAFETY: force a local SQLite DB *before* importing backend.db ─────────
import os

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_voice_action_tag.db"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-voice-action-tests")

import asyncio
from datetime import datetime, time as time_cls, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from pipecat.frames.frames import (
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TextFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

import backend.db as db_mod
from backend.agent.processors.booking_processor import BookingProcessor, BookingTranscriptTap
from backend.agent.processors.tag_scrub import TagScrubProcessor
from backend.agent.processors.voice_action import VoiceActionProcessor
from backend.db import AsyncSessionLocal, Base, engine
from backend.models.agent_config import AgentConfig
from backend.models.appointment import SOURCE_VOICE, Appointment
from backend.models.call_record import CallRecord
from backend.models.doctor import Doctor
from backend.models.doctor_availability import DoctorAvailability
from backend.models.tenant import Tenant
from backend.services.timeutil import ist_now, ist_wall_clock_to_utc, to_ist

TENANT_ID = "cccccccc-0000-0000-0000-000000000001"
AGENT_ID = "cccccccc-0000-0000-0000-000000000002"
DOCTOR_ID = "cccccccc-0000-0000-0000-000000000003"
CALL_ID = "cccccccc-0000-0000-0000-000000000004"
DOCTOR_NAME = "Salman"

PATIENT = "Ainan"
PHONE = "9148768120"

TOMORROW = ist_now().date() + timedelta(days=1)
TOMORROW_TAG = TOMORROW.strftime("%d/%m/%Y")


def _ist(date_, hour, minute=0):
    return ist_wall_clock_to_utc(datetime.combine(date_, time_cls(hour, minute)))


def _ist_hour(appt: Appointment) -> int:
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
                     admin_email="voice_action@example.com"))
        s.add(Doctor(id=DOCTOR_ID, tenant_id=TENANT_ID, name=DOCTOR_NAME,
                     specialization="Cardiologist"))
        for dow in range(7):
            s.add(DoctorAvailability(tenant_id=TENANT_ID, doctor_id=DOCTOR_ID,
                                     day_of_week=dow,
                                     start_time=time_cls(9, 0), end_time=time_cls(17, 0)))
        s.add(AgentConfig(
            id=AGENT_ID, tenant_id=TENANT_ID, agent_name="Receptionist",
            llm_provider="groq", llm_model="llama-3.3-70b-versatile",
            can_book_appointments=True, can_cancel_appointments=True,
        ))
        s.add(CallRecord(id=CALL_ID, tenant_id=TENANT_ID, agent_id=AGENT_ID))
        await s.commit()
    from backend.services.his import invalidate_doctor_cache
    invalidate_doctor_cache(TENANT_ID)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _appointments() -> list[Appointment]:
    async with AsyncSessionLocal() as s:
        return list((await s.execute(
            select(Appointment).where(Appointment.tenant_id == TENANT_ID)
            .order_by(Appointment.created_at.asc())
        )).scalars().all())


async def _seed_appointment(hour: int = 14, source: str = SOURCE_VOICE) -> None:
    async with AsyncSessionLocal() as s:
        s.add(Appointment(tenant_id=TENANT_ID, doctor_id=DOCTOR_ID,
                          slot_time=_ist(TOMORROW, hour), patient_phone=PHONE,
                          patient_name=PATIENT, status="confirmed", source=source))
        await s.commit()


# ── Harness ───────────────────────────────────────────────────────────────────

class ScriptedLLM(FrameProcessor):
    """Stands in for `llm`, replaying scripted replies.

    Mirrors pipecat's real text-LLM contract precisely, because that contract is
    what VoiceActionProcessor is built against:

      * an ``LLMContextFrame`` is CONSUMED (never forwarded) and answered with
        LLMFullResponseStart -> LLMTextFrame(s) -> LLMFullResponseEnd, pushed
        DOWNSTREAM (openai/base_llm.py:561);
      * that happens whatever DIRECTION the context frame arrived from, which is
        what makes the re-run work: voice_action pushes an LLMRunFrame downstream,
        the assistant aggregator turns it into a context frame pushed back
        UPSTREAM, and it lands here.

    Each reply is streamed in small chunks so a tag straddles frame boundaries,
    exactly as a real stream does.
    """

    def __init__(self, replies: list[str]) -> None:
        super().__init__()
        self._replies = list(replies)
        #: The context messages as they stood at each generation — what the model
        #: was actually told, in order.
        self.seen_messages: list[list[dict]] = []
        self.calls: int = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if not isinstance(frame, LLMContextFrame):
            await self.push_frame(frame, direction)
            return

        self.calls += 1
        ctx = frame.context
        getter = getattr(ctx, "get_messages", None)
        msgs = getter() if callable(getter) else list(ctx.messages)
        self.seen_messages.append([m for m in msgs if isinstance(m, dict)])

        reply = self._replies.pop(0) if self._replies else "Okay."
        await self.push_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
        for i in range(0, len(reply), 7):
            await self.push_frame(LLMTextFrame(reply[i:i + 7]), FrameDirection.DOWNSTREAM)
        await self.push_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)

    def system_lines(self) -> list[str]:
        """Every system message the model was ever shown, flattened."""
        out: list[str] = []
        for msgs in self.seen_messages:
            for m in msgs:
                if m.get("role") == "system":
                    out.append(str(m.get("content") or ""))
        return out


class SpeakerSink(FrameProcessor):
    """Stands in for `tts`: whatever reaches here is what the caller HEARS."""

    def __init__(self) -> None:
        super().__init__()
        self.chunks: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame) and direction == FrameDirection.DOWNSTREAM:
            self.chunks.append(frame.text or "")
        await self.push_frame(frame, direction)

    @property
    def spoken(self) -> str:
        return "".join(self.chunks).strip()


class VoiceCall:
    """One simulated call, in the real pipeline order.

    The segment that matters is reproduced exactly as backend/agent/pipeline.py
    assembles it:

        tap -> user aggregator -> booking FSM -> llm -> voice_action
            -> tag_scrub -> tts -> assistant aggregator (LAST)

    so a regression that moves voice_action out from between `llm` and
    `tag_scrub`, or that puts the assistant aggregator anywhere but last, fails
    these tests instead of failing a call.
    """

    def __init__(self, replies: list[str], agent_config: dict | None = None,
                 call_record_id: str | None = CALL_ID) -> None:
        tenant = {
            "id": TENANT_ID,
            "clinic_name": "Indiana Hospital Mangalore",
            "doctors": [{"id": DOCTOR_ID, "name": DOCTOR_NAME,
                         "specialization": "Cardiologist"}],
        }
        cfg = {"can_book_appointments": True, "can_cancel_appointments": True,
               "can_check_availability": True}
        cfg.update(agent_config or {})
        call_meta = {"caller_phone": PHONE, "call_record_id": call_record_id}

        context = LLMContext(messages=[])
        pair = LLMContextAggregatorPair(context)

        self.booking = BookingProcessor(tenant=tenant, agent_config=cfg, call_meta=call_meta)
        self.action = VoiceActionProcessor(
            context=context, tenant=tenant, agent_config=cfg, call_meta=call_meta)
        self.llm = ScriptedLLM(replies)
        self.speaker = SpeakerSink()

        self.pipeline = Pipeline([
            BookingTranscriptTap(self.booking, on_new_turn=self.action.reset_turn),
            pair.user(),
            self.booking,
            self.llm,
            self.action,
            TagScrubProcessor(),
            self.speaker,
            pair.assistant(),      # MUST be last — it turns LLMRunFrame into a re-run
        ])
        self.task = PipelineTask(self.pipeline)
        self._runner: asyncio.Task | None = None

    async def __aenter__(self) -> "VoiceCall":
        self._runner = asyncio.create_task(
            PipelineRunner(handle_sigint=False).run(self.task))
        await asyncio.sleep(0.35)
        return self

    async def __aexit__(self, *exc) -> None:
        await self.task.stop_when_done()
        if self._runner:
            await asyncio.wait_for(self._runner, timeout=15)

    async def says(self, text: str, *, expect_generations: int = 1) -> None:
        """One finalised caller utterance; waits until the agent has finished
        producing (and, where applicable, re-producing) its reply."""
        target = self.llm.calls + expect_generations
        await self.task.queue_frame(
            TranscriptionFrame(text=text, user_id="caller", timestamp="t"))
        for _ in range(200):
            await asyncio.sleep(0.05)
            if self.llm.calls >= target:
                # Let the last reply drain all the way to the speaker.
                await asyncio.sleep(0.25)
                return
        raise AssertionError(
            f"expected {expect_generations} LLM generation(s) for {text!r}, "
            f"got {self.llm.calls - (target - expect_generations)}"
        )


def _book_tag(time_str: str = "02:00 PM", *, name: str = PATIENT, phone: str = PHONE,
              date: str = TOMORROW_TAG, doctor: str = DOCTOR_NAME) -> str:
    return f"[ACTION: BOOK|{name}|{phone}|{date}|{time_str}|{doctor}|Chest pain]"


# ── 1. The compliant path ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_tag_first_reply_books_a_real_row(seeded_db):
    """The fix, end to end: the model emits a tag, a row exists."""
    async with VoiceCall([_book_tag(), "Your appointment with Dr Salman is confirmed for 2 PM."]) as call:
        await call.says("Book me with Dr Salman tomorrow at 2 PM, I'm Ainan",
                        expect_generations=2)

    rows = await _appointments()
    assert len(rows) == 1, f"the voice call did not create an appointment: {rows}"
    assert rows[0].patient_name == PATIENT
    assert rows[0].status == "confirmed"
    assert _ist_hour(rows[0]) == 14
    assert rows[0].source == SOURCE_VOICE, "a phone booking must be attributed to voice"
    assert rows[0].call_id == CALL_ID


@pytest.mark.asyncio
async def test_nothing_is_spoken_until_the_row_exists(seeded_db):
    """The ORDER is the guarantee, not just the row.

    The model's tag-turn text is written before the outcome is known, so it is
    discarded: the only words the caller hears come from the reply generated
    AFTER the write, with the real result injected.
    """
    async with VoiceCall([
        _book_tag() + " Your appointment is booked!",   # a claim made too early
        "Done — Dr Salman will see you at 2 PM tomorrow.",
    ]) as call:
        await call.says("Book me with Dr Salman tomorrow at 2 PM, I'm Ainan",
                        expect_generations=2)
        spoken = call.speaker.spoken
        lines = call.llm.system_lines()

    assert "Your appointment is booked!" not in spoken, (
        "the pre-outcome text was spoken to the caller — it must be discarded"
    )
    assert "[ACTION" not in spoken and "BOOKING_RESULT" not in spoken, (
        f"a machine tag reached TTS: {spoken!r}"
    )
    assert spoken == "Done — Dr Salman will see you at 2 PM tomorrow."
    assert any("[BOOKING_RESULT success=true]" in line for line in lines), (
        "the model was not told the real outcome before it spoke"
    )
    assert len(await _appointments()) == 1


@pytest.mark.asyncio
async def test_an_ordinary_turn_is_streamed_untouched_and_costs_nothing(seeded_db):
    """Every non-action turn must be exactly as fast as before this processor
    existed: no buffering, no extra generation, no DB work."""
    reply = "We're open from 9 AM to 5 PM, Monday to Saturday."
    async with VoiceCall([reply]) as call:
        await call.says("What are your timings?")
        assert call.speaker.spoken == reply
        assert call.llm.calls == 1, "an ordinary turn triggered a second LLM call"

    assert await _appointments() == []


# ── 2. The production failure: a claim with no tag ─────────────────────────────

@pytest.mark.asyncio
async def test_a_fabricated_hindi_confirmation_is_repaired_into_a_real_booking(seeded_db):
    """The exact 2026-08-12 production reply, verbatim.

    The agent told the caller their appointment was booked and emitted no tag, so
    nothing was written. The model is re-prompted, emits the tag it forgot, and
    the booking becomes real.
    """
    fabricated = "बिल्कुल, ऐनान जी! कल दोपहर 2 बजे डॉक्टर सलमान से आपकी अपॉइंटमेंट बुक कर दी गई है।"
    async with VoiceCall([
        fabricated,
        _book_tag(),
        "जी हाँ, कल दोपहर 2 बजे डॉक्टर सलमान के साथ आपकी अपॉइंटमेंट कन्फर्म है।",
    ]) as call:
        await call.says("मेरा नाम ऐनान है, कल दोपहर के दो बजे डॉक्टर सलमान के पास आ जाऊँगा",
                        expect_generations=3)
        lines = call.llm.system_lines()

    rows = await _appointments()
    assert len(rows) == 1, (
        "a fabricated confirmation was left standing with no appointment behind it"
    )
    assert rows[0].source == SOURCE_VOICE
    assert any("did NOT emit an [ACTION: ...] tag" in line for line in lines), (
        "the model was never told its confirmation had written nothing"
    )


@pytest.mark.asyncio
async def test_the_repair_is_attempted_only_once_per_utterance(seeded_db):
    """A model that keeps claiming success without a tag must not loop forever."""
    fabricated = "Your appointment is booked."
    async with VoiceCall([fabricated, fabricated, fabricated]) as call:
        await call.says("Book me with Dr Salman tomorrow at 2 PM", expect_generations=2)
        await asyncio.sleep(0.5)
        assert call.llm.calls == 2, (
            f"the repair looped: {call.llm.calls} generations for one utterance"
        )

    assert await _appointments() == []


# ── 3. Failures must never be spoken as confirmations ─────────────────────────

@pytest.mark.asyncio
async def test_a_slot_outside_the_doctors_hours_is_refused_not_confirmed(seeded_db):
    """The availability gate's answer has to reach the caller as a refusal."""
    async with VoiceCall([
        _book_tag("08:00 PM"),                      # the clinic closes at 5
        "Sorry, Dr Salman isn't available at 8 PM. He's free at 9 AM or 10 AM.",
    ]) as call:
        await call.says("Book me with Dr Salman tomorrow at 8 PM, I'm Ainan",
                        expect_generations=2)
        lines = call.llm.system_lines()

    assert await _appointments() == [], "an unavailable slot was written anyway"
    assert any("[BOOKING_RESULT success=false]" in line for line in lines)
    assert any("does not consult at that time" in line for line in lines), (
        f"the model was not told WHY it failed: {lines!r}"
    )


@pytest.mark.asyncio
async def test_a_taken_slot_is_refused_with_real_alternatives(seeded_db):
    await _seed_appointment(hour=14)
    async with VoiceCall([
        _book_tag("02:00 PM", name="Rakesh", phone="9008007001"),
        "Sorry, 2 PM is taken. Dr Salman is free at 9 AM.",
    ]) as call:
        await call.says("Book me with Dr Salman tomorrow at 2 PM, I'm Rakesh",
                        expect_generations=2)
        lines = call.llm.system_lines()

    rows = await _appointments()
    assert len(rows) == 1, "the same doctor+slot was double-booked"
    assert any("ALREADY BOOKED" in line for line in lines)
    assert any("IS free at:" in line for line in lines), (
        "a slot conflict must be answered with the doctor's real open times"
    )


@pytest.mark.asyncio
async def test_a_tag_with_no_real_time_books_nothing(seeded_db):
    """parse_slot_datetime would silently fall back to midnight — refuse instead."""
    async with VoiceCall([
        _book_tag("N/A"),
        "What time would suit you?",
    ]) as call:
        await call.says("Book me with Dr Salman tomorrow, I'm Ainan", expect_generations=2)
        lines = call.llm.system_lines()

    assert await _appointments() == []
    assert any("No valid TIME was given" in line for line in lines)


@pytest.mark.asyncio
async def test_a_tag_with_a_placeholder_name_books_nothing(seeded_db):
    """A row named "N/A" can never be found again to cancel or reschedule."""
    async with VoiceCall([
        _book_tag(name="N/A"),
        "Could I have your name, please?",
    ]) as call:
        await call.says("Book me with Dr Salman tomorrow at 2 PM", expect_generations=2)
        lines = call.llm.system_lines()

    assert await _appointments() == []
    assert any("you have not asked the caller for their name" in line for line in lines)


@pytest.mark.asyncio
async def test_a_clinic_that_turned_booking_off_cannot_be_booked_through(seeded_db):
    async with VoiceCall(
        [_book_tag(), "I can't book that on this line."],
        agent_config={"can_book_appointments": False},
    ) as call:
        await call.says("Book me with Dr Salman tomorrow at 2 PM, I'm Ainan",
                        expect_generations=2)
        lines = call.llm.system_lines()

    assert await _appointments() == []
    assert any("turned off" in line for line in lines)


@pytest.mark.asyncio
async def test_a_doctor_this_clinic_does_not_have_books_nothing(seeded_db):
    async with VoiceCall([
        _book_tag(doctor="Meredith Grey"),
        "We don't have that doctor. We have Salman, a cardiologist.",
    ]) as call:
        await call.says("Book me with Dr Grey tomorrow at 2 PM, I'm Ainan",
                        expect_generations=2)
        lines = call.llm.system_lines()

    assert await _appointments() == []
    assert any("not at this clinic" in line for line in lines)
    assert any(DOCTOR_NAME in line for line in lines), (
        "the model was not given the clinic's real roster to offer instead"
    )


# ── 4. Cancel and reschedule ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_via_tag_cancels_the_real_row(seeded_db):
    await _seed_appointment(hour=14)
    async with VoiceCall([
        f"[ACTION: CANCEL|{PATIENT}|{PHONE}|{TOMORROW_TAG}|02:00 PM|{DOCTOR_NAME}|N/A]",
        "Your appointment has been cancelled.",
    ]) as call:
        await call.says("I want to cancel my appointment, this is Ainan",
                        expect_generations=2)

    rows = await _appointments()
    assert len(rows) == 1
    assert rows[0].status == "cancelled", "the appointment was never actually cancelled"


@pytest.mark.asyncio
async def test_reschedule_via_tag_moves_the_real_row(seeded_db):
    await _seed_appointment(hour=14)
    async with VoiceCall([
        f"[ACTION: RESCHEDULE|{PATIENT}|{PHONE}|{TOMORROW_TAG}|04:00 PM|N/A|N/A]",
        "Moved to 4 PM tomorrow.",
    ]) as call:
        await call.says("Please move my appointment to 4 PM, this is Ainan",
                        expect_generations=2)

    rows = await _appointments()
    assert len(rows) == 1
    assert _ist_hour(rows[0]) == 16, "the appointment did not move"
    assert rows[0].status == "confirmed"
    assert rows[0].source == SOURCE_VOICE, (
        "'Booked Via' records how the appointment was CREATED — a later "
        "reschedule must not rewrite it"
    )


@pytest.mark.asyncio
async def test_a_cancel_for_someone_with_no_appointment_says_so(seeded_db):
    async with VoiceCall([
        f"[ACTION: CANCEL|Nobody|9000000000|{TOMORROW_TAG}|02:00 PM|{DOCTOR_NAME}|N/A]",
        "I couldn't find an appointment under that name.",
    ]) as call:
        await call.says("Cancel my appointment, I'm Nobody", expect_generations=2)
        lines = call.llm.system_lines()

    assert any("no active appointment on that phone number" in line for line in lines)
    # And it must not restart the spell-your-name loop that burned 280 seconds of
    # a live call: the lookup is script-independent now, so asking again cannot
    # change the answer.
    assert any("Do NOT ask the caller to spell" in line for line in lines)


# ── 5. Bounds and safety ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_one_utterance_can_only_produce_one_action(seeded_db):
    """The re-run is told not to emit another tag. If it does anyway, executing
    it would be a second write for one request — and, worse, an endless
    execute/re-run cycle."""
    async with VoiceCall([
        _book_tag("02:00 PM"),
        # A disobedient re-run: confirms AND emits a second tag.
        _book_tag("03:00 PM") + " Booked for 3 PM.",
    ]) as call:
        await call.says("Book me with Dr Salman tomorrow at 2 PM, I'm Ainan",
                        expect_generations=2)
        await asyncio.sleep(0.5)
        assert call.llm.calls == 2, f"the action/re-run cycle looped: {call.llm.calls}"

    rows = await _appointments()
    assert len(rows) == 1, "one utterance produced two appointments"
    assert _ist_hour(rows[0]) == 14, "the ignored second tag was executed anyway"


@pytest.mark.asyncio
async def test_a_call_can_book_then_reschedule_in_separate_turns(seeded_db):
    """The per-utterance cap must lift when the caller speaks again — that is
    what BookingTranscriptTap's new-turn hook is for."""
    async with VoiceCall([
        _book_tag("02:00 PM"), "Booked for 2 PM.",
        f"[ACTION: RESCHEDULE|{PATIENT}|{PHONE}|{TOMORROW_TAG}|04:00 PM|N/A|N/A]",
        "Moved to 4 PM.",
    ]) as call:
        await call.says("Book me with Dr Salman tomorrow at 2 PM, I'm Ainan",
                        expect_generations=2)
        await call.says("Actually make it 4 PM instead", expect_generations=2)

    rows = await _appointments()
    assert len(rows) == 1
    assert _ist_hour(rows[0]) == 16, "the second turn's action never ran"


@pytest.mark.asyncio
async def test_a_malformed_tag_never_leaves_the_caller_in_silence(seeded_db):
    """The model is told to reply with the tag and nothing else, so a tag it
    mangles leaves NOTHING to speak. Silence is the worst outcome on a phone
    call — re-prompt instead."""
    async with VoiceCall([
        "[ACTION: BOOK|only|two]",            # unparseable: too few fields
        "Sorry, what time would you like?",
    ]) as call:
        await call.says("Book me with Dr Salman tomorrow", expect_generations=2)
        spoken = call.speaker.spoken

    assert spoken == "Sorry, what time would you like?"
    assert "[ACTION" not in spoken


@pytest.mark.asyncio
async def test_ordinary_bracketed_speech_is_still_spoken(seeded_db):
    """Holding is triggered by a leading bracket, so prose that happens to start
    with one must be released, not swallowed."""
    reply = "[laughs] Of course, we're open until 5 PM."
    async with VoiceCall([reply]) as call:
        await call.says("Are you open late?")
        assert "we're open until 5 PM" in call.speaker.spoken

    assert await _appointments() == []


@pytest.mark.asyncio
async def test_the_day_the_caller_said_beats_the_day_the_model_calculated(seeded_db):
    """The live 2026-08-12 booking: caller said "कल" (tomorrow), model wrote a date
    three days out, and a real appointment was created on it.

    The caller's own words reach the executor through call_meta, which
    BookingProcessor fills in from the transcriptions the executor cannot see.
    """
    wrong_date = (ist_now().date() + timedelta(days=3)).strftime("%d/%m/%Y")
    async with VoiceCall([
        _book_tag("03:00 PM", date=wrong_date),
        "कल दोपहर 3 बजे डॉक्टर सलमान के साथ आपकी अपॉइंटमेंट कन्फर्म है।",
    ]) as call:
        await call.says("मेरा नाम ऐनान है, कल दोपहर 3 बजे डॉक्टर सलमान के पास",
                        expect_generations=2)

    rows = await _appointments()
    assert len(rows) == 1, rows
    booked = to_ist(rows[0].slot_time if rows[0].slot_time.tzinfo
                    else rows[0].slot_time.replace(tzinfo=timezone.utc))
    assert booked.date() == TOMORROW, (
        f"the caller said tomorrow ({TOMORROW}) and got {booked.date()}"
    )
    assert booked.hour == 15


@pytest.mark.asyncio
async def test_promising_to_cancel_without_acting_is_repaired(seeded_db):
    """The live 2026-08-12 cancel call, verbatim: the agent said it would begin
    cancelling, emitted no tag, and cancelled nothing in 280 seconds while the
    caller asked "हो गया क्या?" over and over."""
    await _seed_appointment(hour=15)
    promise = "मैं इस अपॉइंटमेंट को कैंसिल करने की प्रक्रिया शुरू करूंगा।"
    async with VoiceCall([
        promise,
        f"[ACTION: CANCEL|{PATIENT}|{PHONE}|N/A|N/A|N/A|N/A]",
        "आपका अपॉइंटमेंट कैंसिल कर दिया गया है।",
    ]) as call:
        await call.says("मुझे एक अपॉइंटमेंट कैंसिल करना है, मेरा नाम ऐनान है",
                        expect_generations=3)
        lines = call.llm.system_lines()

    rows = await _appointments()
    assert rows[0].status == "cancelled", "the promised cancellation never happened"
    assert any("did NOT emit an [ACTION: ...] tag" in line for line in lines)


@pytest.mark.asyncio
async def test_the_agent_is_told_the_callers_real_appointments(seeded_db):
    """So a cancel is a confirmation, not an interrogation. On the live call the
    agent asked which doctor, which date and which time — all three were rows in
    the database, keyed on the number the caller was calling from."""
    await _seed_appointment(hour=15)
    async with VoiceCall(["मैं देखता हूँ।"]) as call:
        await call.says(f"मेरा नाम ऐनान है और मेरा नंबर {PHONE} है")
        lines = call.llm.system_lines()

    joined = "\n".join(lines)
    assert "EXISTING APPOINTMENTS" in joined, (
        "the agent was never shown the appointment it was about to discuss"
    )
    assert DOCTOR_NAME in joined
    assert "3:00 PM" in joined or "3 PM" in joined.upper()


@pytest.mark.asyncio
async def test_a_caller_cannot_dictate_an_action_tag(seeded_db):
    """The caller's own words must never be executed.

    TranscriptionFrame subclasses TextFrame in pipecat 1.5, so a processor that
    reads "any TextFrame going downstream" as model output would treat a caller
    reciting a tag as an instruction — and would also hold their speech back from
    the LLM. The user aggregator consumes transcriptions long before this point,
    and this test is what keeps that assumption honest.
    """
    async with VoiceCall(["I'm sorry, I can't do that."]) as call:
        await call.says(f"Please book: {_book_tag()}")
        assert call.llm.calls == 1

    assert await _appointments() == [], "a tag the CALLER spoke was executed"


@pytest.mark.asyncio
async def test_the_call_record_shows_the_booking(seeded_db):
    """Overview's resolution rate and the All Calls status read call_records —
    a real booking that leaves them untouched reads as an unresolved call."""
    async with VoiceCall([_book_tag(), "Confirmed for 2 PM."]) as call:
        await call.says("Book me with Dr Salman tomorrow at 2 PM, I'm Ainan",
                        expect_generations=2)

    async with AsyncSessionLocal() as s:
        rec = (await s.execute(select(CallRecord).where(CallRecord.id == CALL_ID))).scalar_one()
        assert rec.outcome == "booked"
        assert rec.booking_successful is True


@pytest.mark.asyncio
async def test_a_second_confirm_on_the_same_call_cannot_double_book(seeded_db):
    """Idempotency is keyed on the call record, and it is what lets the FSM and
    the tag executor both write without producing two rows for one call."""
    async with VoiceCall([
        _book_tag("02:00 PM"), "Booked for 2 PM.",
        _book_tag("02:00 PM"), "Still booked for 2 PM.",
    ]) as call:
        await call.says("Book me with Dr Salman tomorrow at 2 PM, I'm Ainan",
                        expect_generations=2)
        await call.says("Sorry, did that go through? Book it again", expect_generations=2)

    assert len(await _appointments()) == 1, "one call produced two appointments"
