# -*- coding: utf-8 -*-
"""
One recovery, two channels.

Voice (``agent/processors/voice_action.py``) and chat/embed
(``routers/agent_test.py::_handle_booking_action``) used to carry their own copies
of the same decision — "this reply has no parseable [ACTION:] tag; is it safe as
it stands, does it earn one re-prompt, or is this turn out of chances?" — and
their own wording of the same re-prompt. They shared the parser and nothing else,
and they had already drifted three times, each time in a way that cost a real
conversation:

  * chat sent its "you told the patient it was already done" re-prompt even when
    the reply was an unreadable machine tag and the model had told the patient
    nothing at all;
  * voice's re-prompt carried the fact that a CANCEL needs only a name and a phone
    number, chat's did not;
  * chat's terminal reply existed in two languages, voice's equivalent in seven.

These tests exist so that cannot recur. They drive the SAME mangled tag through
BOTH entry points and assert the same recovery, and then — the part a behavioural
test cannot show — assert that both channels obtained their re-prompt from the one
function, by substituting it and watching the substitution arrive on both sides.

Run: python -m pytest backend/tests/test_tag_recovery_is_shared.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from unittest.mock import patch  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

from backend.services import tag_recovery  # noqa: E402

# The voice harness owns the database and the seeded clinic; the chat path is
# pointed at the SAME clinic, so "both channels" means one tenant, one doctor and
# one appointments table — not two test worlds that merely resemble each other.
from backend.tests.test_voice_action_tag_e2e import (  # noqa: E402
    AGENT_ID,
    DOCTOR_NAME,
    PATIENT,
    PHONE,
    TOMORROW_TAG,
    VoiceCall,
    _appointments,
    _ist_hour,
    seeded_db,  # noqa: F401  (fixture)
)

#: A complete, correctly-bracketed tag that neither parser can read — the bare
#: "[ACTION: None]" observed in production 2026-08-10. Scrubbing it leaves
#: nothing, so on chat the patient gets an empty message and on voice the caller
#: gets silence. This is the input both channels must recover from.
MANGLED = "[ACTION: None]"

#: A reply that carries no tag and needs no recovery: it claims nothing, promises
#: nothing, and is simply a question.
INNOCENT = "Which doctor would you like to see?"

VOICE_TAG = f"[ACTION: BOOK|{PATIENT}|{PHONE}|{TOMORROW_TAG}|02:00 PM|{DOCTOR_NAME}|N/A]"
CHAT_TAG = f"[ACTION: BOOK|{PATIENT}|{PHONE}|{TOMORROW_TAG}|03:00 PM|{DOCTOR_NAME}|N/A]"


async def _chat_agent(db):
    from backend.models.agent_config import AgentConfig

    return (await db.execute(
        select(AgentConfig).where(AgentConfig.id == AGENT_ID))).scalar_one()


async def _run_chat(replies: dict, *, message: str, language: str = "en-IN"):
    """Drive the real chat handler with a scripted LLM.

    ``replies`` maps a phase name to the reply the model gives: 'first' (the
    broken one), 'repair' (its second attempt) and 'regen' (the reply generated
    from the real booking outcome). Returns (reply, captured_system_prompts).
    """
    from backend.db import AsyncSessionLocal
    from backend.routers import agent_test as chat_mod

    captured: list[str] = []

    async def fake_dispatch(provider, api_key, system_prompt, history, model, max_tokens):
        # Keyed on call ORDER, not on prompt text. The chat handler's sequence is
        # deterministic — phase 1, then the repair only if recovery fired, then the
        # regeneration from the real outcome — and matching on substrings is how a
        # scripted LLM ends up answering the wrong phase (the base prompt's booking
        # rules quote the very markers the later phases carry).
        captured.append(system_prompt)
        return replies[("first", "repair", "regen")[min(len(captured), 3) - 1]]

    chat_mod._conversation_history.clear()
    chat_mod._avail_cache.clear()
    with patch.object(chat_mod, "_dispatch_llm", side_effect=fake_dispatch), \
            patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
        async with AsyncSessionLocal() as db:
            reply = await chat_mod.generate_llm_response(
                await _chat_agent(db), message, db,
                session_id="s-shared", user_language=language,
            )
    return reply, captured


# ── The same input, the same decision ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_same_mangled_tag_is_repaired_into_a_real_row_on_both_channels(
    seeded_db,
):
    """The behavioural half. One unreadable tag, two channels, two real rows —
    and neither conversation ends on a promise or on nothing."""
    async with VoiceCall([MANGLED, VOICE_TAG, "Done — 2 PM tomorrow with Dr Salman."]) as call:
        await call.says("Book me with Dr Salman tomorrow at 2 PM, I'm Ainan",
                        expect_generations=3)
        spoken = call.speaker.spoken

    chat_reply, _ = await _run_chat(
        {"first": MANGLED, "repair": CHAT_TAG,
         "regen": "Done — 3 PM tomorrow with Dr Salman."},
        message="Book me with Dr Salman tomorrow at 3 PM, I'm Ainan",
    )

    rows = await _appointments()
    assert len(rows) == 2, f"one channel failed to recover into a row: {rows}"
    assert sorted(_ist_hour(r) for r in rows) == [14, 15]

    # Both conversations RESOLVE. A claim of completion is correct here — the rows
    # above are what make it true — so what must be absent is a promise of a
    # later message, which is the shape that strands a user on either channel.
    from backend.services.action_tag import promises_followup

    for who, text in (("the caller", spoken), ("the patient", chat_reply)):
        assert text.strip(), f"{who} got nothing at all"
        assert "[ACTION" not in text, f"a machine tag reached {who}: {text!r}"
        assert not promises_followup(text), f"{who} was left waiting: {text!r}"


@pytest.mark.asyncio
async def test_an_innocent_reply_is_left_alone_on_both_channels(seeded_db):
    """The other half of the shared decision, which matters just as much: a reply
    that claims nothing must not cost an extra LLM call on either channel. A
    recovery that fires on good replies is a recovery nobody can afford to keep."""
    async with VoiceCall([INNOCENT]) as call:
        await call.says("I'd like an appointment", expect_generations=1)
        assert call.speaker.spoken == INNOCENT
        assert call.llm.calls == 1, "voice re-prompted on a perfectly good reply"

    chat_reply, captured = await _run_chat(
        {"first": INNOCENT, "repair": "unused", "regen": "unused"},
        message="I'd like an appointment",
    )
    assert chat_reply == INNOCENT
    assert len(captured) == 1, "chat re-prompted on a perfectly good reply"
    assert not await _appointments()


@pytest.mark.asyncio
async def test_the_chat_prompt_shows_the_per_intent_grammar(seeded_db):
    """The chat half of the grammar assertion (voice's is in
    test_voice_never_ends_in_silence.py). Both prompts render the shapes from
    action_tag.TAG_GRAMMAR, so neither can teach the model a tag the parser
    cannot read — which is exactly what the padded CANCEL was."""
    from backend.services.action_tag import tag_template

    _, captured = await _run_chat(
        {"first": INNOCENT, "repair": "unused", "regen": "unused"},
        message="I'd like an appointment",
    )
    prompt = captured[0]
    for action in ("BOOK", "CANCEL", "RESCHEDULE"):
        assert tag_template(action) in prompt, f"the {action} shape is never shown"
    assert "[ACTION: CANCEL|Name|Phone|Date|Time|Doctor|Notes]" not in prompt, (
        "the chat prompt still asks a CANCEL for fields nothing consults"
    )


# ── The structural half: it is literally the same function ────────────────────

@pytest.mark.asyncio
async def test_both_channels_read_the_re_prompt_from_the_one_function(seeded_db):
    """Substitute the shared re-prompt and watch it arrive on both channels.

    This is the test that a behavioural comparison cannot be: two independent
    copies of the wording can pass every assertion above and still drift apart the
    next time one of them is edited. Only one thing makes a substitution here
    appear in what BOTH models are told — both call sites reading it from here.
    """
    SENTINEL = "SENTINEL-REPAIR-INSTRUCTION-9148768120"
    seen: list[bool] = []

    def fake_instruction(reason, *, spoken):
        seen.append(spoken)
        return f"{SENTINEL} reason={reason} spoken={spoken}"

    with patch.object(tag_recovery, "repair_instruction", side_effect=fake_instruction):
        async with VoiceCall([MANGLED, VOICE_TAG, "Booked."]) as call:
            await call.says("Book me with Dr Salman tomorrow at 2 PM, I'm Ainan",
                            expect_generations=3)
            voice_system = call.llm.system_lines()

        _, chat_system = await _run_chat(
            {"first": MANGLED, "repair": CHAT_TAG, "regen": "Booked."},
            message="Book me with Dr Salman tomorrow at 3 PM, I'm Ainan",
        )

    assert any(SENTINEL in line for line in voice_system), (
        "the voice path did not get its re-prompt from tag_recovery"
    )
    assert any(SENTINEL in prompt for prompt in chat_system), (
        "the chat path did not get its re-prompt from tag_recovery"
    )
    # Same function, same reason, different register — the one thing the two
    # channels are entitled to differ on.
    assert sorted(seen) == [False, True], (
        f"expected one spoken and one written call, got {seen}"
    )


def test_both_channels_import_the_same_decision():
    """No local re-implementation may creep back in beside the shared one."""
    from backend.agent.processors import voice_action
    from backend.routers import agent_test as chat_mod

    for module in (voice_action, chat_mod):
        assert module.tag_recovery.classify_untagged_reply is \
            tag_recovery.classify_untagged_reply
        assert module.tag_recovery.repair_instruction is \
            tag_recovery.repair_instruction


# ── The decision itself ───────────────────────────────────────────────────────

@pytest.mark.parametrize("reply,reason", [
    (MANGLED, tag_recovery.TAG_ONLY),
    ("[ACTION: BOOK|Ainan|9148768120|01/01/2027|2 PM", tag_recovery.TAG_ONLY),
    ("Your appointment with Dr Salman is booked.", tag_recovery.FABRICATED),
    ("आपकी अपॉइंटमेंट बुक कर दी गई है।", tag_recovery.FABRICATED),
    ("Please hold on while I complete your booking.", tag_recovery.PROMISED),
    ("मैं इस अपॉइंटमेंट को कैंसिल करने की प्रक्रिया शुरू करूंगा।", tag_recovery.PROMISED),
])
def test_why_a_reply_needs_recovery(reply, reason):
    r = tag_recovery.classify_untagged_reply(reply)
    assert r.decision == tag_recovery.REPAIR
    assert r.reason == reason


@pytest.mark.parametrize("reply", [
    INNOCENT,
    "We're open until 5 PM.",
    "Just to confirm, you'd like 3 PM with Dr Salman? Reply yes to proceed.",
    "",
])
def test_a_reply_that_needs_nothing(reply):
    assert tag_recovery.classify_untagged_reply(reply).decision == \
        tag_recovery.PASS_THROUGH


def test_the_once_per_turn_cap_is_shared_too():
    """Both channels allow exactly one repair per user turn. A model that
    produced one unusable reply produces another often enough that a second
    attempt is a loop, not a retry."""
    r = tag_recovery.classify_untagged_reply(MANGLED, already_repaired=True)
    assert r.decision == tag_recovery.GIVE_UP
    assert r.reason == tag_recovery.TAG_ONLY
    assert not r.needs_repair


@pytest.mark.parametrize("reply,ok", [
    ("Which doctor would you like?", True),          # option (b): a real question
    ("Sorry, what time suits you?", True),
    ("Please hold on while I book that.", False),    # still stranding
    ("Your appointment is booked.", False),          # still fabricating
    ("[ACTION: None]", False),                       # still nothing to show
    ("", False),
])
def test_which_second_attempts_are_usable(reply, ok):
    assert tag_recovery.resolves_turn(reply) is ok


# ── The terminal reply ────────────────────────────────────────────────────────

def test_the_terminal_reply_exists_in_every_language_and_promises_nothing():
    """It is reached when even the repair failed, so it is the last thing the
    patient will be told — it must resolve the turn in their own language."""
    from backend.agent import spoken_fallback

    assert set(tag_recovery.supported_languages()) == \
        set(spoken_fallback.supported_languages()), (
        "chat and voice disagree about which languages get a terminal reply"
    )
    for lang in tag_recovery.supported_languages():
        reply = tag_recovery.needs_details_reply(lang)
        assert reply.strip()
        assert not tag_recovery.classify_untagged_reply(reply).needs_repair, (
            f"the terminal reply for {lang} itself needs recovery: {reply!r}"
        )


def test_an_unknown_language_still_gets_a_terminal_reply():
    assert tag_recovery.needs_details_reply("xx-YY") == \
        tag_recovery.needs_details_reply("en-IN")
    assert tag_recovery.needs_details_reply(None).strip()
