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

import asyncio

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
    _ist_hour,
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


# ── The tag-only reply, reported live on a Hindi CANCEL (2026-08-14) ──────────
#
# The captured event: the model's entire response was the raw [ACTION: …] tag,
# with nothing speakable around it, and the system logged "the whole reply was an
# unparseable machine tag" and re-prompted. Two separate defects made that
# unrecoverable, and they are independent — either one alone loses the call.
#
#   1. The tag was unparseable only because of its FIELD COUNT. ACTION_RE demands
#      six '|' fields; a CANCEL needs just name and phone, and the repair prompt
#      tells the model to fill the other three with N/A, one step from omitting
#      them. See services/action_tag.py::_parse_short_action_tag.
#   2. When the re-prompt then failed too, the backstop sentence was pushed as a
#      plain TextFrame AFTER the response's end frame — so its last sentence was
#      stranded in the TTS sentence aggregator and never spoken. Covered by every
#      test above, now that SpeakerSink models the real aggregation contract.


#: Three fields for a CANCEL, five for a RESCHEDULE: both short of the six
#: ACTION_RE requires, and both exactly what the tag instructions imply.
_SHORT_TAGS = {
    "CANCEL": f"[ACTION: CANCEL|{PATIENT}|{PHONE}]",
    "RESCHEDULE": f"[ACTION: RESCHEDULE|{PATIENT}|{PHONE}|{TOMORROW_TAG}|04:00 PM]",
}


@pytest.mark.parametrize("action", ["CANCEL", "RESCHEDULE"])
@pytest.mark.asyncio
async def test_a_tag_with_too_few_fields_still_acts(seeded_db, action):
    """Defect 1, for the two intents whose tags the model most often shortens.

    Asserted on the DATABASE, because the spoken reply cannot tell the two flows
    apart: an unexecuted short tag is unspeakable, which re-prompts, which speaks
    the model's NEXT reply — the same words, over a row that never changed. Only
    the row shows whether anything happened.
    """
    async with VoiceCall([_book_tag(), "बुक हो गई।"], agent_config=HINDI) as call:
        await call.says("कल दो बजे डॉक्टर सलमान, मैं आइनान", expect_generations=2)
    assert len(await _appointments()) == 1

    async with VoiceCall([_SHORT_TAGS[action], "हो गया।"], agent_config=HINDI,
                         call_record_id=None) as call:
        await call.says("उसको बदल दो", expect_generations=2)
        spoken = call.speaker.spoken

    rows = await _appointments()
    assert len(rows) == 1
    if action == "CANCEL":
        assert rows[0].status == "cancelled", (
            "the short CANCEL tag was not executed — nothing was cancelled"
        )
    else:
        assert _ist_hour(rows[0]) == 16, (
            "the short RESCHEDULE tag was not executed — the row never moved"
        )
    assert spoken == "हो गया।", spoken


@pytest.mark.asyncio
async def test_a_tag_still_short_of_a_name_and_number_is_not_guessed_at(seeded_db):
    """The floor on defect 1's fix. Name and phone are what an appointment is
    found by, so a tag carrying neither must stay unparseable — the caller is
    asked, and the backstop covers the silence."""
    async with VoiceCall([f"[ACTION: CANCEL|{PATIENT}]", TRUNCATED_TAG],
                         agent_config=HINDI) as call:
        await call.says("रद्द कर दो", expect_generations=2)
        spoken = call.speaker.spoken

    assert not await _appointments(), "a tag with no phone number wrote a row"
    assert spoken == spoken_fallback.sentence(
        spoken_fallback.NOT_UNDERSTOOD, "hi-IN"), spoken


@pytest.mark.asyncio
async def test_releasing_an_ignored_second_tag_is_not_counted_as_speech(seeded_db):
    """The backstop is disarmed by _spoke_this_turn, so that flag must mean "the
    caller heard something" and never "a frame was pushed".

    One action per utterance is the rule, so a SECOND tag in the same turn is
    released as ordinary text rather than executed — and tag_scrub then deletes
    all of it before TTS. Counting that as speech switched the backstop off for a
    turn in which the caller heard nothing at all: the booking below exists and
    they were never told.
    """
    async with VoiceCall([_book_tag(), _book_tag(), TRUNCATED_TAG],
                         agent_config=HINDI) as call:
        await call.says("कल दोपहर दो बजे डॉक्टर सलमान के पास, मेरा नाम आइनान है",
                        expect_generations=3)
        spoken = call.speaker.spoken

    assert len(await _appointments()) == 1, "the booking itself regressed"
    assert "[ACTION" not in spoken, f"a machine tag reached TTS: {spoken!r}"
    assert spoken == spoken_fallback.sentence(spoken_fallback.BOOKED, "hi-IN"), (
        f"the row exists but the caller heard {spoken!r}"
    )


# ── The captured event, end to end ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_captured_tag_only_cancel_needs_no_recovery_at_all(seeded_db):
    """The reported event, reproduced: a Hindi CANCEL whose ENTIRE reply is the
    raw tag, with nothing speakable around it.

    Recovery is not the goal here — not needing it is. The generation count is the
    assertion that matters: THREE generations means the tag was unreadable, the
    system re-prompted, and the caller waited through an extra LLM round trip for
    a cancel it had already asked for. TWO means the tag was executed on arrival
    and the second generation is simply the confirmation being spoken.
    """
    async with VoiceCall([_book_tag(), "बुक हो गई।"], agent_config=HINDI) as call:
        await call.says("कल दो बजे डॉक्टर सलमान, मैं आइनान", expect_generations=2)
    assert len(await _appointments()) == 1

    async with VoiceCall([_SHORT_TAGS["CANCEL"], "आपकी अपॉइंटमेंट रद्द कर दी गई है।"],
                         agent_config=HINDI, call_record_id=None) as call:
        await call.says("मेरी अपॉइंटमेंट रद्द कर दो, मैं आइनान", expect_generations=2)
        spoken, generations = call.speaker.spoken, call.llm.calls

    rows = await _appointments()
    assert rows[0].status == "cancelled", "the captured tag still cancels nothing"
    assert generations == 2, (
        f"the tag-only reply still needed a re-prompt ({generations} generations)"
    )
    assert "रद्द कर दी गई है" in spoken, spoken


# ── The three fixes, asserted at the mechanism ────────────────────────────────
#
# The tests above assert what the caller hears. These assert HOW, because in each
# case the mechanism is the fix: a change that reverts the mechanism while
# leaving today's audible output intact is a change that puts the hang back.


@pytest.mark.asyncio
async def test_the_backstop_is_spoken_on_a_ttsspeakframe(seeded_db):
    """Fix 1's mechanism. The backstop necessarily runs after the response's
    LLMFullResponseEndFrame, and by then a plain TextFrame cannot be synthesized
    in full — pipecat's TTS sentence aggregator is flushed only BY that frame
    (tts_service.py:711-725), so a later TextFrame's last sentence is stranded.
    Only TTSSpeakFrame is unconditional (tts_service.py:758-778)."""
    async with VoiceCall([TRUNCATED_TAG, TRUNCATED_TAG], agent_config=HINDI) as call:
        await call.says("मुझे कल दोपहर दो बजे अपॉइंटमेंट चाहिए", expect_generations=2)
        types = call.speaker.frame_types

    assert types == ["TTSSpeakFrame"], (
        f"the backstop reached TTS as {types} — a TextFrame here loses its last "
        "sentence, and a one-sentence phrase loses all of it"
    )


@pytest.mark.asyncio
async def test_a_backstop_phrase_with_no_final_punctuation_is_still_spoken(
    seeded_db, monkeypatch,
):
    """Fix 1's mechanism, in its worst form. Today every phrase in
    spoken_fallback happens to be two sentences, so the stranding bug cost the
    caller only the second half — which is why it read as a wording problem for a
    year rather than as a hang. A phrase with nothing after its last word has
    NOTHING to strand: under the old TextFrame path the caller heard silence."""
    one_sentence = "एक ही वाक्य"          # no '।', no '.', no lookahead ever
    monkeypatch.setattr(spoken_fallback, "sentence",
                        lambda key, language: one_sentence)

    async with VoiceCall([TRUNCATED_TAG, TRUNCATED_TAG], agent_config=HINDI) as call:
        await call.says("कल दो बजे अपॉइंटमेंट", expect_generations=2)
        spoken = call.speaker.spoken

    assert spoken == one_sentence, (
        f"a fallback with no terminal punctuation was not spoken at all: {spoken!r}"
    )


@pytest.mark.asyncio
async def test_held_bracketed_prose_goes_out_before_the_end_frame(seeded_db):
    """Fix 2's mechanism. A reply that opens with a bracket is HELD until it is
    known not to be a tag, so it is released at the end of the response — and it
    has to be released BEFORE the end frame is forwarded, or the TTS aggregator
    never flushes its last sentence. Asserted on the ordering, by checking the
    text arrived as a TextFrame (i.e. through the aggregation path) AND arrived
    whole; the two together are only possible if it preceded the end frame."""
    reply = "[Note] We're open until 5 PM. Shall I book you in?"
    async with VoiceCall([reply], agent_config={"language": "en-IN"}) as call:
        await call.says("What time do you close?", expect_generations=1)
        spoken, types = call.speaker.spoken, call.speaker.frame_types

    assert "Shall I book you in?" in spoken, (
        f"the held prose lost its last sentence: {spoken!r}"
    )
    assert types and set(types) == {"TextFrame"}, types


@pytest.mark.asyncio
async def test_an_inaudible_frame_does_not_disarm_the_backstop(seeded_db):
    """Fix 3's mechanism, stated directly. `_spoke_this_turn` is what switches the
    backstop off, so it must be set only when something SURVIVES tag_scrub. Here
    the whole turn's output is tags, which scrub to nothing — the flag must stay
    false even though frames were pushed."""
    async with VoiceCall([_book_tag(), _book_tag(), TRUNCATED_TAG],
                         agent_config=HINDI) as call:
        await call.says("कल दोपहर दो बजे डॉक्टर सलमान के पास, मेरा नाम आइनान है",
                        expect_generations=3)
        # Read the flag on the real processor, not inferred from the audio: this
        # is the invariant, and the audible symptom is downstream of it.
        assert call.action._spoke_this_turn is True, (
            "the backstop ran, so by then something WAS spoken"
        )
        assert call.speaker.frame_types[-1] == "TTSSpeakFrame", (
            "the last thing the caller heard was not the backstop"
        )


# ── Per-intent grammars ───────────────────────────────────────────────────────
#
# The three actions never needed the same fields, and the single shape is what
# forced the model to pad or shorten. The prompts now ask for one shape per
# intent, rendered from the same TAG_GRAMMAR the parser reads, so padding is no
# longer part of the primary path.


def test_each_intent_asks_for_only_the_fields_it_uses():
    from backend.services.action_tag import TAG_GRAMMAR, tag_template

    assert tag_template("BOOK") == "[ACTION: BOOK|Name|Phone|Date|Time|Doctor|Notes]"
    assert tag_template("CANCEL") == "[ACTION: CANCEL|Name|Phone]"
    assert tag_template("RESCHEDULE") == "[ACTION: RESCHEDULE|Name|Phone|NewDate|NewTime]"
    # A CANCEL is found by name + phone alone (his.py::sync_appointment_to_db), so
    # asking for a date is asking the caller for something nothing consults.
    assert TAG_GRAMMAR["CANCEL"] == ("Name", "Phone")


def test_the_voice_prompt_shows_the_grammar_and_nothing_else():
    """The prompt and the parser must not be able to disagree about what a CANCEL
    looks like — they did, and the disagreement was the bug: the prose said "Name
    and Phone, and NOTHING else" while the template showed four N/A fields nailed
    on. (The chat prompt's half of this is asserted in
    test_tag_recovery_is_shared.py, where the chat harness lives.)"""
    from backend.agent.booking_rules import voice_action_tag_block
    from backend.services.action_tag import tag_template

    prompt = voice_action_tag_block("Thursday, 14/08/2026")
    for action in ("BOOK", "CANCEL", "RESCHEDULE"):
        assert tag_template(action) in prompt, f"the {action} shape is never shown"
    assert "[ACTION: CANCEL|Name|Phone|N/A|N/A|N/A|N/A]" not in prompt, (
        "the prompt still teaches the padded CANCEL that caused the hang"
    )


@pytest.mark.parametrize("tag,shape", [
    ("[ACTION: CANCEL|Ainan|9148768120]", "grammar"),
    ("[ACTION: RESCHEDULE|Ainan|9148768120|15/08/2026|04:00 PM]", "grammar"),
    ("[ACTION: BOOK|Ainan|9148768120|15/08/2026|02:00 PM|Salman|N/A]", "grammar"),
    # The universal six-field form every intent used to share. Still accepted
    # for ever — it is what older prompts taught and what a model copies from a
    # BOOK example.
    ("[ACTION: CANCEL|Ainan|9148768120|N/A|N/A|N/A|N/A]", "canonical"),
    ("[ACTION: RESCHEDULE|Ainan|9148768120|15/08/2026|04:00 PM|Salman|N/A]", "canonical"),
    # A shape nobody asked for. Accepted, but it is the defensive net doing work
    # the prompt should have prevented — so it is visible, not silent.
    ("[ACTION: BOOK|Ainan|9148768120|15/08/2026]", "padded"),
    ("[ACTION: CANCEL|Ainan|9148768120|N/A]", "padded"),
])
def test_which_shape_a_tag_arrived_in_is_observable(tag, shape):
    from backend.services.action_tag import classify_tag_shape

    assert classify_tag_shape(tag) == shape


def test_the_defensive_fallback_says_so_in_the_log(caplog):
    """"No longer doing routine work" has to be checkable in production, not
    asserted here and assumed there."""
    import logging

    from backend.services.action_tag import parse_action_tag

    with caplog.at_level(logging.WARNING, logger="backend.services.action_tag"):
        assert parse_action_tag("[ACTION: CANCEL|Ainan|9148768120]") is not None
    assert not caplog.records, "a grammar-shaped tag was logged as a fallback parse"

    with caplog.at_level(logging.WARNING, logger="backend.services.action_tag"):
        assert parse_action_tag("[ACTION: BOOK|Ainan|9148768120|15/08/2026]") is not None
    assert any("shape nothing asked it for" in r.message for r in caplog.records)


# ── The gates must not have loosened ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_reschedule_in_its_own_grammar_still_needs_a_real_time(seeded_db):
    """RESCHEDULE's grammar carries a time, so the invalid_time gate must still
    fire when that field is a placeholder — a shorter tag must not become a way
    to skip validation."""
    async with VoiceCall([_book_tag(), "बुक हो गई।"], agent_config=HINDI) as call:
        await call.says("कल दो बजे डॉक्टर सलमान, मैं आइनान", expect_generations=2)
    before = await _appointments()
    assert len(before) == 1

    async with VoiceCall([f"[ACTION: RESCHEDULE|{PATIENT}|{PHONE}|{TOMORROW_TAG}|N/A]",
                          "कौन सा समय चाहिए?"],
                         agent_config=HINDI, call_record_id=None) as call:
        await call.says("उसको बदल दो", expect_generations=2)
        lines = call.llm.system_lines()

    after = await _appointments()
    assert _ist_hour(after[0]) == _ist_hour(before[0]), "the row moved on a placeholder time"
    assert any("No valid TIME was given" in line for line in lines), lines


@pytest.mark.asyncio
async def test_a_book_shortened_below_its_grammar_still_needs_a_real_time(seeded_db):
    """BOOK's grammar is all six fields. A model that shortens it anyway lands in
    the defensive path — where the time gate must still refuse, exactly as if the
    field had been blank."""
    async with VoiceCall([f"[ACTION: BOOK|{PATIENT}|{PHONE}|{TOMORROW_TAG}]",
                          "किस समय आना चाहेंगे?"], agent_config=HINDI) as call:
        await call.says("कल अपॉइंटमेंट चाहिए", expect_generations=2)
        lines = call.llm.system_lines()

    assert not await _appointments(), "a BOOK with no time created a row"
    assert any("No valid TIME was given" in line for line in lines), lines


@pytest.mark.asyncio
async def test_a_cancel_in_its_own_grammar_still_needs_a_real_identity(seeded_db):
    """CANCEL's grammar is exactly name + phone, which are the two fields the
    identity gate checks. A placeholder must still be refused — the shorter shape
    must not make the gate unreachable.

    Tested on the NAME, because a placeholder phone is legitimately filled from
    what the caller said or from caller ID (_execute's phone fallback) and so is
    not a refusal at all on a real call. There is no such evidence for a name.
    """
    async with VoiceCall([f"[ACTION: CANCEL|N/A|{PHONE}]", "आपका नाम बताइए?"],
                         agent_config=HINDI, call_record_id=None) as call:
        await call.says("रद्द कर दो", expect_generations=2)
        lines = call.llm.system_lines()

    assert any("their name" in line for line in lines), lines


# ── The short-tag parser itself ───────────────────────────────────────────────

@pytest.mark.parametrize("tag,expected", [
    # The shapes the model actually emits when it drops trailing fields.
    (f"[ACTION: CANCEL|{PATIENT}|{PHONE}]", (PATIENT, PHONE, "N/A", "N/A")),
    (f"[ACTION: CANCEL|{PATIENT}|{PHONE}|N/A|N/A]", (PATIENT, PHONE, "N/A", "N/A")),
    (f"[ACTION: BOOK|{PATIENT}|{PHONE}|{TOMORROW_TAG}|02:00 PM]",
     (PATIENT, PHONE, TOMORROW_TAG, "02:00 PM")),
])
def test_a_short_tag_keeps_the_fields_it_does_carry(tag, expected):
    from backend.services.action_tag import parse_action_tag

    parsed = parse_action_tag(tag)
    assert parsed is not None, f"still unparseable: {tag}"
    assert (parsed.name, parsed.phone, parsed.date, parsed.time) == expected


@pytest.mark.parametrize("tag", [
    "[ACTION: CANCEL|Ainan]",            # no phone number to find anything by
    "[ACTION: None]",                    # the 2026-08-10 production shape
    "[ACTION: BOOK|Ainan|9148768120",    # truncated by the token cap: no ']'
])
def test_what_must_stay_unparseable(tag):
    """A tag still mid-stream, or with too little to act on, must not be
    executed on a guess — the padding fills TRAILING fields only, and a
    bracket that never closed could still be growing."""
    from backend.services.action_tag import parse_action_tag

    assert parse_action_tag(tag) is None


def test_a_full_tag_is_unaffected_by_the_short_tag_path():
    """The strict regex still owns every well-formed tag: the fallback must not
    be able to re-interpret the fields of a tag that already parsed."""
    from backend.services.action_tag import parse_action_tag

    parsed = parse_action_tag(
        f"[ACTION: BOOK|{PATIENT}|{PHONE}|{TOMORROW_TAG}|02:00 PM|{DOCTOR_NAME}|Chest pain]")
    assert parsed == (
        "BOOK", PATIENT, PHONE, TOMORROW_TAG, "02:00 PM", DOCTOR_NAME, "Chest pain")


# ── The silence shield must not be lockable ───────────────────────────────────
#
# `call_logger.action_in_progress` STOPS the silence watchdog's clock
# (pipeline.py::_enforce_silence_timeout): while it is True no silence accrues and
# the call cannot be ended for a caller who is hearing nothing. Every path that
# cleared it depended on a FRAME arriving — LLMFullResponseStartFrame for the
# re-run, or _flush_rerun deciding none is needed. Both hold for the two installed
# LLM services, and nothing structurally enforces it: a provider added later, a
# dropped run frame, or a cancelled task leaves the flag set for the rest of the
# call, with the one mechanism that could rescue the caller switched off.


@pytest.mark.asyncio
async def test_a_dropped_rerun_still_reaches_the_caller(seeded_db):
    """The gap, reproduced: the re-run is requested and simply never happens.

    Nothing in the pipeline answers, so no frame ever comes back to clear the
    shield — the exact structural assumption that was unenforced. The independent
    timer must fire, release the shield, and speak.
    """
    async with VoiceCall([TRUNCATED_TAG], agent_config=HINDI,
                         swallow_reruns=True, busy_timeout_seconds=0.6) as call:
        await call.says("मुझे कल दोपहर दो बजे अपॉइंटमेंट चाहिए", expect_generations=1)
        # Long enough for the timer to expire; short enough that a test hang is
        # still a test failure rather than a wait.
        await asyncio.sleep(1.6)

        assert call.swallower.swallowed == 1, (
            "the re-run was never requested, so this test is not exercising the gap"
        )
        assert call.speaker.spoken == spoken_fallback.sentence(
            spoken_fallback.NOT_UNDERSTOOD, "hi-IN"), (
            f"the caller heard {call.speaker.spoken!r} — the timer did not rescue the turn"
        )
        assert call.call_logger.action_in_progress is False, (
            "the silence watchdog is still disabled — the call can never time out"
        )


@pytest.mark.asyncio
async def test_a_dropped_rerun_after_a_real_write_tells_the_caller_the_truth(seeded_db):
    """The worst version: the row EXISTS, and the reply that would have announced
    it is the thing that vanished. The caller must be told they are booked — the
    timeout must not turn a successful booking into an apology.

    The window is deliberately wide enough to cover the write itself, and the test
    waits for the row before letting the timer run out: this is about the SECOND
    busy window (waiting on a re-run that never comes), and a timer that expired
    mid-write would be exercising the other one.
    """
    async with VoiceCall([_book_tag()], agent_config=HINDI,
                         swallow_reruns=True, busy_timeout_seconds=3.0) as call:
        await call.says("कल दोपहर दो बजे डॉक्टर सलमान के पास, मेरा नाम आइनान है",
                        expect_generations=1)
        for _ in range(40):
            if len(await _appointments()) == 1:
                break
            await asyncio.sleep(0.05)
        assert len(await _appointments()) == 1, "the booking itself regressed"
        assert call.action._action_in_flight is None, "the write has not finished"

        await asyncio.sleep(3.4)
        spoken = call.speaker.spoken

    assert spoken == spoken_fallback.sentence(spoken_fallback.BOOKED, "hi-IN"), (
        f"the row exists but the caller was told {spoken!r}"
    )


@pytest.mark.asyncio
async def test_a_write_still_in_flight_is_not_reported_as_a_misheard_caller(seeded_db):
    """The other side of the same timer. If it fires while the write is genuinely
    still running there IS no outcome yet, so the backstop must not say "sorry, I
    didn't catch that" — the caller was understood perfectly and their booking may
    still be landing. It says it could not be completed, and points them at the
    clinic."""
    async with VoiceCall([_book_tag()], agent_config=HINDI,
                         swallow_reruns=True, busy_timeout_seconds=0.01) as call:
        await call.says("कल दोपहर दो बजे डॉक्टर सलमान के पास, मेरा नाम आइनान है",
                        expect_generations=1)
        await asyncio.sleep(1.2)
        spoken = call.speaker.spoken

    assert spoken in (
        spoken_fallback.sentence(spoken_fallback.ACTION_FAILED, "hi-IN"),
        spoken_fallback.sentence(spoken_fallback.BOOKED, "hi-IN"),
    ), f"a caller mid-booking was told {spoken!r}"
    assert spoken != spoken_fallback.sentence(
        spoken_fallback.NOT_UNDERSTOOD, "hi-IN"), (
        "a caller whose booking was in flight was told they had been misheard"
    )


@pytest.mark.asyncio
async def test_the_shield_is_not_released_early_on_a_normal_turn(seeded_db):
    """The other half: a timer that fires during legitimate work would end the
    shield mid-booking and speak over the real reply. A normal booking turn must
    complete with the timer never firing and nothing extra spoken."""
    async with VoiceCall([_book_tag(), "आपकी अपॉइंटमेंट पक्की हो गई है।"],
                         agent_config=HINDI, busy_timeout_seconds=5.0) as call:
        await call.says("कल दोपहर दो बजे डॉक्टर सलमान के पास, मेरा नाम आइनान है",
                        expect_generations=2)
        spoken = call.speaker.spoken
        # The shield must be DOWN once the turn resolves — a stuck flag here is
        # the same permanent disablement, just reached by the happy path.
        assert call.call_logger.action_in_progress is False

    assert len(await _appointments()) == 1
    assert spoken == "आपकी अपॉइंटमेंट पक्की हो गई है।", (
        f"the backstop fired during a healthy turn: {spoken!r}"
    )


@pytest.mark.asyncio
async def test_a_new_utterance_never_leaves_the_shield_pinned(seeded_db):
    """A caller who speaks again while a turn is still 'in progress' supersedes
    it. Cancelling the timer alone would leave the flag set with nothing timing
    it — the unbounded state, re-created by the fix for it."""
    async with VoiceCall([TRUNCATED_TAG, "जी बताइए?"], agent_config=HINDI,
                         swallow_reruns=True, busy_timeout_seconds=30.0) as call:
        await call.says("रद्द कर दो", expect_generations=1)
        assert call.call_logger.action_in_progress is True, (
            "the shield was never raised, so this test proves nothing"
        )
        await call.says("हैलो? सुन रहे हैं?", expect_generations=1)

        assert call.call_logger.action_in_progress is False, (
            "the caller spoke again and the silence shield stayed up with no timer "
            "left to release it"
        )
        assert call.action._busy_timer is None


def test_the_busy_timeout_sits_below_the_silence_timeout():
    """The shield expiring must give the caller an answer BEFORE the watchdog
    would end their call outright. pipeline.py floors the silence timeout at 20s
    (`max(int(...), 20)`), so the busy window has to stay under that."""
    from backend.agent.processors.voice_action import BUSY_TIMEOUT_SECONDS

    assert 0 < BUSY_TIMEOUT_SECONDS < 20, BUSY_TIMEOUT_SECONDS


# ── The live verification path ────────────────────────────────────────────────
#
# Everything above runs in a simulated pipeline. This environment cannot publish
# or subscribe WebRTC audio (UDP is blocked), so no test in this repo can prove
# anything about a real call — which is how five rounds of green tests and green
# deploys coexisted with a caller hearing silence.
#
# scripts/verify_no_silent_turns.py closes that gap by checking the invariant
# against production call_records instead of audio: every user turn must be
# followed by an assistant turn. The checker itself is pure, so its logic is
# testable here even though its subject is not.

def _checker():
    """Load the script's checker without importing it as a package module."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "verify_no_silent_turns.py"
    spec = importlib.util.spec_from_file_location("verify_no_silent_turns", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check_transcript


def _u(turn, text):
    return {"turn": turn, "role": "user", "text": text}


def _a(turn, text):
    return {"turn": turn, "role": "assistant", "text": text}


def test_the_live_checker_passes_a_healthy_call():
    assert _checker()([
        _u(1, "मुझे अपॉइंटमेंट चाहिए"), _a(2, "किस डॉक्टर के साथ?"),
        _u(3, "डॉक्टर सलमान"), _a(4, "बुक हो गई।"),
    ]) == []


@pytest.mark.parametrize("transcript,expect", [
    # The reported symptom, exactly: the caller spoke and nothing came back.
    ([_u(1, "हैलो"), _a(2, "नमस्ते"), _u(3, "रद्द कर दो")], "CALLER spoke last"),
    # The same failure mid-call — visible as the caller repeating themselves into
    # a dead line ("हेलो? हेलो?"), which is what the live transcripts showed.
    ([_u(1, "रद्द कर दो"), _u(2, "हेलो? हेलो?"), _a(3, "माफ़ कीजिए")],
     "two caller turns in a row"),
    # Not silence, but the other way this processor can fail a caller.
    ([_u(1, "हैलो"), _a(2, "ठीक [ACTION: BOOK|a|b|c|d|e|f]")], "machine tag was SPOKEN"),
])
def test_the_live_checker_catches_every_shape_of_the_bug(transcript, expect):
    failures = _checker()(transcript)
    assert any(expect in f for f in failures), failures


def test_the_live_checker_never_passes_a_call_it_cannot_read():
    """A missing transcript must not read as a pass — that is how "verified" gets
    claimed for calls nobody checked."""
    for empty in ([], None, [None, "junk"]):
        assert _checker()(empty), f"{empty!r} was reported clean"


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
