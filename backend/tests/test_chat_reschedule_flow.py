# -*- coding: utf-8 -*-
"""
The RESCHEDULE flow on the chat/embed path, end to end.

Reproduces the 2026-08-11 Indiana Hospital transcript, in which three separate
things went wrong at once:

  1. The model emitted "[ ACTION: RESCHEDULE|Ainan|9090909090|…|03:00 PM|Rajesh|N/A ]"
     — with a space after the bracket. Neither the strict parser nor the
     scrubber tolerated that space, so NOTHING was executed and the raw tag was
     shown to the patient verbatim.
  2. Availability was never checked for a reschedule at all. The agent admitted
     it had no slot data for Dr Rajesh, then confirmed a specific time anyway.
  3. Because nothing ever executed, every "is it done?" got another stall.

The whole real handler path runs here; only the LLM call is stubbed.

Run: python -m pytest backend/tests/test_chat_reschedule_flow.py -v
"""

# ── TEST SAFETY: force a local SQLite DB *before* importing backend.db ─────────
import os
os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_chat_reschedule_flow.db"

from datetime import datetime, time as time_cls, timedelta, timezone

import pytest
import pytest_asyncio
from unittest.mock import patch
from sqlalchemy import select

import backend.db as db_mod
from backend.db import AsyncSessionLocal, engine, Base
from backend.models.agent_config import AgentConfig
from backend.models.appointment import Appointment
from backend.models.doctor import Doctor
from backend.models.doctor_availability import DoctorAvailability
from backend.models.tenant import Tenant
from backend.routers import agent_test as chat_mod
from backend.services.timeutil import ist_now, ist_wall_clock_to_utc, to_ist

TENANT_ID = "aaaaaaaa-0000-0000-0000-000000000001"
AGENT_ID = "aaaaaaaa-0000-0000-0000-000000000002"
DOCTOR_ID = "aaaaaaaa-0000-0000-0000-000000000003"
DOCTOR_NAME = "Rajesh"
OTHER_DOCTOR_ID = "aaaaaaaa-0000-0000-0000-000000000004"
OTHER_DOCTOR_NAME = "Nazima khan"

PATIENT = "Ainan"
PHONE = "9090909090"

# The live clinic's real shape: 09:00–17:00, every day. Tomorrow, so nothing
# depends on the time of day the suite runs.
TOMORROW = ist_now().date() + timedelta(days=1)
TOMORROW_STR = TOMORROW.strftime("%d/%m/%Y")


def _ist(date_, hour, minute=0):
    return ist_wall_clock_to_utc(datetime.combine(date_, time_cls(hour, minute)))


@pytest_asyncio.fixture
async def seeded_db():
    assert db_mod.IS_SQLITE, "TEST SAFETY: refusing to run against a non-SQLite database"
    db_mod._import_all_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as s:
        s.add(Tenant(id=TENANT_ID, clinic_name="Indiana Hospital Mangalore",
                     admin_email="indiana_test@example.com"))
        s.add(Doctor(id=DOCTOR_ID, tenant_id=TENANT_ID, name=DOCTOR_NAME,
                     specialization="General Physician"))
        s.add(Doctor(id=OTHER_DOCTOR_ID, tenant_id=TENANT_ID, name=OTHER_DOCTOR_NAME,
                     specialization="Paediatrician"))
        for dow in range(7):
            for doc_id in (DOCTOR_ID, OTHER_DOCTOR_ID):
                s.add(DoctorAvailability(tenant_id=TENANT_ID, doctor_id=doc_id,
                                         day_of_week=dow,
                                         start_time=time_cls(9, 0), end_time=time_cls(17, 0)))
        s.add(AgentConfig(
            id=AGENT_ID, tenant_id=TENANT_ID, agent_name="Receptionist",
            llm_provider="groq", llm_model="llama-3.3-70b-versatile",
            system_prompt="You are a receptionist for Indiana Hospital Mangalore.",
            can_book_appointments=True, can_cancel_appointments=True,
        ))
        # The appointment the patient wants moved: tomorrow, 2 PM.
        s.add(Appointment(tenant_id=TENANT_ID, doctor_id=DOCTOR_ID,
                          slot_time=_ist(TOMORROW, 14), patient_phone=PHONE,
                          patient_name=PATIENT, status="confirmed"))
        await s.commit()
    chat_mod._conversation_history.clear()
    # The availability digest is cached for 30s in-process; a stale entry
    # would leak one test's schedule into the next.
    chat_mod._avail_cache.clear()
    from backend.services.his import invalidate_doctor_cache
    invalidate_doctor_cache(TENANT_ID)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def _ist_hour(appt: Appointment) -> int:
    """The appointment's IST hour. SQLite hands back a naive datetime where
    Postgres returns a tz-aware one; every slot_time written is a true UTC
    instant, so labelling a naive value UTC is always correct here (same rule
    as availability._ensure_utc)."""
    dt = appt.slot_time
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return to_ist(dt).hour


async def _the_appointment() -> Appointment:
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(Appointment).where(
            Appointment.tenant_id == TENANT_ID))).scalars().first()


async def _agent():
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()


def _dispatcher(phase1: str, phase2: str = "Done — your appointment has been rescheduled.",
                captured: dict | None = None):
    """Two-phase LLM stub: the model's first reply, then its reply after the
    real outcome has been injected."""
    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        if "SYSTEM UPDATE (AUTHORITATIVE" in system_prompt:
            if captured is not None:
                captured["regen_system"] = system_prompt
            return phase2
        if captured is not None:
            captured["first_system"] = system_prompt
        return phase1
    return fake_dispatch


async def _chat(message: str, dispatch, session_id: str) -> str:
    with patch.object(chat_mod, "_dispatch_llm", side_effect=dispatch), \
         patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(
                select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            return await chat_mod.generate_llm_response(
                agent, message, db, session_id=session_id, user_language="en-IN",
            )


# ── 1. The exact production leak ──────────────────────────────────────────────

def test_space_padded_action_tag_is_parsed_and_never_leaks():
    """The verbatim string the patient was shown on 2026-08-11."""
    leaked = "[ ACTION: RESCHEDULE|Ainan|9090909090|11/08/2026|03:00 PM|Rajesh|N/A ]"

    m = chat_mod._ACTION_RE.search(leaked)
    assert m is not None, "the space after '[' must not stop the tag being executed"
    assert m.group(1).upper() == "RESCHEDULE"
    assert m.group(2).strip() == "Ainan"
    assert m.group(5).strip() == "03:00 PM"
    assert m.group(6).strip() == "Rajesh"

    assert chat_mod._scrub_reply(leaked) == ""
    assert chat_mod._is_only_a_tag(leaked) is True
    assert "ACTION" not in chat_mod._scrub_reply(f"Okay. {leaked} Anything else?")


def test_scrub_handles_every_tag_shape_seen_so_far():
    s = chat_mod._scrub_reply
    assert "[" not in s("Sure. [ACTION: None]")
    assert "[" not in s("Sure. [ action : BOOK|a|b|c|d|e|f ]")
    assert "[" not in s("Okay [BOOKING_RESULT success=true] done")
    assert "[" not in s("Right [AVAILABILITY_NOTE] 3 PM")
    # Cut off by the token cap mid-tag — no closing bracket ever arrives.
    assert s("Booking that now. [ ACTION: RESCHEDULE|Ainan|909090") == "Booking that now."
    # Ordinary prose in brackets is NOT a machine tag and must survive.
    assert s("Dr Rajesh (General Physician) [main building] is free.") == \
        "Dr Rajesh (General Physician) [main building] is free."


# ── 2. Reschedule really executes ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reschedule_to_an_open_slot_actually_moves_the_appointment(seeded_db):
    captured = {}
    dispatch = _dispatcher(
        f"Okay. [ ACTION: RESCHEDULE|{PATIENT}|{PHONE}|{TOMORROW_STR}|03:00 PM|{DOCTOR_NAME}|N/A ]",
        "Your appointment has been rescheduled to 3 PM tomorrow.",
        captured,
    )
    reply = await _chat(f"Reschedule it to tomorrow at 3pm. {PATIENT}, {PHONE}.",
                        dispatch, "s-resched-ok")

    appt = await _the_appointment()
    assert _ist_hour(appt) == 15, \
        f"the row must really have moved to 3 PM IST, got {appt.slot_time}"
    assert appt.status == "confirmed"

    # The patient is TOLD, in a reply with no machine tag and no stall.
    assert "ACTION" not in reply and "[" not in reply
    assert chat_mod._asserts_completion(reply, "RESCHEDULE"), reply
    assert not chat_mod._promises_followup(reply)
    assert "[BOOKING_RESULT success=true]" in captured["regen_system"]


@pytest.mark.asyncio
async def test_reschedule_survives_a_tag_the_model_pads_with_spaces(seeded_db):
    """Same as above but the reply is ONLY the tag — the production shape. It
    must still execute, and must never be shown as-is."""
    dispatch = _dispatcher(
        f"[ ACTION: RESCHEDULE|{PATIENT}|{PHONE}|{TOMORROW_STR}|03:00 PM|{DOCTOR_NAME}|N/A ]",
        "Your appointment has been moved to 3 PM tomorrow.",
    )
    reply = await _chat("yes please", dispatch, "s-resched-tagonly")

    appt = await _the_appointment()
    assert _ist_hour(appt) == 15
    assert reply.strip() != ""
    assert "ACTION" not in reply


# ── 3. Check before confirm ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reschedule_to_a_taken_slot_is_refused_and_offers_real_times(seeded_db):
    # Someone else already has 3 PM tomorrow with the same doctor.
    async with AsyncSessionLocal() as s:
        s.add(Appointment(tenant_id=TENANT_ID, doctor_id=DOCTOR_ID,
                          slot_time=_ist(TOMORROW, 15), patient_phone="9111111111",
                          patient_name="Someone Else", status="confirmed"))
        await s.commit()

    captured = {}
    dispatch = _dispatcher(
        f"Okay. [ ACTION: RESCHEDULE|{PATIENT}|{PHONE}|{TOMORROW_STR}|03:00 PM|{DOCTOR_NAME}|N/A ]",
        "Sorry, 3 PM is taken. I can do 9:00 AM, 9:30 AM or 10:00 AM.",
        captured,
    )
    reply = await _chat("Move it to 3 pm tomorrow", dispatch, "s-resched-taken")

    appt = await _the_appointment()
    assert _ist_hour(appt) == 14, "the original appointment must be untouched"

    update = captured["regen_system"].split("SYSTEM UPDATE (AUTHORITATIVE", 1)[1]
    update = update.split("--- APPOINTMENT BOOKING RULES", 1)[0]
    assert "[BOOKING_RESULT success=false]" in update
    assert "ALREADY BOOKED" in update
    assert "still stands at its original time" in update
    assert "IS free at:" in update, "must offer the doctor's real remaining times"
    assert not chat_mod._promises_followup(reply)


@pytest.mark.asyncio
async def test_reschedule_outside_consulting_hours_is_refused(seeded_db):
    captured = {}
    dispatch = _dispatcher(
        f"Okay. [ ACTION: RESCHEDULE|{PATIENT}|{PHONE}|{TOMORROW_STR}|09:00 PM|{DOCTOR_NAME}|N/A ]",
        "Dr Rajesh doesn't consult at 9 PM. He's free at 9:00 AM.",
        captured,
    )
    await _chat("Move it to 9 pm tomorrow", dispatch, "s-resched-late")

    appt = await _the_appointment()
    assert _ist_hour(appt) == 14, "nothing may move to a time outside the hours"
    update = captured["regen_system"]
    assert "[BOOKING_RESULT success=false]" in update
    assert "outside their hours" in update


@pytest.mark.asyncio
async def test_reschedule_into_the_past_is_refused(seeded_db):
    yesterday = (ist_now().date() - timedelta(days=1)).strftime("%d/%m/%Y")
    captured = {}
    dispatch = _dispatcher(
        f"Okay. [ ACTION: RESCHEDULE|{PATIENT}|{PHONE}|{yesterday}|10:00 AM|{DOCTOR_NAME}|N/A ]",
        "That time has already passed.",
        captured,
    )
    await _chat("Move it to yesterday 10 am", dispatch, "s-resched-past")

    appt = await _the_appointment()
    assert _ist_hour(appt) == 14
    assert "already passed" in captured["regen_system"]


@pytest.mark.asyncio
async def test_reschedule_with_no_real_time_never_moves_the_appointment(seeded_db):
    captured = {}
    dispatch = _dispatcher(
        f"Okay. [ ACTION: RESCHEDULE|{PATIENT}|{PHONE}|{TOMORROW_STR}|N/A|{DOCTOR_NAME}|N/A ]",
        "What time would you like instead?",
        captured,
    )
    await _chat("Move it to tomorrow", dispatch, "s-resched-notime")

    appt = await _the_appointment()
    assert _ist_hour(appt) == 14, "a blank time must never move the row to midnight"
    assert "No valid appointment TIME" in captured["regen_system"]


# ── 4. Honest failures ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reschedule_for_an_unknown_patient_says_so(seeded_db):
    captured = {}
    dispatch = _dispatcher(
        f"Okay. [ ACTION: RESCHEDULE|Nobody|9000000000|{TOMORROW_STR}|03:00 PM|{DOCTOR_NAME}|N/A ]",
        "I couldn't find an appointment under that name and number.",
        captured,
    )
    reply = await _chat("Reschedule mine to 3 pm", dispatch, "s-resched-unknown")

    appt = await _the_appointment()
    assert _ist_hour(appt) == 14
    assert "no active appointment on that phone number" in captured["regen_system"]
    assert not chat_mod._promises_followup(reply)


@pytest.mark.asyncio
async def test_reschedule_before_the_patient_is_identified_asks_who_they_are(seeded_db):
    """A placeholder name/phone used to produce "no appointment found", which
    reads as "your appointment doesn't exist" when the real problem is that the
    agent never asked who it was talking to."""
    captured = {}
    dispatch = _dispatcher(
        f"Okay. [ ACTION: RESCHEDULE|N/A|N/A|{TOMORROW_STR}|03:00 PM|{DOCTOR_NAME}|N/A ]",
        "Could you tell me your name and phone number?",
        captured,
    )
    await _chat("Move my appointment to 3 pm", dispatch, "s-resched-anon")

    appt = await _the_appointment()
    assert _ist_hour(appt) == 14
    update = captured["regen_system"]
    assert "do NOT tell them no appointment exists" in update
    assert "name" in update and "phone" in update


@pytest.mark.asyncio
async def test_rescheduling_to_the_same_time_is_not_reported_as_a_change(seeded_db):
    captured = {}
    dispatch = _dispatcher(
        f"Okay. [ ACTION: RESCHEDULE|{PATIENT}|{PHONE}|{TOMORROW_STR}|02:00 PM|{DOCTOR_NAME}|N/A ]",
        "Your appointment is already at 2 PM, and it's still confirmed.",
        captured,
    )
    reply = await _chat("Move it to 2 pm tomorrow", dispatch, "s-resched-noop")

    appt = await _the_appointment()
    assert _ist_hour(appt) == 14
    assert "ALREADY at exactly that time" in captured["regen_system"]
    assert not chat_mod._promises_followup(reply)


# ── 5. Phone formatting must not hide an appointment ──────────────────────────

@pytest.mark.asyncio
async def test_phone_written_with_a_country_code_still_finds_the_appointment(seeded_db):
    dispatch = _dispatcher(
        f"Okay. [ ACTION: RESCHEDULE|{PATIENT}|+91 90909-09090|{TOMORROW_STR}|03:00 PM|{DOCTOR_NAME}|N/A ]",
        "Your appointment has been rescheduled to 3 PM.",
    )
    await _chat("Move it to 3 pm", dispatch, "s-resched-phone")

    appt = await _the_appointment()
    assert _ist_hour(appt) == 15, \
        "a +91/punctuated phone is the same number and must match"


def test_normalize_phone():
    from backend.services.his import normalize_phone
    assert normalize_phone("+91 90909-09090") == "9090909090"
    assert normalize_phone("09090909090") == "9090909090"
    assert normalize_phone("9090909090") == "9090909090"
    # A genuinely different number must NOT be normalized into a match.
    assert normalize_phone("90909090") != normalize_phone("9090909090")


# ── 6. Availability questions get real answers ────────────────────────────────

@pytest.mark.asyncio
async def test_availability_question_gets_real_slots_in_the_prompt(seeded_db):
    """The transcript's other half: asked what was free for Dr Rajesh tomorrow,
    the agent had no data at all, stalled, and then admitted it did not know."""
    captured = {}
    dispatch = _dispatcher("Dr Rajesh is free at 9:00 AM and 9:30 AM tomorrow.",
                           captured=captured)
    await _chat("What times are available for Dr Rajesh tomorrow?",
                dispatch, "s-avail")

    prompt = captured["first_system"]
    assert "REAL DOCTOR AVAILABILITY" in prompt
    assert DOCTOR_NAME in prompt
    assert TOMORROW.strftime("%A %d/%m/%Y") in prompt
    # Real computed slots from the 09:00–17:00 window.
    assert "09:00 AM" in prompt or "9:00 AM" in prompt
    # …and the numbers come from the same engine that gates the write, so the
    # 2 PM slot the patient already holds is excluded from what may be offered.
    from backend.services.availability import availability_digest
    slots = (await availability_digest(TENANT_ID, [DOCTOR_ID], [TOMORROW]))[(DOCTOR_ID, TOMORROW)]
    assert slots, "the doctor really does have open slots tomorrow"
    assert _ist(TOMORROW, 14) not in slots, "the already-booked 2 PM must not be offered"


@pytest.mark.asyncio
async def test_a_full_consulting_day_is_listed_whole(seeded_db):
    """A truncated slot list reads to the model as the WHOLE day, and it then
    tells the patient a genuinely free time is unavailable. Measured live
    2026-08-11: Dr Rajesh consults 09:00–17:00, only the first 8 slots were
    listed, and the agent refused a 3 PM reschedule that was open."""
    captured = {}
    dispatch = _dispatcher("Let me see.", captured=captured)
    await _chat("What's free with Dr Rajesh tomorrow?", dispatch, "s-avail-full")

    prompt = captured["first_system"]
    line = next(l for l in prompt.splitlines()
                if l.strip().startswith(f"- {DOCTOR_NAME},") and "PM" in l)
    times = [t.strip() for t in line.split(":", 1)[1].split(",")]
    assert "3:00 PM" in times, f"3 PM is open and must be listed: {line}"
    assert "4:30 PM" in times, f"the day must be listed to its end: {line}"
    assert "cut short" not in line
    # …and the one slot the patient already holds is the only PM gap.
    assert "2:00 PM" not in times, f"the taken 2 PM must not be offered: {line}"


@pytest.mark.asyncio
async def test_an_availability_question_never_writes_anything(seeded_db):
    dispatch = _dispatcher("Dr Rajesh is free at 9:00 AM tomorrow.")
    before = (await _the_appointment()).slot_time
    await _chat("What times are free tomorrow?", dispatch, "s-avail-nowrite")
    assert (await _the_appointment()).slot_time == before


@pytest.mark.asyncio
async def test_availability_block_marks_an_on_leave_doctor(seeded_db):
    async with AsyncSessionLocal() as s:
        doc = (await s.execute(select(Doctor).where(Doctor.id == OTHER_DOCTOR_ID))).scalar_one()
        doc.is_available = False
        doc.leave_reason = "on maternity leave"
        await s.commit()
    from backend.services.his import invalidate_doctor_cache
    invalidate_doctor_cache(TENANT_ID)

    captured = {}
    dispatch = _dispatcher("Dr Nazima is on leave.", captured=captured)
    await _chat(f"Is {OTHER_DOCTOR_NAME} free tomorrow?", dispatch, "s-avail-leave")

    prompt = captured["first_system"]
    assert "ON LEAVE" in prompt
    assert "maternity" in prompt
