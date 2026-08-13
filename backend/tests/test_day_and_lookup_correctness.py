# -*- coding: utf-8 -*-
"""
Two live-call defects, both about the system trusting the wrong thing.

Both were measured on real calls to Indiana Hospital Mangalore on 2026-08-12,
after voice bookings started reaching the database at all:

1. **The model was allowed to do date arithmetic.** The caller said
   "कल दोपहर 3 बजे" (tomorrow, 3 PM) with "Today is Wednesday, 12/08/2026" in the
   system prompt, and the model wrote ``15/08/2026`` into its tag. A real
   appointment was created three days out. Nothing downstream could catch it:
   15/08 is a valid future date and the doctor genuinely was free at 3 PM on it.

2. **The appointment lookup could not find the row it had just written.** The
   booking stored ``patient_name='आइनान'``; the next call's transcript said
   ``ऐनान`` and ``आइनन``, and the lookup was a SQL ``ilike '%name%'``. It can
   never match a spelling variant, let alone the same name written ``Ainan``. So
   CANCEL returned "not found" every time, and the agent spent 280 seconds asking
   the caller to spell their own name.

Run: python -m pytest backend/tests/test_day_and_lookup_correctness.py -v
"""

# ── TEST SAFETY: force a local SQLite DB *before* importing backend.db ─────────
import os

os.environ["ENVIRONMENT"] = "development"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_day_and_lookup.db"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-day-lookup-tests")

import datetime as dt

import pytest
import pytest_asyncio
from sqlalchemy import select

import backend.db as db_mod
from backend.db import AsyncSessionLocal, Base, engine
from backend.models.appointment import SOURCE_VOICE, Appointment
from backend.models.doctor import Doctor
from backend.models.doctor_availability import DoctorAvailability
from backend.models.tenant import Tenant
from backend.services import dayref
from backend.services.his import (
    caller_appointments,
    execute_booking_action,
    find_active_appointment,
    is_date_str_parseable,
    names_refer_to_same_person,
    parse_slot_datetime,
)
from backend.services.timeutil import ist_now, ist_wall_clock_to_utc, to_ist

TENANT_ID = "dddddddd-0000-0000-0000-000000000001"
DOCTOR_ID = "dddddddd-0000-0000-0000-000000000002"
OTHER_DOCTOR_ID = "dddddddd-0000-0000-0000-000000000003"
PHONE = "9148768120"

TODAY = dt.date(2026, 8, 12)          # a Wednesday, the day of the real calls
TOMORROW_REAL = ist_now().date() + dt.timedelta(days=1)


# ── 1. Day resolution, in every language the product speaks ───────────────────

@pytest.mark.parametrize("word,offset", [
    ("tomorrow", 1), ("kal", 1), ("कल", 1),          # Hindi — the live failure
    ("আগামীকাল", 1),                                  # Bengali
    ("કાલે", 1),                                      # Gujarati
    ("ਕੱਲ੍ਹ", 1),                                     # Punjabi
    ("କାଲି", 1),                                      # Odia
    ("நாளை", 1),                                      # Tamil
    ("రేపు", 1),                                      # Telugu
    ("ನಾಳೆ", 1),                                      # Kannada
    ("നാളെ", 1),                                      # Malayalam
    ("today", 0), ("आज", 0), ("ഇന്ന്", 0),
    ("day after tomorrow", 2), ("परसों", 2), ("മറ്റന്നാൾ", 2),
])
def test_every_language_s_word_for_tomorrow_resolves_from_todays_date(word, offset):
    """"When I say anything like tomorrow in any language it should look at
    today's date." The map used to cover English and Devanagari only, so a
    Malayalam caller's "നാളെ" silently resolved to today."""
    got = dayref.parse_day_string(word, TODAY)
    assert got == (TODAY + dt.timedelta(days=offset) if offset is not None else None)


def test_a_weekday_always_means_the_next_one_never_a_past_one():
    """"It should consider only future dates, not past dates." 12/08/2026 is a
    Wednesday, so "Wednesday" means the NEXT one, not the one nearly over."""
    assert dayref.parse_day_string("wednesday", TODAY) == dt.date(2026, 8, 19)
    assert dayref.parse_day_string("friday", TODAY) == dt.date(2026, 8, 14)
    for name in dayref.WEEKDAY_NAMES:
        assert dayref.parse_day_string(name, TODAY) > TODAY


def test_a_day_that_names_nothing_is_refused_not_defaulted_to_today():
    """parse_slot_datetime's documented fallback is today — fine for "the day was
    not specified", catastrophic for "the model wrote something unparseable"."""
    assert is_date_str_parseable("") is True          # not specified: fine
    assert is_date_str_parseable("कल") is True
    assert is_date_str_parseable("13/08/2026") is True
    assert is_date_str_parseable("next week") is False
    assert is_date_str_parseable("sometime soon") is False
    assert is_date_str_parseable("asap") is False


def test_a_day_word_inside_a_longer_word_is_not_a_day():
    """Hindi "कलम" is a pen, not tomorrow. This is the same false-positive class
    that once booked 2 AM out of "दोपहर के दो बजे" — and here it would overrule
    the model with a day the caller never said."""
    assert dayref.parse_day_string("कलम", TODAY) is None
    assert dayref.dates_in_text("मुझे एक कलम चाहिए", TODAY) == []
    # …while the real word, in a real sentence, still resolves.
    assert dayref.dates_in_text("कल दोपहर 3 बजे", TODAY) == [dt.date(2026, 8, 13)]


def test_the_long_way_of_saying_day_after_tomorrow_is_not_tomorrow():
    assert dayref.parse_day_string("kal ke baad", TODAY) == dt.date(2026, 8, 14)
    assert dayref.parse_day_string("कल के बाद", TODAY) == dt.date(2026, 8, 14)
    assert dayref.parse_day_string("day after tomorrow", TODAY) == dt.date(2026, 8, 14)


def test_the_callers_own_word_overrules_the_models_arithmetic():
    """The exact live failure: caller said tomorrow, model wrote three days out."""
    said = dayref.note_dates_said([], "कल दोपहर 3 बजे", TODAY)
    assert said == [dt.date(2026, 8, 13)]

    date_str, correction = dayref.reconcile_requested_date("15/08/2026", said, TODAY)
    assert date_str == "13/08/2026", "the caller asked for tomorrow and must get tomorrow"
    assert correction and "15/08/2026" in correction


def test_the_model_is_trusted_when_it_agrees_with_the_caller():
    said = dayref.note_dates_said([], "tomorrow at 3", TODAY)
    assert dayref.reconcile_requested_date("13/08/2026", said, TODAY) == ("13/08/2026", None)


def test_the_model_is_trusted_when_the_caller_named_no_day():
    """Overruling a day the caller never mentioned would be its own invention —
    they may have given it earlier, or in writing."""
    assert dayref.reconcile_requested_date("15/08/2026", [], TODAY) == ("15/08/2026", None)


def test_a_correction_is_recorded_as_the_most_recent_day():
    """On the live cancel call the caller said "कल" and then corrected themselves
    to the 15th, and the correction is what a later invented date is replaced
    with.

    Note what this deliberately does NOT do: it does not overrule a model date
    that IS one of the days the caller discussed. The cross-check exists to catch
    arithmetic the caller never asked for; inside the set of days actually talked
    about, the model has the whole conversation and we do not, so second-guessing
    it there is more likely to introduce an error than remove one.
    """
    said = dayref.note_dates_said([], "कल दोपहर 3 बजे", TODAY)
    said = dayref.note_dates_said(said, "नहीं, 15/08/2026 को था", TODAY)
    assert said == [dt.date(2026, 8, 13), dt.date(2026, 8, 15)]

    # A day nobody mentioned -> replaced by the most recent one they did.
    assert dayref.reconcile_requested_date("20/08/2026", said, TODAY)[0] == "15/08/2026"
    # A day they did mention -> left alone, either of them.
    assert dayref.reconcile_requested_date("13/08/2026", said, TODAY)[1] is None
    assert dayref.reconcile_requested_date("15/08/2026", said, TODAY)[1] is None


def test_a_booking_is_never_moved_into_the_past():
    past = TODAY - dt.timedelta(days=3)
    assert dayref.reconcile_requested_date("15/08/2026", [past], TODAY) == ("15/08/2026", None)


def test_parse_slot_datetime_resolves_native_day_words():
    """The end-to-end consequence: the stored UTC instant is the right day."""
    for word in ("कल", "നാളെ", "ਕੱਲ੍ਹ", "tomorrow"):
        got = to_ist(parse_slot_datetime(word, "3 PM"))
        assert got.date() == TOMORROW_REAL, f"{word!r} resolved to {got.date()}"
        assert got.hour == 15


# ── 2. Finding the appointment again ──────────────────────────────────────────

@pytest.mark.parametrize("stored,spoken", [
    ("आइनान", "ऐनान"),        # the live failure: same name, two transcriptions
    ("आइनान", "आइनन"),
    ("आइनान", "Ainan"),        # stored in Devanagari, spoken/typed in Latin
    ("Ainan", "आइनान"),        # and the reverse
    ("Ainan", "ainan"),
    ("Ramesh Kumar", "Ramesh"),
    ("Ramesh", "Ramesh Kumar"),
    ("സൽമാൻ", "Salman"),
])
def test_a_name_is_recognised_however_it_was_transcribed(stored, spoken):
    assert names_refer_to_same_person(stored, spoken) is True


@pytest.mark.parametrize("stored,spoken", [
    ("Ainan", "Syed"),
    ("Ramesh", "Priya"),
    ("आइनान", "सुरेश"),
])
def test_two_different_people_are_still_two_different_people(stored, spoken):
    assert names_refer_to_same_person(stored, spoken) is False


@pytest_asyncio.fixture
async def seeded_db():
    assert db_mod.IS_SQLITE, "TEST SAFETY: refusing to run against a non-SQLite database"
    db_mod._import_all_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as s:
        s.add(Tenant(id=TENANT_ID, clinic_name="Indiana Hospital Mangalore",
                     admin_email="daylookup@example.com"))
        s.add(Doctor(id=DOCTOR_ID, tenant_id=TENANT_ID, name="Salman",
                     specialization="Cardiologist"))
        s.add(Doctor(id=OTHER_DOCTOR_ID, tenant_id=TENANT_ID, name="Rajesh",
                     specialization="General Physician"))
        for did in (DOCTOR_ID, OTHER_DOCTOR_ID):
            for dow in range(7):
                s.add(DoctorAvailability(tenant_id=TENANT_ID, doctor_id=did, day_of_week=dow,
                                        start_time=dt.time(9, 0), end_time=dt.time(17, 0)))
        await s.commit()
    from backend.services.his import invalidate_doctor_cache
    invalidate_doctor_cache(TENANT_ID)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def _add_appointment(name: str, phone: str, hour: int = 15,
                           doctor_id: str = DOCTOR_ID, days: int = 1) -> str:
    slot = ist_wall_clock_to_utc(
        dt.datetime.combine(ist_now().date() + dt.timedelta(days=days), dt.time(hour, 0)))
    async with AsyncSessionLocal() as s:
        appt = Appointment(tenant_id=TENANT_ID, doctor_id=doctor_id, slot_time=slot,
                           patient_phone=phone, patient_name=name, status="confirmed",
                           source=SOURCE_VOICE)
        s.add(appt)
        await s.commit()
        return str(appt.id)


async def _statuses() -> list:
    async with AsyncSessionLocal() as s:
        return [(a.patient_name, a.status) for a in (await s.execute(
            select(Appointment).where(Appointment.tenant_id == TENANT_ID)
            .order_by(Appointment.slot_time.asc())
        )).scalars().all()]


@pytest.mark.asyncio
async def test_the_live_cancel_failure_now_cancels(seeded_db):
    """Stored 'आइनान', caller transcribed as 'ऐनान' — the row that could not be
    found for 280 seconds."""
    appt_id = await _add_appointment("आइनान", PHONE)

    found = await find_active_appointment(TENANT_ID, "ऐनान", PHONE)
    assert found and found["appointment_id"] == appt_id

    res = await execute_booking_action(
        action="CANCEL", tenant_id=TENANT_ID, name="ऐनान", phone=PHONE,
        date_str="", time_str="", doctor_name="", source=SOURCE_VOICE,
    )
    assert res["success"] is True, res
    assert await _statuses() == [("आइनान", "cancelled")]


@pytest.mark.asyncio
async def test_a_number_with_one_appointment_needs_no_name_at_all(seeded_db):
    """The number is the caller's own. Requiring a name spelled the same way it
    was stored is what made cancellation impossible."""
    await _add_appointment("आइनान", PHONE)
    found = await find_active_appointment(TENANT_ID, "", PHONE)
    assert found is not None


@pytest.mark.asyncio
async def test_the_phone_is_matched_however_it_is_written(seeded_db):
    await _add_appointment("Ainan", "+91 98450-12345")
    for spoken in ("9845012345", "+919845012345", "098450 12345"):
        assert await find_active_appointment(TENANT_ID, "Ainan", spoken) is not None


@pytest.mark.asyncio
async def test_one_patient_still_cannot_cancel_anothers_appointment(seeded_db):
    """The safety property the old both-must-match rule existed for. Two people
    share a name; the phone numbers differ; the wrong row must stay untouched."""
    await _add_appointment("Ainan", "9000000001", hour=10)
    await _add_appointment("Ainan", "9000000002", hour=11)

    res = await execute_booking_action(
        action="CANCEL", tenant_id=TENANT_ID, name="Ainan", phone="9000000001",
        date_str="", time_str="", doctor_name="", source=SOURCE_VOICE,
    )
    assert res["success"] is True
    async with AsyncSessionLocal() as s:
        rows = {a.patient_phone: a.status for a in (await s.execute(
            select(Appointment).where(Appointment.tenant_id == TENANT_ID)
        )).scalars().all()}
    assert rows == {"9000000001": "cancelled", "9000000002": "confirmed"}


@pytest.mark.asyncio
async def test_a_stranger_cannot_cancel_by_guessing_a_name(seeded_db):
    await _add_appointment("Ainan", "9000000001")
    res = await execute_booking_action(
        action="CANCEL", tenant_id=TENANT_ID, name="Ainan", phone="9999999999",
        date_str="", time_str="", doctor_name="", source=SOURCE_VOICE,
    )
    assert res["success"] is False
    assert res["reason"] == "not_found"
    assert await _statuses() == [("Ainan", "confirmed")]


@pytest.mark.asyncio
async def test_the_caller_with_several_appointments_gets_the_one_they_named(seeded_db):
    await _add_appointment("आइनान", PHONE, hour=10, doctor_id=DOCTOR_ID)
    later = await _add_appointment("Syed", PHONE, hour=16, doctor_id=OTHER_DOCTOR_ID)

    found = await find_active_appointment(TENANT_ID, "Syed", PHONE)
    assert found["appointment_id"] == later, "the name must pick between one number's bookings"


@pytest.mark.asyncio
async def test_the_agent_is_shown_the_callers_real_appointments(seeded_db):
    """So it stops asking for details the database already holds."""
    await _add_appointment("आइनान", PHONE, hour=15)
    rows = await caller_appointments(TENANT_ID, PHONE)
    assert len(rows) == 1
    assert rows[0]["doctor_name"] == "Salman"
    assert rows[0]["time"].lower().replace(" ", "").startswith("3")
    assert rows[0]["date"] == (ist_now().date() + dt.timedelta(days=1)).strftime("%d/%m/%Y")

    from backend.services.availability_prompt import caller_appointments_block

    block = await caller_appointments_block(TENANT_ID, PHONE)
    assert "Salman" in block
    assert "do NOT ask" in block or "Do NOT ask" in block
    assert await caller_appointments_block(TENANT_ID, "9999999999") == ""


@pytest.mark.parametrize("utterance,expected", [
    ("मेरा नंबर 9148768120 है", "9148768120"),
    # The one that corrupted it: a time follows the number, and stripping every
    # non-digit from the sentence yields 11 digits whose last 10 are a number the
    # caller does not have.
    ("मेरा नंबर 9148768120 है और 3 बजे", "9148768120"),
    ("my number is +91 98450 12345", "9845012345"),
    ("098450-12345 pe call karo", "9845012345"),
    ("3 बजे", None),
    ("I'll come at 11", None),
])
def test_the_callers_number_is_read_out_of_their_sentence_intact(utterance, expected):
    from backend.agent.processors.booking_processor import BookingProcessor

    proc = BookingProcessor(
        tenant={"id": TENANT_ID, "doctors": []},
        agent_config={},
        call_meta={"caller_phone": "unknown"},
    )
    proc._note_what_the_caller_said(utterance)
    assert proc._call_meta.get("stated_phone") == expected


@pytest.mark.asyncio
async def test_a_reschedule_moves_the_day_the_caller_asked_for(seeded_db):
    """Day words go through the resolver on the reschedule path too."""
    await _add_appointment("आइनान", PHONE, hour=15, days=1)
    res = await execute_booking_action(
        action="RESCHEDULE", tenant_id=TENANT_ID, name="ऐनान", phone=PHONE,
        date_str="परसों", time_str="11 AM", doctor_name="", source=SOURCE_VOICE,
    )
    assert res["success"] is True, res
    async with AsyncSessionLocal() as s:
        appt = (await s.execute(select(Appointment))).scalars().one()
    moved = to_ist(appt.slot_time if appt.slot_time.tzinfo
                   else appt.slot_time.replace(tzinfo=dt.timezone.utc))
    assert moved.date() == ist_now().date() + dt.timedelta(days=2)
    assert moved.hour == 11
    assert appt.rescheduled_at is not None, (
        "a real move must set rescheduled_at — it's the only thing that lets "
        "the dashboards show a distinct 'Rescheduled' (blue) badge instead of "
        "an indistinguishable 'Confirmed' (green) one"
    )
    assert appt.status == "confirmed", "rescheduled_at must never change status itself"


@pytest.mark.asyncio
async def test_a_no_op_reschedule_does_not_mark_it_as_rescheduled(seeded_db):
    """Asking to move an appointment to the time it is ALREADY at is a success
    (nothing is broken), but it is not a MOVE — the blue badge would be a lie."""
    await _add_appointment("आइनान", PHONE, hour=15, days=1)
    tomorrow_tag = (ist_now().date() + dt.timedelta(days=1)).strftime("%d/%m/%Y")
    res = await execute_booking_action(
        action="RESCHEDULE", tenant_id=TENANT_ID, name="ऐनान", phone=PHONE,
        date_str=tomorrow_tag, time_str="3 PM", doctor_name="", source=SOURCE_VOICE,
    )
    assert res["success"] is True and res["reason"] == "already_at_that_time"
    async with AsyncSessionLocal() as s:
        appt = (await s.execute(select(Appointment))).scalars().one()
    assert appt.rescheduled_at is None
