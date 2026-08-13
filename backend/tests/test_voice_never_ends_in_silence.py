"""
A voice turn must never end without the caller hearing something.

Measured live 2026-08-13, call 7b775fc9. The caller said "जी ठीक है", the
appointment row was written at 08:29:00, and the agent never spoke again — the
caller sat saying "हेलो? हेलो?" into a line that was still open. Reported four
times before this; every previous fix patched one branch of
VoiceActionProcessor._on_response_end.

Two distinct defects, both reproduced below.

1. **Giving up in silence.** The worker log for that call shows two
   `voice_action` errors nine seconds apart: the unspeakable-reply path fired,
   re-prompted once, got a second unspeakable reply, and then took the
   `_repaired_this_turn` branch — "giving up on this turn rather than looping".
   Giving up was correct; giving up without speaking was not. Every terminal path
   there resolved into either already-spoken text or ANOTHER LLM generation, so
   when the LLM was the broken part there was nothing left to say anything.

2. **A late tag attached to a QUESTION.** Recovery 1 skipped the re-run whenever
   the action succeeded, on the assumption that the words already spoken were a
   confirmation. In that call the model spoke "क्या यह समय आपके लिए उपयुक्त है?"
   and appended a BOOK tag. The booking succeeded, the re-run was skipped as
   redundant, and the caller was left answering a question about an appointment
   that already existed.

The fix keys off the condition the caller actually experiences — nothing was
spoken — rather than enumerating the branches that can cause it, so a future
branch that forgets to speak is covered before it is written.

Run: python -m pytest backend/tests/test_voice_never_ends_in_silence.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest

from backend.agent import spoken_fallback

# Reuse the real-pipeline harness — the point is the processor's position between
# `llm` and `tag_scrub`, which a standalone unit test cannot see.
from backend.tests.test_voice_action_tag_e2e import (  # noqa: E402
    CALL_ID,
    DOCTOR_NAME,
    PATIENT,
    PHONE,
    TOMORROW_TAG,
    VoiceCall,
    _appointments,
    _book_tag,
    seeded_db,  # noqa: F401  (fixture)
)

#: A tag the parser can never complete — the shape a token cap produces when it
#: cuts a reply off mid-tag. Nothing in it is speakable, which is what left the
#: caller in silence.
TRUNCATED_TAG = f"[ACTION: BOOK|{PATIENT}|{PHONE}|{TOMORROW_TAG}|02:00 PM"

HINDI = {"language": "hi-IN"}


@pytest.mark.asyncio
async def test_two_unspeakable_replies_still_say_something(seeded_db):
    """Defect 1, exactly as it happened. The model produces nothing speakable
    twice; the system correctly refuses to loop a third time, and must speak."""
    async with VoiceCall([TRUNCATED_TAG, TRUNCATED_TAG], agent_config=HINDI) as call:
        await call.says("मुझे कल दोपहर दो बजे अपॉइंटमेंट चाहिए", expect_generations=2)
        spoken = call.speaker.spoken

    assert spoken, "the caller heard NOTHING — this is the reported hang"
    assert "[ACTION" not in spoken, f"a machine tag reached TTS: {spoken!r}"
    assert spoken == spoken_fallback.sentence(
        spoken_fallback.NOT_UNDERSTOOD, "hi-IN",
    ), f"expected the Hindi retry sentence, got {spoken!r}"


@pytest.mark.asyncio
async def test_a_successful_booking_is_always_announced(seeded_db):
    """The worst version of defect 1: the row EXISTS and the caller is never told.

    A compliant tag books the row, then every attempt to report it produces
    nothing speakable. The caller must still hear that they are booked — saying
    "sorry, that didn't work" over a real appointment would trade silence for a
    lie, so the backstop states the real outcome.
    """
    async with VoiceCall(
        [_book_tag(), TRUNCATED_TAG, TRUNCATED_TAG], agent_config=HINDI,
    ) as call:
        await call.says("कल दोपहर दो बजे डॉक्टर सलमान के पास, मेरा नाम आइनान है",
                        expect_generations=3)
        spoken = call.speaker.spoken

    rows = await _appointments()
    assert len(rows) == 1, f"the booking itself regressed: {rows}"
    assert spoken == spoken_fallback.sentence(spoken_fallback.BOOKED, "hi-IN"), (
        f"the row exists but the caller was told {spoken!r}"
    )


@pytest.mark.asyncio
async def test_a_late_tag_on_a_question_still_gets_a_confirmation(seeded_db):
    """Defect 2, verbatim from the live call: the model ASKS whether the time
    suits, and appends the tag. The booking succeeds. The caller must be told —
    not left answering a question about an appointment that already exists."""
    asked = "क्या यह समय आपके लिए उपयुक्त है?"
    async with VoiceCall(
        [asked + " " + _book_tag(), "आपकी अपॉइंटमेंट पक्की हो गई है।"],
        agent_config=HINDI,
    ) as call:
        await call.says("मेरा नाम आइनान है और मेरा नंबर है 9148768120",
                        expect_generations=2)
        spoken = call.speaker.spoken

    assert len(await _appointments()) == 1
    assert "पक्की हो गई है" in spoken, (
        f"the booking succeeded but the caller only heard {spoken!r}"
    )


@pytest.mark.asyncio
async def test_a_late_tag_on_a_real_confirmation_is_not_repeated(seeded_db):
    """The other half of defect 2 — do not over-correct. When the words already
    spoken DO claim the booking, and the booking really succeeded, a second
    sentence would just say it twice."""
    said = "आपकी अपॉइंटमेंट बुक हो गई है।"
    async with VoiceCall([said + " " + _book_tag()], agent_config=HINDI) as call:
        await call.says("हाँ जी ठीक है", expect_generations=1)
        spoken = call.speaker.spoken

    assert len(await _appointments()) == 1
    assert spoken.count("बुक हो गई है") == 1, (
        f"the confirmation was spoken more than once: {spoken!r}"
    )


@pytest.mark.parametrize("action,expected", [
    ("CANCEL", spoken_fallback.CANCELLED),
    ("RESCHEDULE", spoken_fallback.RESCHEDULED),
])
@pytest.mark.asyncio
async def test_cancel_and_reschedule_get_the_same_guarantee(
    seeded_db, action, expected,
):
    """The guarantee is channel-wide, not booking-only. VoiceActionProcessor sees
    every response end, so one backstop covers all three actions — this asserts
    that rather than trusting it, because cancel and reschedule were reported as
    hanging too and no transcript was ever attached for cancel."""
    # Give the caller an appointment to act on, so the action really succeeds.
    async with VoiceCall([_book_tag(), "बुक हो गई।"], agent_config=HINDI) as call:
        await call.says("कल दो बजे डॉक्टर सलमान, मैं आइनान", expect_generations=2)
    assert len(await _appointments()) == 1

    tag = f"[ACTION: {action}|{PATIENT}|{PHONE}|{TOMORROW_TAG}|03:00 PM|{DOCTOR_NAME}|N/A]"
    async with VoiceCall([tag, TRUNCATED_TAG, TRUNCATED_TAG],
                         agent_config=HINDI, call_record_id=None) as call:
        await call.says("उसको बदल दो", expect_generations=3)
        spoken = call.speaker.spoken

    assert spoken == spoken_fallback.sentence(expected, "hi-IN"), (
        f"a {action} ended without telling the caller: {spoken!r}"
    )


# ── The phrase table itself ───────────────────────────────────────────────────

def test_every_language_has_every_sentence():
    for lang in spoken_fallback.supported_languages():
        for key in (spoken_fallback.BOOKED, spoken_fallback.CANCELLED,
                    spoken_fallback.RESCHEDULED, spoken_fallback.ACTION_FAILED,
                    spoken_fallback.NOT_UNDERSTOOD):
            assert spoken_fallback.sentence(key, lang).strip(), f"{lang}/{key} empty"


def test_an_unknown_language_or_key_still_returns_speech():
    """This module exists to stop a call dying, so it must never raise and never
    return empty — both degradations are still speech."""
    assert spoken_fallback.sentence(spoken_fallback.BOOKED, "xx-YY") == \
        spoken_fallback.sentence(spoken_fallback.BOOKED, "en-IN")
    assert spoken_fallback.sentence("not_a_real_key", None).strip()
    assert spoken_fallback.sentence(spoken_fallback.BOOKED, None).strip()


def test_the_sentences_carry_no_placeholders():
    """They are constants precisely so they cannot fail to format. A stray
    brace or percent would reach TTS literally, or crash the one path that must
    not crash."""
    for lang in spoken_fallback.supported_languages():
        for key in (spoken_fallback.BOOKED, spoken_fallback.ACTION_FAILED,
                    spoken_fallback.NOT_UNDERSTOOD):
            s = spoken_fallback.sentence(key, lang)
            assert "{" not in s and "}" not in s and "%s" not in s, s


def test_a_failed_action_is_not_reported_as_success():
    assert spoken_fallback.outcome_key("BOOK", True) == spoken_fallback.BOOKED
    assert spoken_fallback.outcome_key("BOOK", False) == spoken_fallback.ACTION_FAILED
    assert spoken_fallback.outcome_key("CANCEL", True) == spoken_fallback.CANCELLED
    assert spoken_fallback.outcome_key("RESCHEDULE", True) == spoken_fallback.RESCHEDULED
    # An action name nobody recognises must not be announced as a success.
    assert spoken_fallback.outcome_key("FROBNICATE", True) == spoken_fallback.ACTION_FAILED
