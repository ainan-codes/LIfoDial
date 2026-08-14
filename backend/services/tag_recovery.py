"""
backend/services/tag_recovery.py

The ONE recovery for a reply that should have carried an ``[ACTION: …]`` tag and
didn't — shared by both agent channels.

Why this exists
---------------
The tag mechanism itself has been shared since services/action_tag.py: one regex,
one definition of "this reply claims the action is done". The *recovery* was not.
Voice (``agent/processors/voice_action.py``) and chat/embed
(``routers/agent_test.py::_handle_booking_action``) each carried their own copy of
the same three-way decision and their own wording of the same re-prompt, and they
had already drifted in ways that cost real calls:

  * chat had no instruction for the tag-only case at all. A reply that was
    ENTIRELY a mangled tag got the "you told the patient it was already done"
    re-prompt — an accusation about something the model had not done, which is
    the least likely prompt to produce the missing tag.
  * voice's instruction carried the fact that a CANCEL needs only a name and a
    phone number (put N/A in the rest); chat's did not, so the same model was
    told two different things about the same tag depending on the channel.
  * chat's terminal "I still can't do this, tell me the details" reply existed in
    English and Hindi only, so a Malayalam patient reaching it got English —
    while the voice path's equivalent has had seven languages since it was
    written.

Every one of those is a fix that was applied to one channel and never ported.
This module is the place where that stops: the decision, the wording, the
acceptance test for the model's second attempt, and the terminal reply all live
here, and both call sites read them from here.

What is deliberately NOT shared
-------------------------------
Turn-state plumbing. Voice is a frame pipeline with streaming responses, an
LLM re-run pushed back through the aggregator, a per-utterance action cap and a
followup-response flag; chat is a single request/response function call. There is
no honest common abstraction over those two, and inventing one would obscure both.
So each channel still decides WHEN to ask (which turn state permits a repair) and
HOW to run the second attempt — but WHAT counts as broken, WHAT the model is
told, and WHAT counts as a usable answer come from here.
"""

from typing import NamedTuple

from backend.services.action_tag import (
    claims_any_completion,
    promises_followup,
    scrub_reply,
)

# ── Why a reply needs recovery ────────────────────────────────────────────────

#: The whole reply was machine tag(s) this system could not parse. Scrubbing it
#: leaves nothing, so the patient gets an empty message and the caller gets
#: silence — the worst outcome on either channel, and the one that has no
#: user-visible symptom to report except "it stopped talking".
TAG_ONLY = "tag_only"

#: The reply asserts the appointment is booked/cancelled/rescheduled. With no tag
#: nothing was written, so it is a fabricated confirmation.
FABRICATED = "fabricated"

#: The reply promises a later message ("hold on while I book that"). Nothing
#: schedules one on either channel, so the user waits forever.
PROMISED = "promised"

# ── What to do about it ───────────────────────────────────────────────────────

#: The reply is honest as it stands: no tag, but it claims nothing and promises
#: nothing. Show/speak it unchanged.
PASS_THROUGH = "pass_through"

#: Re-prompt the model once for the tag it should have emitted.
REPAIR = "repair"

#: This turn has already had its one repair. Stop — but the caller/patient must
#: still be given something that resolves the turn (see needs_details_reply, and
#: agent/spoken_fallback.py on the voice side).
GIVE_UP = "give_up"


class Recovery(NamedTuple):
    """What to do with a reply that carried no parseable ``[ACTION:]`` tag."""

    decision: str
    #: TAG_ONLY / FABRICATED / PROMISED, or "" when nothing is wrong.
    reason: str = ""

    @property
    def needs_repair(self) -> bool:
        return self.decision == REPAIR


def classify_untagged_reply(reply: str, *, already_repaired: bool = False) -> Recovery:
    """The three-way decision, from the reply text alone.

    ``already_repaired`` is the once-per-turn cap: a second unusable reply must
    not start a third LLM call. Both channels enforce exactly one repair per user
    turn, and both must — the model that produced one unusable reply produces
    another often enough that this is a loop, not a retry.

    Judged on the SCRUBBED text throughout, because that is what the user
    actually receives: a claim or a promise buried inside a machine tag is not
    something the user was told, and (on the chat side, which used to test the raw
    reply) treating it as one attributes to the model a sentence it never said.
    """
    speakable = scrub_reply(reply)

    if (reply or "").strip() and not speakable:
        reason = TAG_ONLY
    elif claims_any_completion(speakable):
        reason = FABRICATED
    elif promises_followup(speakable):
        reason = PROMISED
    else:
        return Recovery(PASS_THROUGH)

    return Recovery(GIVE_UP if already_repaired else REPAIR, reason)


def resolves_turn(reply: str) -> bool:
    """True if this reply can be handed to the user as-is after a repair.

    The repair's second attempt is allowed to be a QUESTION — asking for the one
    missing detail is option (b) of the instruction — but it must not still be
    claiming or promising, and it must not be empty. Anything else and the
    channel substitutes its own terminal reply.
    """
    cleaned = scrub_reply(reply)
    if not cleaned:
        return False
    return not promises_followup(cleaned) and not claims_any_completion(cleaned)


def log_summary(reason: str) -> str:
    """A phrase for the ERROR line each channel logs, so the two read alike in
    one grep across both services' logs."""
    return {
        TAG_ONLY: "WAS AN UNPARSEABLE MACHINE TAG AND NOTHING ELSE",
        FABRICATED: "CLAIMED THE ACTION WAS DONE",
        PROMISED: "PROMISED to perform the action",
    }.get(reason, "carried no action tag")


# ── The re-prompt ─────────────────────────────────────────────────────────────

#: Opening line per reason. The rest of the instruction is identical, because
#: what the model has to DO about it is identical — only the account of what went
#: wrong differs, and getting that account right is what makes the re-prompt work
#: (telling a model it "claimed the booking was done" when it actually emitted a
#: mangled tag asks it to correct something it did not do).
_WHY = {
    TAG_ONLY: (
        "Your last reply was a machine tag this system could not read, so NOTHING was saved and "
        "{recipient} {got_nothing}."
    ),
    FABRICATED: (
        "You just told {recipient} an appointment was booked, cancelled or rescheduled, but you did "
        "NOT emit an [ACTION: ...] tag, so NOTHING happened and what you said is not true."
    ),
    PROMISED: (
        "You just told {recipient} you were about to book, cancel or reschedule an appointment, but "
        "you did NOT emit an [ACTION: ...] tag, so NOTHING is happening and nothing will."
    ),
}

#: True on both channels, and the sentence that does the work: models treat
#: "saying it" as the action. There is no queue, no callback and no background
#: worker on either path.
_HOW_IT_WORKS = (
    "Saying it does not do it; only the tag does. There is nothing running in the background and "
    "nothing will happen later."
)

#: The tag's exact shape, and the CANCEL-specific relief that stops the model
#: padding or truncating it. Both channels now state this; only voice used to.
_THE_TAG = (
    "The tag is [ACTION: BOOK|Name|Phone|DD/MM/YYYY|Time|Doctor|Notes] — seven fields separated by "
    "| inside one pair of square brackets. For a CANCEL you need only the name and the phone number; "
    "put N/A in the date, time and doctor fields. If {recipient}'s existing appointments are listed "
    "above, every detail you need is already there — do not ask for it again."
)

#: The ban that this whole module exists to enforce, stated to the model in the
#: same words on both channels.
_NO_PROMISES = (
    "Never say 'hold on', 'one moment', 'please wait', 'I'll confirm shortly', 'I'll start the "
    "process' or 'I'll proceed'. Nothing follows any of those, and {recipient} will wait for a "
    "reply that never comes."
)


def repair_instruction(reason: str, *, spoken: bool) -> str:
    """The authoritative re-prompt body for one strict second attempt.

    Returns the BODY only. Each channel wraps it in its own envelope — voice
    injects it into the live LLM context as a system message prefixed with
    ``[BOOKING_RESULT success=false]``; chat appends it to the system prompt under
    its SYSTEM UPDATE header — because those envelopes are properties of the two
    pipelines, not of the instruction.

    ``spoken`` selects register, and one real behavioural difference: the voice
    path HOLDS a reply that begins with a bracket and speaks nothing until the tag
    resolves, so there a tag must be the whole reply; the chat path renders its
    reply only after the write, so a short neutral sentence around the tag is
    harmless there and keeps its established behaviour.
    """
    recipient = "the caller" if spoken else "the patient"
    got_nothing = "heard nothing at all" if spoken else "saw an empty message"
    placement = (
        "Output the correct [ACTION: ...] tag as the WHOLE of your reply, with nothing else in it."
        if spoken else
        "Output the correct [ACTION: ...] tag NOW, at the end of one short neutral sentence."
    )
    why = _WHY.get(reason, _WHY[FABRICATED])

    parts = [
        why,
        _HOW_IT_WORKS,
        "Fix it NOW in your next reply, choosing EXACTLY ONE of:",
        f"  (a) {placement}",
        f"  (b) If a detail is genuinely missing, ask {recipient} for THAT ONE detail in one short "
        "question, and claim nothing.",
        _THE_TAG,
        _NO_PROMISES,
    ]
    return "\n".join(parts).format(recipient=recipient, got_nothing=got_nothing)


# ── The terminal reply, when even the repair produced nothing usable ──────────

#: Asks for what is missing and claims nothing — the chat/embed analogue of
#: agent/spoken_fallback.py, and now in the same seven languages. It lived in the
#: chat router with English and Hindi only, so every patient in the five other
#: languages this product ships reached this line in English.
#: Each one ENDS on the question. That is not a style choice: a trailing sentence
#: like "बताते ही मैं बुक कर दूँगा।" ("I'll book it as soon as you tell me") reads
#: to action_tag.asserts_completion as a completion claim — the bare "बुक" marker
#: cannot tell "will book" from "has been booked" — and it is a soft promise about
#: a later action besides. Ending on "?" is what makes these unambiguously a
#: request for information, to the guardrails and to the patient alike. Asserted
#: by test_the_terminal_reply_exists_in_every_language_and_promises_nothing.
_NEEDS_DETAILS = {
    "en-IN": "Sorry — which doctor would you like, and on what date and at what time?",
    "hi-IN": "क्षमा करें — कृपया बताइए किस डॉक्टर के साथ, और कौन सी तारीख़ और कौन सा समय चाहिए?",
    "ml-IN": "ക്ഷമിക്കണം — ഏത് ഡോക്ടറെ, ഏത് ദിവസം, എത്ര മണിക്ക് വേണം എന്ന് പറയാമോ?",
    "ta-IN": ("மன்னிக்கவும் — எந்த மருத்துவரை, எந்த நாள், எத்தனை மணிக்கு வேண்டும் எனச் "
              "சொல்ல முடியுமா?"),
    "te-IN": "క్షమించండి — ఏ డాక్టర్‌ని, ఏ రోజు, ఎన్ని గంటలకు కావాలో చెప్పగలరా?",
    "kn-IN": "ಕ್ಷಮಿಸಿ — ಯಾವ ವೈದ್ಯರನ್ನು, ಯಾವ ದಿನ, ಎಷ್ಟು ಗಂಟೆಗೆ ಬೇಕು ಎಂದು ಹೇಳಬಹುದೇ?",
    "mr-IN": "क्षमस्व — कोणत्या डॉक्टरांकडे, कोणत्या दिवशी आणि किती वाजता हवे आहे ते सांगाल का?",
}


def needs_details_reply(language: str | None) -> str:
    """The terminal chat/embed reply. Never empty: an unknown language gets
    English, which is imperfect and still resolves the turn."""
    return _NEEDS_DETAILS.get((language or "").strip()) or _NEEDS_DETAILS["en-IN"]


def supported_languages() -> list[str]:
    """Languages with a real terminal reply, for tests and diagnostics."""
    return sorted(_NEEDS_DETAILS)
