# -*- coding: utf-8 -*-
"""
Verifies the chat/embed booking path now obeys the honesty contract (audit
FIX 4) — the same guarantee the voice pipeline already had. This reproduces the
audit's exact repro: the Test Agent chat is asked to book with a doctor that
does NOT exist at the clinic, and it must NOT fabricate a confirmation.

Only the external LLM output is stubbed (via agent_test._dispatch_llm) — the
entire real handler path runs: guardrail prompt -> [ACTION:] parse -> capability
gate -> execute_booking_action -> find_doctor_for_booking -> create_appointment
-> [BOOKING_RESULT ...] injection -> honest regeneration. Everything DB-touching
runs against a real (SQLite) database.

Run:
    python -m pytest backend/tests/test_chat_booking_honesty.py -v
"""

# ── TEST SAFETY: force a local SQLite DB *before* importing backend.db ──────────
# load_dotenv(override=False) inside backend/db.py will NOT override these, so a
# real DATABASE_URL in .env can never be touched by this test.
import os
os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_chat_booking_honesty.db"

import pytest
import pytest_asyncio
from unittest.mock import patch
from sqlalchemy import select

import backend.db as db_mod
from backend.db import AsyncSessionLocal, engine, Base
from backend.models.tenant import Tenant
from backend.models.doctor import Doctor
from backend.models.appointment import Appointment
from backend.models.agent_config import AgentConfig
from backend.agent.booking_rules import BOOKING_RESULT_TRUE, BOOKING_RESULT_FALSE
from backend.routers import agent_test as chat_mod

TENANT_ID = "11111111-1111-1111-1111-111111111111"
AGENT_ID = "22222222-2222-2222-2222-222222222222"
REAL_DOCTOR_ID = "33333333-3333-3333-3333-333333333333"
REAL_DOCTOR_NAME = "Dr Anjali Sharma"


@pytest_asyncio.fixture
async def seeded_db():
    # Hard stop if we are somehow NOT on SQLite — never run against a real DB.
    assert db_mod.IS_SQLITE, "TEST SAFETY: refusing to run against a non-SQLite database"
    db_mod._import_all_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as s:
        s.add(Tenant(id=TENANT_ID, clinic_name="ZZZ Audit Clinic", admin_email="zzz_audit@example.com"))
        s.add(Doctor(id=REAL_DOCTOR_ID, tenant_id=TENANT_ID, name=REAL_DOCTOR_NAME, specialization="Cardiologist"))
        s.add(AgentConfig(
            id=AGENT_ID, tenant_id=TENANT_ID, agent_name="Aster Bot",
            llm_provider="gemini", llm_model="gemini-2.5-flash",
            system_prompt="You are a receptionist for ZZZ Audit Clinic.",
            can_book_appointments=True, can_cancel_appointments=True,
        ))
        await s.commit()
    chat_mod._conversation_history.clear()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _appointments():
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(Appointment).where(Appointment.tenant_id == TENANT_ID))).scalars().all()


async def _set_can_book(value: bool):
    async with AsyncSessionLocal() as s:
        ag = (await s.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
        ag.can_book_appointments = value
        await s.commit()


# ── THE AUDIT REPRO: book with a nonexistent doctor ─────────────────────────────

@pytest.mark.asyncio
async def test_chat_refuses_nonexistent_doctor(seeded_db):
    captured = {}

    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        # Phase 2 = the honest regeneration (its system prompt carries the result).
        if "SYSTEM UPDATE (AUTHORITATIVE" in system_prompt:
            captured["regen_system"] = system_prompt
            return ("I'm sorry, Dr. Strange isn't available at our clinic. "
                    f"We do have {REAL_DOCTOR_NAME} (Cardiologist). Who would you like to see?")
        # Phase 1 = model follows the ACTION RULE and emits a tag for a fake doctor.
        return ("One moment while I check that for you.\n"
                "[ACTION: BOOK|John Doe|+919812345678|23/07/2026|3 PM|Dr Strange|N/A]")

    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            reply = await chat_mod.generate_llm_response(
                agent,
                "Book me with Dr Strange tomorrow at 3 PM. I'm John Doe, 9812345678.",
                db, session_id="s-repro", user_language="en-IN",
            )

    # 1. The authoritative injected outcome is a FAILURE — never success=true.
    #    (Isolate the injected SYSTEM UPDATE line; the rules block itself quotes
    #    the success token, so we must not match against the whole prompt.)
    assert "regen_system" in captured, "honest regeneration pass never ran"
    update = captured["regen_system"].split("SYSTEM UPDATE (AUTHORITATIVE", 1)[1]
    update = update.split("--- APPOINTMENT BOOKING RULES", 1)[0]
    assert BOOKING_RESULT_FALSE in update
    assert BOOKING_RESULT_TRUE not in update
    # 2. NOTHING was written — no silent booking against an arbitrary/zero-UUID doctor.
    assert await _appointments() == []
    # 3. The user-facing reply does not fabricate a confirmation.
    low = reply.lower()
    assert "not available" in low or "isn't available" in low
    assert "booked" not in low and "confirmed" not in low


# ── Positive path: a real doctor really books ──────────────────────────────────

@pytest.mark.asyncio
async def test_chat_books_real_doctor_after_real_write(seeded_db):
    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        if "SYSTEM UPDATE (AUTHORITATIVE" in system_prompt:
            assert BOOKING_RESULT_TRUE in system_prompt, system_prompt[-400:]
            return f"You're all set — your appointment with {REAL_DOCTOR_NAME} is confirmed."
        return ("One moment while I book that.\n"
                f"[ACTION: BOOK|John Doe|+919812345678|23/07/2026|3 PM|{REAL_DOCTOR_NAME}|N/A]")

    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            reply = await chat_mod.generate_llm_response(
                agent, "Book Dr Anjali Sharma tomorrow 3 PM, I'm John Doe 9812345678.",
                db, session_id="s-ok", user_language="en-IN",
            )

    appts = await _appointments()
    assert len(appts) == 1
    assert appts[0].doctor_id == REAL_DOCTOR_ID
    assert appts[0].patient_name == "John Doe"
    assert appts[0].status == "confirmed"
    assert "confirmed" in reply.lower()


# ── Capability gate: booking disabled must not book ─────────────────────────────

@pytest.mark.asyncio
async def test_chat_respects_disabled_booking_flag(seeded_db):
    await _set_can_book(False)

    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        if "SYSTEM UPDATE (AUTHORITATIVE" in system_prompt:
            assert BOOKING_RESULT_FALSE in system_prompt
            assert "turned off" in system_prompt
            return "I'm sorry, I can't book appointments here. Let me connect you with our staff."
        return ("Sure.\n[ACTION: BOOK|John Doe|+919812345678|23/07/2026|3 PM|" + REAL_DOCTOR_NAME + "|N/A]")

    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            reply = await chat_mod.generate_llm_response(
                agent, "Book Dr Anjali Sharma 3 PM, John 9812345678.",
                db, session_id="s-gate", user_language="en-IN",
            )

    assert await _appointments() == []
    assert "can't" in reply.lower() or "cannot" in reply.lower() or "unable" in reply.lower()


# ── Time gate: an [ACTION:] tag with no real time must not book midnight ───────

@pytest.mark.asyncio
async def test_chat_refuses_booking_with_blank_time(seeded_db):
    """Reproduces a real 2026-08-10 production incident: the model's [ACTION:]
    tag carried a correct Date but a blank Time field, and
    parse_slot_datetime's fallback-on-unparseable behavior silently booked
    midnight instead of refusing. This is exactly the fabricated-slot failure
    mode audit FIX 4 already bans for the voice path — the chat path must
    refuse instead of booking whatever the fallback produces."""
    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        if "SYSTEM UPDATE (AUTHORITATIVE" in system_prompt:
            captured["regen_system"] = system_prompt
            return "I'm sorry, I didn't catch the time. Could you say it again?"
        # Correct date, BLANK time — the real production repro.
        return ("One moment while I book that.\n"
                f"[ACTION: BOOK|John Doe|+919812345678|23/07/2026||{REAL_DOCTOR_NAME}|N/A]")

    captured = {}
    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            reply = await chat_mod.generate_llm_response(
                agent, f"Book me with {REAL_DOCTOR_NAME} tomorrow. I'm John Doe, 9812345678.",
                db, session_id="s-blanktime", user_language="en-IN",
            )

    assert "regen_system" in captured, "honest regeneration pass never ran"
    update = captured["regen_system"].split("SYSTEM UPDATE (AUTHORITATIVE", 1)[1]
    update = update.split("--- APPOINTMENT BOOKING RULES", 1)[0]
    assert BOOKING_RESULT_FALSE in update
    assert BOOKING_RESULT_TRUE not in update
    assert "no valid appointment time" in update.lower()
    # NOTHING was written — not even a midnight fallback appointment.
    assert await _appointments() == []
    low = reply.lower()
    assert "booked" not in low and "confirmed" not in low


@pytest.mark.asyncio
async def test_chat_refuses_booking_with_na_time(seeded_db):
    """Same gate, but the model wrote the literal placeholder 'N/A' instead of
    leaving the field blank — both must be refused, not just an empty string."""
    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        if "SYSTEM UPDATE (AUTHORITATIVE" in system_prompt:
            captured["regen_system"] = system_prompt
            return "I'm sorry, I didn't catch the time. Could you say it again?"
        return ("One moment while I book that.\n"
                f"[ACTION: BOOK|John Doe|+919812345678|23/07/2026|N/A|{REAL_DOCTOR_NAME}|N/A]")

    captured = {}
    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            await chat_mod.generate_llm_response(
                agent, f"Book me with {REAL_DOCTOR_NAME} tomorrow. I'm John Doe, 9812345678.",
                db, session_id="s-natime", user_language="en-IN",
            )

    assert "regen_system" in captured
    update = captured["regen_system"].split("SYSTEM UPDATE (AUTHORITATIVE", 1)[1]
    assert BOOKING_RESULT_FALSE in update.split("--- APPOINTMENT BOOKING RULES", 1)[0]
    assert await _appointments() == []


@pytest.mark.asyncio
async def test_chat_still_books_with_a_real_time(seeded_db):
    """Regression guard: the time gate must not block a REAL time — only
    empty/unparseable ones."""
    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        if "SYSTEM UPDATE (AUTHORITATIVE" in system_prompt:
            assert BOOKING_RESULT_TRUE in system_prompt, system_prompt[-400:]
            return f"You're all set — your appointment with {REAL_DOCTOR_NAME} is confirmed."
        return ("One moment while I book that.\n"
                f"[ACTION: BOOK|John Doe|+919812345678|23/07/2026|3 PM|{REAL_DOCTOR_NAME}|N/A]")

    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            reply = await chat_mod.generate_llm_response(
                agent, f"Book {REAL_DOCTOR_NAME} tomorrow 3 PM, I'm John Doe 9812345678.",
                db, session_id="s-realtime", user_language="en-IN",
            )

    appts = await _appointments()
    assert len(appts) == 1
    assert appts[0].status == "confirmed"
    assert "confirmed" in reply.lower()


# ── Never end a turn on a promise of a message that can never arrive ───────────
#
# The 2026-08-10 production hang: this path is request/response, so any reply
# that tells the patient to wait strands them permanently. Three ways it
# happened live, all covered below.

def test_promises_followup_detector():
    from backend.routers.agent_test import _promises_followup
    # The two exact strings from the production transcript.
    assert _promises_followup("please hold on for a moment while I complete your booking") is True
    assert _promises_followup("I've sent the request, please wait for a moment to confirm") is True
    assert _promises_followup("Let me check the availability for you") is True
    # Resolved replies must NOT trip it.
    assert _promises_followup("Your appointment with Dr Sharma is confirmed for 3 PM.") is False
    assert _promises_followup("Which doctor would you like to see?") is False


def test_scrub_reply_strips_malformed_action_tags():
    from backend.routers.agent_test import _scrub_reply
    # Production 2026-08-10: this malformed tag leaked to the patient verbatim
    # because the strict _ACTION_RE could not parse it.
    assert "[ACTION" not in _scrub_reply("What time would you like? [ACTION: None]")
    assert "[ACTION" not in _scrub_reply("Sure. [ACTION: BOOK|a|b|c|d|e|f]")
    assert _scrub_reply("What time would you like? [ACTION: None]") == "What time would you like?"


@pytest.mark.asyncio
async def test_no_tag_but_promised_followup_is_repaired_into_a_real_booking(seeded_db):
    """The exact production repro: the model says 'hold on while I complete
    your booking' and emits NO tag. Before the fix the turn ended there and the
    patient waited forever. Now it is repaired into the real booking."""
    calls = {"n": 0}

    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        calls["n"] += 1
        if "SYSTEM UPDATE (AUTHORITATIVE" in system_prompt and "did NOT" in system_prompt:
            # The repair pass — model now emits the tag it forgot.
            return ("Booking that now.\n"
                    f"[ACTION: BOOK|Ramesh Kumar|9845012345|23/07/2026|2 PM|{REAL_DOCTOR_NAME}|N/A]")
        if "SYSTEM UPDATE (AUTHORITATIVE" in system_prompt:
            return f"Your appointment with {REAL_DOCTOR_NAME} is confirmed."
        # Phase 1: the broken promise, verbatim from production.
        return "I've got your details, please hold on for a moment while I complete your booking"

    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            reply = await chat_mod.generate_llm_response(
                agent, "Yes, Ramesh Kumar, 9845012345.", db,
                session_id="s-repair", user_language="en-IN",
            )

    # The booking the patient was promised actually happened...
    appts = await _appointments()
    assert len(appts) == 1, "the repair pass should have completed the real booking"
    # ...and the patient was TOLD, rather than left waiting.
    assert "confirmed" in reply.lower()
    assert not chat_mod._promises_followup(reply), f"turn still ends on a promise: {reply!r}"


@pytest.mark.asyncio
async def test_successful_write_never_replies_with_please_wait(seeded_db):
    """The nastiest production variant: the row WAS written, but the model's
    regenerated reply still said 'please wait to confirm', so the patient never
    learned their appointment existed. The reply must be replaced with a real
    confirmation."""
    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        if "SYSTEM UPDATE (AUTHORITATIVE" in system_prompt:
            # Model ignores the injected success and hedges anyway.
            return "I've sent the request, please wait for a moment to confirm"
        return ("Okay.\n"
                f"[ACTION: BOOK|John Doe|+919812345678|23/07/2026|3 PM|{REAL_DOCTOR_NAME}|N/A]")

    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            reply = await chat_mod.generate_llm_response(
                agent, "Book it please.", db, session_id="s-hedge", user_language="en-IN",
            )

    assert len(await _appointments()) == 1
    assert not chat_mod._promises_followup(reply), f"patient told to wait after a REAL booking: {reply!r}"
    assert "confirmed" in reply.lower()


@pytest.mark.asyncio
async def test_claiming_booked_with_no_tag_is_never_shown_to_the_patient(seeded_db):
    """The worst failure mode: the model says "your appointment is booked" and
    emits NO tag, so nothing was written — a fabricated confirmation.

    Measured 2026-08-10 while benchmarking Groq models: llama-3.1-8b-instant
    did exactly this in 2 of 3 booking runs. The previous guard only caught
    replies that PROMISED a follow-up, so this sailed straight through."""
    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        if "SYSTEM UPDATE (AUTHORITATIVE" in system_prompt and "NOTHING happened" in system_prompt:
            # Repair pass: model now emits the tag it should have emitted.
            return f"[ACTION: BOOK|John Doe|+919812345678|23/07/2026|3 PM|{REAL_DOCTOR_NAME}|N/A]"
        if "SYSTEM UPDATE (AUTHORITATIVE" in system_prompt:
            return f"Your appointment with {REAL_DOCTOR_NAME} is confirmed."
        # Phase 1: fabricated confirmation, no tag.
        return f"Your appointment with {REAL_DOCTOR_NAME} is booked for 3 PM on 23/07/2026."

    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            reply = await chat_mod.generate_llm_response(
                agent, "John Doe, 9812345678.", db, session_id="s-fabricated", user_language="en-IN")

    # The claim is only allowed to stand because the repair actually booked it.
    appts = await _appointments()
    assert len(appts) == 1, "a 'booked' claim must be backed by a real row"
    assert "confirmed" in reply.lower()


@pytest.mark.asyncio
async def test_fabricated_claim_that_cannot_be_repaired_is_replaced(seeded_db):
    """If the model keeps claiming success without a tag, the patient must NOT
    be told it is booked."""
    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        return f"Your appointment with {REAL_DOCTOR_NAME} is booked."

    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            reply = await chat_mod.generate_llm_response(
                agent, "book it", db, session_id="s-fab-norepair", user_language="en-IN")

    assert await _appointments() == [], "nothing was written"
    assert not chat_mod._claims_any_completion(reply), \
        f"must not tell the patient it is booked when it is not: {reply!r}"


@pytest.mark.asyncio
async def test_no_tag_and_repair_fails_still_resolves_the_turn(seeded_db):
    """If even the repair pass keeps promising, fall back to a question. The
    turn must never end on 'please wait'."""
    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        return "please hold on for a moment while I complete your booking"

    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            reply = await chat_mod.generate_llm_response(
                agent, "Book me in.", db, session_id="s-norepair", user_language="en-IN",
            )

    assert await _appointments() == []
    assert not chat_mod._promises_followup(reply), f"turn ended on a promise: {reply!r}"
    assert "?" in reply, "should ask for the missing details instead of promising"


@pytest.mark.asyncio
async def test_double_booking_reports_conflict_with_real_alternatives(seeded_db):
    """A conflict used to fall through to the generic 'system error… try
    again?' message — wrong, and an infinite retry loop, since retrying the
    same slot can never succeed."""
    from datetime import time as time_cls
    from backend.models.doctor_availability import DoctorAvailability
    from backend.services.timeutil import ist_now

    # Give the doctor real hours today so alternatives can be computed.
    today = ist_now().date()
    async with AsyncSessionLocal() as s:
        s.add(DoctorAvailability(tenant_id=TENANT_ID, doctor_id=REAL_DOCTOR_ID,
                                 day_of_week=today.weekday(),
                                 start_time=time_cls(0, 0), end_time=time_cls(23, 30)))
        await s.commit()

    date_str = today.strftime("%d/%m/%Y")
    captured = {}

    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        if "SYSTEM UPDATE (AUTHORITATIVE" in system_prompt:
            captured["regen_system"] = system_prompt
            return "Sorry, that time is taken."
        return ("Okay.\n"
                f"[ACTION: BOOK|John Doe|+919812345678|{date_str}|11:30 PM|{REAL_DOCTOR_NAME}|N/A]")

    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            # First booking claims the slot.
            await chat_mod.generate_llm_response(
                agent, "Book 11:30 PM.", db, session_id="s-first", user_language="en-IN")
            assert len(await _appointments()) == 1
            # Second booking hits the same doctor+slot.
            reply = await chat_mod.generate_llm_response(
                agent, "Book 11:30 PM.", db, session_id="s-second", user_language="en-IN")

    # No double-booking, and the conflict was reported AS a conflict.
    assert len(await _appointments()) == 1
    update = captured["regen_system"].split("SYSTEM UPDATE (AUTHORITATIVE", 1)[1]
    update = update.split("--- APPOINTMENT BOOKING RULES", 1)[0]
    assert "ALREADY BOOKED" in update
    assert "do NOT offer to retry the SAME time" in update.lower() or "retry the same time" in update.lower()
    assert not chat_mod._promises_followup(reply)


def test_asserts_completion_rejects_questions_and_hedges():
    from backend.routers.agent_test import _asserts_completion
    assert _asserts_completion("Your appointment is confirmed.", "BOOK") is True
    assert _asserts_completion("You're all set for 3 PM.", "BOOK") is True
    # The exact live 2026-08-10 reply that followed a SUCCESSFUL write — it
    # mentions "confirm" but asks rather than tells, so it must be rejected.
    assert _asserts_completion(
        "To confirm, you are Ramesh Kumar, and you would like to book with Dr. Rajesh "
        "for tomorrow at two PM, is that right?", "BOOK") is False
    assert _asserts_completion("", "BOOK") is False
    assert _asserts_completion("Okay, noted.", "BOOK") is False


@pytest.mark.asyncio
async def test_successful_write_reported_as_a_question_is_replaced(seeded_db):
    """A successful booking answered with another question leaves the patient
    believing nothing happened — same stranding as 'please wait', different
    wording. Observed live 2026-08-10."""
    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        if "SYSTEM UPDATE (AUTHORITATIVE" in system_prompt:
            return "To confirm, you are John Doe and you want Dr Sharma at three PM, is that right?"
        return ("Okay.\n"
                f"[ACTION: BOOK|John Doe|+919812345678|23/07/2026|3 PM|{REAL_DOCTOR_NAME}|N/A]")

    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            reply = await chat_mod.generate_llm_response(
                agent, "Book it.", db, session_id="s-question", user_language="en-IN")

    assert len(await _appointments()) == 1, "the write really happened"
    assert chat_mod._asserts_completion(reply, "BOOK"), \
        f"patient must be TOLD the booking exists, got: {reply!r}"


@pytest.mark.asyncio
async def test_book_with_placeholder_name_and_phone_is_refused(seeded_db):
    """Live 2026-08-10: the model emitted a BOOK tag with N/A name and phone
    before asking for them, writing a real appointment for patient "N/A" —
    a row the patient could never cancel, since that lookup matches on
    name AND phone."""
    captured = {}

    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        if "SYSTEM UPDATE (AUTHORITATIVE" in system_prompt:
            captured["regen_system"] = system_prompt
            return "Could you tell me your name and phone number?"
        return ("Booking that now.\n"
                f"[ACTION: BOOK|N/A|N/A|23/07/2026|2 PM|{REAL_DOCTOR_NAME}|N/A]")

    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
         patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            agent = (await db.execute(select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()
            reply = await chat_mod.generate_llm_response(
                agent, "Tomorrow at 2 PM please.", db, session_id="s-noname", user_language="en-IN")

    assert await _appointments() == [], "must not write an unidentifiable appointment"
    update = captured["regen_system"].split("SYSTEM UPDATE (AUTHORITATIVE", 1)[1]
    assert BOOKING_RESULT_FALSE in update.split("--- APPOINTMENT BOOKING RULES", 1)[0]
    assert "name" in update.lower() and "phone" in update.lower()
    assert not chat_mod._promises_followup(reply)


# ── Unit-level guards on the shared service ─────────────────────────────────────

@pytest.mark.asyncio
async def test_execute_booking_action_refuses_unknown_doctor(seeded_db):
    from backend.services.his import execute_booking_action
    res = await execute_booking_action(
        action="BOOK", tenant_id=TENANT_ID, name="John", phone="+919812345678",
        date_str="23/07/2026", time_str="3 PM", doctor_name="Dr Strange",
    )
    assert res["success"] is False
    assert res["reason"] == "doctor_not_found"
    assert REAL_DOCTOR_NAME in res["available_doctors"]
    assert await _appointments() == []


def test_is_time_str_parseable():
    from backend.services.his import is_time_str_parseable
    assert is_time_str_parseable("3 PM") is True
    assert is_time_str_parseable("11:30 AM") is True
    assert is_time_str_parseable("18:00") is True
    assert is_time_str_parseable(None) is False
    assert is_time_str_parseable("") is False
    assert is_time_str_parseable("   ") is False
    assert is_time_str_parseable("N/A") is False
    assert is_time_str_parseable("whenever is convenient") is False


@pytest.mark.asyncio
async def test_sync_appointment_to_db_book_no_arbitrary_fallback(seeded_db):
    # The removed bug: BOOK used to fall back to the first/zero-UUID doctor.
    from backend.services.his import sync_appointment_to_db
    res = await sync_appointment_to_db(
        action="BOOK", name="John", phone="+919812345678", date_str="23/07/2026",
        time_str="3 PM", doctor_name="Dr Strange", tenant_id=TENANT_ID,
    )
    assert res is None
    assert await _appointments() == []
