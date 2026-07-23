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
