"""
backend/services/action_tag.py

The ONE implementation of the ``[ACTION: …]`` machine tag — the mechanism by
which the LLM tells the system to actually book, reschedule or cancel an
appointment — plus the detectors that decide whether a reply is honest about it.

Shared by BOTH agent channels:

  * chat / embed  — backend/routers/agent_test.py::_handle_booking_action
  * voice         — backend/agent/processors/voice_action.py

It lived, private, inside the chat router until now, and voice therefore had NO
way for the model to signal a booking at all: the voice path's only writer was
the keyword state machine in processors/booking_processor.py, which requires the
CALLER to name the doctor and then say a confirm word in a later turn. A real
call does neither — the AGENT proposes the doctor ("for chest pain I'd suggest
Dr Salman"), and the caller answers "tomorrow 2 PM, I'll come". Measured against
production on 2026-08-12: of every appointment row ever written, none had a
call_id — i.e. not one voice call in the product's lifetime had booked anything,
while the model told each caller "आपकी अपॉइंटमेंट बुक कर दी गई है".

Two rules make the duplication that caused that impossible to reintroduce:

  1. ONE regex. A gap in tag parsing has two consequences at once — the write is
     silently skipped AND the raw tag leaks to the user — so a second, slightly
     different copy is a defect waiting to happen (see the whitespace history on
     ACTION_RE below).
  2. ONE definition of "this reply claims the action is done". Both channels
     need it to catch a fabricated confirmation, and they must agree on what
     counts.
"""

import logging
import re
from typing import NamedTuple, Optional

# ── The tag itself ────────────────────────────────────────────────────────────

#: Matches the ``[ACTION: BOOK|Name|Phone|Date|Time|Doctor|Notes]`` tag the LLM
#: emits.
#:
#: Whitespace is tolerated everywhere the model actually puts it. Production
#: 2026-08-11 (Indiana Hospital): the model emitted
#:   "[ ACTION: RESCHEDULE|Ainan|9090909090|11/08/2026|03:00 PM|Rajesh|N/A ]"
#: — a space after the opening bracket. The old pattern required "[ACTION"
#: adjacently, so it did not match, NOTHING was executed, and (because the
#: loose scrubber below had the same gap) the raw tag was shown to the patient
#: verbatim. Both are whitespace- and case-tolerant; the fields themselves are
#: .strip()ed by parse_action_tag.
ACTION_RE = re.compile(
    r'\[\s*ACTION\s*:\s*(BOOK|RESCHEDULE|CANCEL)\s*\|(.*?)\|(.*?)\|(.*?)\|(.*?)\|(.*?)(?:\|(.*?))?\]',
    re.IGNORECASE,
)

#: ANY ``[ACTION: …]``-shaped fragment, including malformed ones ACTION_RE
#: cannot parse. Observed in production 2026-08-10: the model emitted a bare
#: "[ACTION: None]", which the strict regex ignored and which therefore leaked
#: into the patient's chat verbatim. Every user-facing reply is scrubbed with
#: this, not just the ones where a valid tag was found.
LOOSE_ACTION_RE = re.compile(r'\[\s*ACTION\b[^\]]*\]', re.IGNORECASE)

# ── One grammar per intent ────────────────────────────────────────────────────
#
# The three actions do not need the same information, and pretending they do is
# what produced the tag-only hang. A CANCEL is found by name + phone alone; the
# instructions said so in prose ("Name and Phone, and NOTHING else") while showing
# a template with four "N/A" fields nailed on. Asked to satisfy both, models drop
# the padding — and a short tag was unparseable, so nothing was written, nothing
# could be spoken, and the caller heard silence.
#
# These are the shapes the model is now ASKED for (voice: booking_rules.py, chat:
# routers/agent_test.py both render them from here), so padding is no longer part
# of the primary path.

#: Action -> the fields that action actually needs, in tag order.
TAG_GRAMMAR: dict[str, tuple[str, ...]] = {
    # Everything: this one creates a row out of nothing.
    "BOOK": ("Name", "Phone", "Date", "Time", "Doctor", "Notes"),
    # The existing row is found by name + phone (his.py::sync_appointment_to_db).
    # Nothing else is consulted, so nothing else is asked for — which also stops
    # the agent interrogating a caller about a date it does not need, as it did
    # for 280 seconds on a live call.
    "CANCEL": ("Name", "Phone"),
    # Found the same way, then moved — so it needs the NEW day and time, and
    # nothing about the old ones. The doctor only changes if the caller says so.
    "RESCHEDULE": ("Name", "Phone", "NewDate", "NewTime"),
}

#: The full six-field form every action used to share. Still accepted from every
#: intent, for ever: it is what older prompts taught, what the chat path has
#: always emitted, and what a model that has seen one BOOK example will copy.
CANONICAL_FIELD_COUNT = 6

#: What shape a tag actually arrived in — see classify_tag_shape.
SHAPE_GRAMMAR = "grammar"      # exactly the fields its intent asks for
SHAPE_CANONICAL = "canonical"  # the universal six-field form
SHAPE_PADDED = "padded"        # neither; accepted defensively, trailing fields assumed


def tag_grammar(action: str) -> tuple[str, ...]:
    """The fields ``action`` is asked for. BOOK's, for an unknown verb."""
    return TAG_GRAMMAR.get((action or "").strip().upper(), TAG_GRAMMAR["BOOK"])


def tag_template(action: str) -> str:
    """The tag shape to SHOW the model for one intent."""
    action = (action or "").strip().upper()
    return f"[ACTION: {action}|" + "|".join(tag_grammar(action)) + "]"


def tag_templates() -> str:
    """All three shapes, one per line, for a system prompt. Rendered rather than
    typed out, so the prompt and the parser can never disagree about what a
    CANCEL looks like — which is precisely how they came to disagree."""
    return "\n".join(f"  {tag_template(a)}" for a in ("BOOK", "RESCHEDULE", "CANCEL"))


def _tag_fields(text: str) -> Optional[tuple[str, list[str]]]:
    """(ACTION, [fields]) for the first complete, correctly-bracketed tag."""
    m = SHORT_ACTION_RE.search(text or "")
    if not m:
        return None
    return (m.group(1) or "").strip().upper(), [
        f.strip() for f in (m.group(2) or "").split("|")
    ]


def classify_tag_shape(text: str) -> Optional[str]:
    """Which shape the model actually emitted, or None if there is no tag.

    Exists to make "the fallback is doing routine work" observable instead of
    invisible: SHAPE_PADDED in the logs means the model is emitting a shape
    nobody asked it for, and the prompt — not the parser — is what needs fixing.
    """
    parsed = _tag_fields(text)
    if parsed is None:
        return None
    action, fields = parsed
    if len(fields) == len(tag_grammar(action)):
        return SHAPE_GRAMMAR
    if len(fields) == CANONICAL_FIELD_COUNT:
        return SHAPE_CANONICAL
    return SHAPE_PADDED


#: A complete, correctly-bracketed tag with a valid verb whose FIELD COUNT is
#: wrong — fewer than the six ``|`` fields ACTION_RE requires. Parsed by
#: _parse_short_action_tag below, and only ever consulted after ACTION_RE has
#: already failed.
SHORT_ACTION_RE = re.compile(
    r'\[\s*ACTION\s*:\s*(BOOK|RESCHEDULE|CANCEL)\s*\|([^\]]*)\]',
    re.IGNORECASE,
)

#: A machine tag whose closing bracket never arrived — the model ran into the
#: max_tokens cap mid-tag, or stopped early. There is no valid user-facing text
#: that starts "[ACTION"/"[BOOKING_RESULT" and never closes, so the whole tail
#: is dropped rather than shown/spoken.
TRUNCATED_TAG_RE = re.compile(
    r'\[\s*(?:ACTION|BOOKING_RESULT|AVAILABILITY_NOTE)\b[^\]]*$',
    re.IGNORECASE,
)

def has_open_action_tag(text: str) -> bool:
    """True if `text` ends part-way through something that may still become an
    ``[ACTION: …]`` tag, so a caller reading streamed text should wait for more.

    The voice path needs this and the two complete-tag regexes above cannot
    answer it: the LLM streams a reply in small chunks, so a tag routinely
    arrives split ("…sure. [ACT" + "ION: BOOK|…]"). Ordinary bracketed prose is
    never held — only a bracket whose contents so far are consistent with the
    word "action". Same test processors/tag_scrub.py makes for its own narrower
    purpose (never SPEAK a tag); kept here as well because this module is what
    the voice executor imports.
    """
    idx = (text or "").rfind("[")
    if idx == -1:
        return False
    tail = text[idx:]
    if "]" in tail:
        return False
    body = tail[1:].lstrip().lower()
    return body.startswith("action") or "action".startswith(body[:6])


class ActionTag(NamedTuple):
    """The seven fields of a parsed tag, stripped. ``notes`` defaults to "N/A"
    because the tag's last field is optional (models drop it)."""

    action: str
    name: str
    phone: str
    date: str
    time: str
    doctor: str
    notes: str


#: The tag's fields after the verb, in order. Named here because
#: _parse_short_action_tag pads a short tag out to this length.
_TAG_FIELDS = ("name", "phone", "date", "time", "doctor", "notes")


#: Where each intent's grammar fields land in the seven-field ActionTag. BOOK and
#: CANCEL are prefixes of it; RESCHEDULE's NewDate/NewTime are the date/time
#: slots, so its four fields are a prefix too. Written out rather than assumed,
#: because a grammar whose fields did NOT map to a prefix would otherwise be
#: silently mis-filled.
_GRAMMAR_IS_PREFIX = {"BOOK", "CANCEL", "RESCHEDULE"}


def _parse_short_action_tag(text: str) -> Optional[ActionTag]:
    """A tag with fewer than six fields — its intent's own grammar, or a short
    shape nobody asked for — padded out to the full seven. None if unusable.

    Two different jobs, and the difference matters:

    * SHAPE_GRAMMAR — the shape the prompts now ASK for. A CANCEL carrying just a
      name and a phone number is complete, not truncated, and this is its primary
      parse. See TAG_GRAMMAR.
    * SHAPE_PADDED — anything else. ACTION_RE demands six fields, so a tag that is
      correct in every other way used to be not merely mis-parsed but INVISIBLE:
      nothing written, and on voice the whole reply scrubs to nothing, so the
      caller hears silence and the only recovery is a re-prompt that costs an
      extra LLM call and may fail again. That was the reported Hindi CANCEL hang.
      Accepting the shape beats re-prompting for it, so this stays as a defensive
      net — but it is logged, because a model emitting shapes nobody asked for is
      a prompt problem that this function would otherwise hide.

    Padding fills TRAILING fields, which is sound because every intent's grammar
    is a prefix of the seven-field tag (_GRAMMAR_IS_PREFIX). Being wrong about it
    cannot create a bad row in any case: every gate still runs downstream, so a
    BOOK or RESCHEDULE that lands here without a real Time is refused with
    ``invalid_time`` and the caller is asked, exactly as if the field had been
    blank. Name and phone are the two the whole tag is useless without — an
    appointment carrying neither can never be found again — so a tag with fewer
    than two fields is still rejected outright.
    """
    parsed = _tag_fields(text)
    if parsed is None:
        return None
    action, fields = parsed
    if len(fields) < 2:
        return None

    if action not in _GRAMMAR_IS_PREFIX:
        # Unreachable via SHORT_ACTION_RE's verb list; here so that adding a verb
        # whose fields are NOT a prefix fails loudly instead of mis-filling.
        return None

    shape = SHAPE_GRAMMAR if len(fields) == len(tag_grammar(action)) else SHAPE_PADDED
    if shape is SHAPE_PADDED:
        logging.getLogger(__name__).warning(
            "action_tag: a %s tag arrived with %d field(s); its grammar asks for %d "
            "(%s). Accepting it defensively and padding the rest — but the model is "
            "emitting a shape nothing asked it for, which is a prompt problem.",
            action, len(fields), len(tag_grammar(action)), tag_template(action),
        )

    fields += ["N/A"] * (len(_TAG_FIELDS) - len(fields))
    return ActionTag(action, *fields)


def parse_action_tag(text: str) -> Optional[ActionTag]:
    """The first well-formed tag in `text`, or None.

    ``action`` is upper-cased; every other field is stripped exactly as the
    model wrote it (validation is the caller's job — see gate_action_tag).
    """
    m = ACTION_RE.search(text or "")
    if not m:
        return _parse_short_action_tag(text)
    g = m.groups()
    return ActionTag(
        action=(g[0] or "").strip().upper(),
        name=(g[1] or "").strip(),
        phone=(g[2] or "").strip(),
        date=(g[3] or "").strip(),
        time=(g[4] or "").strip(),
        doctor=(g[5] or "").strip(),
        notes=(g[6].strip() if (len(g) > 6 and g[6] is not None) else "N/A"),
    )


# ── Scrubbing: never show/speak a machine tag ─────────────────────────────────

def scrub_reply(text: str) -> str:
    """Strip machine tags (valid, malformed, or truncated) out of anything shown
    to a user. Every user-facing string on the chat path passes through here;
    the voice path's equivalent last line of defence is
    processors/tag_scrub.py, which additionally has to cope with tags split
    across streamed frames."""
    text = LOOSE_ACTION_RE.sub("", text or "")
    text = re.sub(r'\[\s*BOOKING_RESULT[^\]]*\]', "", text, flags=re.IGNORECASE)
    text = re.sub(r'\[\s*AVAILABILITY_NOTE[^\]]*\]', "", text, flags=re.IGNORECASE)
    text = TRUNCATED_TAG_RE.sub("", text)
    return re.sub(r'[ \t]{2,}', ' ', text).strip()


def is_only_a_tag(text: str) -> bool:
    """True if the model's whole reply was machine tag(s) and nothing else.

    Scrubbing such a reply yields "" — and an empty chat bubble (or silence, on
    voice) tells the user nothing at all, which is the same dead end as leaking
    the tag. The 2026-08-11 production reply was exactly this: a bare
    "[ ACTION: RESCHEDULE|…]" with no prose around it.
    """
    return bool((text or "").strip()) and not scrub_reply(text)


# ── "Does this reply strand the user?" ────────────────────────────────────────

#: Phrases that promise a LATER message ("hold on", "I'll confirm shortly").
#:
#: The chat path is strictly request/response: one user message in, one reply
#: out. There is no queue, no callback, no polling — nothing that can ever
#: deliver a promised follow-up, so a reply ending on one of these strands the
#: patient in a permanent "waiting to be confirmed" state. That is the exact
#: 2026-08-10 production bug: the agent said "please hold on for a moment while
#: I complete your booking" and then went silent forever — in one case even
#: though the appointment row HAD been written successfully.
#:
#: Voice is not request/response (the agent CAN speak again), but the same
#: phrases are still a defect there: nothing schedules that later utterance
#: either, so the caller is left listening to silence.
FOLLOWUP_PROMISE_PATTERNS = (
    "hold on", "please hold", "one moment", "a moment", "a minute",
    "bear with me", "please wait", "kindly wait", "shortly", "momentarily",
    "i'll confirm", "i will confirm", "let me confirm", "let me check",
    "i'm checking", "i am checking", "checking availability", "checking the availability",
    "sent the request", "processing your", "working on it", "get back to you",
    # "The action is underway" — the same stranding without the word "wait".
    # Measured live 2026-08-11 on a reschedule: asked "is it done?", the model
    # replied "Yes, proceeding with the change." and emitted no tag, so nothing
    # happened and the patient was told it was in progress forever. Note these
    # must NOT match "please reply yes to proceed", which is a legitimate
    # question — hence "proceeding", not "proceed".
    "proceeding", "going ahead", "i'll go ahead", "i will go ahead",
    "doing that now", "i'll do that now", "i will do that now",
    "making that change", "making the change", "updating your appointment",
    "changing your appointment", "in progress", "being processed",
    "इंतज़ार", "इंतजार", "प्रतीक्षा", "थोड़ा रुक", "एक मिनट",
    # Measured on a live Hindi CANCEL call, 2026-08-12: the agent said
    # "मैं इस अपॉइंटमेंट को कैंसिल करने की प्रक्रिया शुरू करूंगा" and then
    # "अब मैं इसे कैंसिल करने के लिए आगे बढ़ूंगा", emitted no tag, and cancelled
    # nothing in 280 seconds while the caller asked "हो गया क्या?" four times.
    # The English list above could not see any of it.
    # Deliberately NOT the broader "कर दूंगा" / "करने वाला हूँ" / "देख रहा हूँ":
    # each of those appears in perfectly good replies ("बताते ही मैं बुक कर दूँगा"
    # is a QUESTION turn, not a promise), and a false positive here throws away a
    # good reply on chat and spends an extra LLM call on voice.
    "प्रक्रिया शुरू", "आगे बढ़ूंगा", "आगे बढ़ता हूँ", "आगे बढ़ रहा",
    "कोशिश करूंगा", "कोशिश कर रहा", "शुरू कर रहा",
    "जाँच रहा", "जांच रहा", "चेक कर", "प्रोसेस", "थोड़ी देर",
    # The other six languages this product ships. Their absence was not a
    # judgement call, it was a gap: until 2026-08-14 this list was English plus
    # Hindi/Marathi Devanagari only, so a Malayalam or Tamil agent could tell a
    # caller to wait for a booking it never made and NOTHING here could see it —
    # on voice that is a caller listening to silence, on chat a patient who never
    # gets the promised message. Found by running this very detector over the
    # product's OWN spoken fallback phrases (resilience.py::_FALLBACK_PHRASES),
    # which promise a wait in all eight languages and tripped it in exactly one.
    #
    # Deliberately only the WAIT IMPERATIVE and the "one moment" idiom in each
    # language — the forms that can only mean "expect something later". The
    # broader "I am checking / I will try" shapes are left out for the same reason
    # they are left out of Hindi above: they appear in perfectly good replies, and
    # a false positive throws one away.
    "காத்திரு", "ஒரு நிமிடம்",                    # Tamil
    "ఆగండి", "వేచి", "ఒక్క క్షణం", "ఒక క్షణం",      # Telugu
    "ಕಾಯಿ", "ಒಂದು ಕ್ಷಣ", "ಸ್ವಲ್ಪ ಸಮಯ",              # Kannada
    "കാത്തിരി", "ഒരു നിമിഷം", "കുറച്ചു സമയം",       # Malayalam
    "অপেক্ষা", "একটু সময়",                        # Bengali
    "थांबा", "एक क्षण",                            # Marathi
    # Romanized Hindi. Sarvam STT returns Latin script for some models, and the
    # product's own constants have been written this way, so the Devanagari
    # entries above cannot be assumed to cover Hindi.
    "rukiye", "rukiyega", "thodi der", "ek pal", "ek minute", "intezaar",
)


def promises_followup(text: str) -> bool:
    """True if the reply tells the user to wait for something that will never
    arrive. False positives are safe here: the caller's response is to
    substitute a deterministic, outcome-accurate reply, which is correct
    either way."""
    low = (text or "").lower()
    return any(p in low for p in FOLLOWUP_PROMISE_PATTERNS)


# ── "Does this reply CLAIM the action is done?" ────────────────────────────────

#: Words that make a reply an actual ASSERTION that the action completed. A
#: reply may mention "confirm" and still not confirm anything ("To confirm, you
#: are Ramesh Kumar… is that right?"), so a trailing question mark disqualifies
#: it regardless — see asserts_completion.
#:
#: The native-script entries are what make this work on a real Indian-language
#: call: the fabricated confirmations measured in production were Hindi
#: ("बुक कर दी गई है"), and an English-only marker list scores those as "no
#: claim was made" — exactly the blind spot that let them through.
COMPLETION_MARKERS = {
    "BOOK": (
        "confirmed", "booked", "scheduled", "reserved", "is set", "all set",
        # Hindi / Marathi. Deliberately NOT the bare "हो गया है" ("it's done"):
        # it is generic enough to appear in replies about anything at all, and a
        # false positive here costs a needless repair turn.
        "कन्फर्म", "बुक", "निश्चित", "तय हो", "कर दी गई", "कर दिया गया", "नोंदणी",
        # Bengali, Gujarati, Punjabi, Odia
        "বুক", "নিশ্চিত", "બુક", "કન્ફર્મ", "ਬੁੱਕ", "ਪੱਕੀ", "ବୁକ୍",
        # Tamil, Telugu, Kannada, Malayalam
        "பதிவு", "உறுதி", "బుక్", "ఖరారు", "ಬುಕ್", "ಖಚಿತ",
        "ബുക്ക്", "ബുക്ക", "സ്ഥിരീകരി", "ഉറപ്പിച്ച",
    ),
    "RESCHEDULE": (
        "rescheduled", "moved", "changed", "updated", "new time",
        "रीशेड्यूल", "बदल", "स्थानांतरित",
        "পরিবর্তন", "બદલ", "ਬਦਲ", "ବଦଳ",
        "மாற்ற", "మార్చ", "ಬದಲಾ", "മാറ്റി",
    ),
    "CANCEL": (
        "cancelled", "canceled", "called off",
        "रद्द", "कैंसल",
        "বাতিল", "રદ", "ਰੱਦ", "ବାତିଲ",
        "ரத்து", "రద్దు", "ರದ್ದು", "റദ്ദാക്കി", "ക്യാൻസൽ",
    ),
}


def asserts_completion(text: str, action: str) -> bool:
    """True if this reply actually TELLS the user the action is done.

    Guards the success path: the write really happened, so a reply that merely
    asks another question ("…is that correct?") leaves the user believing
    nothing has been booked — the same stranding as a "please wait", just
    phrased differently. Observed live 2026-08-10.
    """
    t = (text or "").strip()
    if not t or t.endswith("?"):
        return False
    low = t.lower()
    return any(k in low for k in COMPLETION_MARKERS.get((action or "").upper(), ()))


def claims_any_completion(text: str) -> bool:
    """True if the reply claims ANY appointment action is done.

    Used where no ``[ACTION:]`` tag was emitted — in which case nothing was
    written, so such a claim is a fabricated confirmation. Measured 2026-08-10
    on chat: llama-3.1-8b-instant produced exactly this ("Your appointment with
    Dr. Rajesh is booked", no tag) in 2 of 3 runs, and llama-3.3-70b does it
    occasionally too. Measured 2026-08-12 on voice: it happened on 2 of 2 calls.
    """
    return any(asserts_completion(text, action) for action in COMPLETION_MARKERS)


# ── Field validation, shared by both channels ─────────────────────────────────

#: Values a model writes when it does not actually have the detail. The tag
#: instructions themselves say to use 'N/A' for fields that don't apply, so the
#: model readily fills these in for BOOK fields it hasn't collected yet.
PLACEHOLDER_VALUES = frozenset({
    "", "n/a", "na", "n.a.", "none", "null", "-", "--", "unknown", "not provided",
    "not given", "patient", "customer", "caller", "tbd", "xxx",
})


def is_placeholder(value: str) -> bool:
    return (value or "").strip().lower() in PLACEHOLDER_VALUES


def missing_identity_fields(tag: ActionTag) -> list[str]:
    """Which of (name, phone number) the model has not actually collected.

    A BOOK with a placeholder Name/Phone creates a row nobody can be identified
    from, and which the user can NEVER cancel or reschedule, because that lookup
    matches on name AND phone (his.py::sync_appointment_to_db). Observed live
    2026-08-10: the model emitted a BOOK tag with "N/A" for both before it had
    asked for them, and a real appointment was written for "N/A".

    CANCEL/RESCHEDULE need the same two fields for the opposite reason: the
    existing appointment is FOUND by name + phone, so a placeholder there can
    only ever produce "no appointment found" — which reads to the user as "your
    appointment doesn't exist" when the truth is "you were never asked who you
    are".
    """
    if tag.action not in ("BOOK", "RESCHEDULE", "CANCEL"):
        return []
    return [
        label for label, val in (("name", tag.name), ("phone number", tag.phone))
        if is_placeholder(val)
    ]


def needs_real_time(action: str) -> bool:
    """BOOK/RESCHEDULE need a REAL user-given time; CANCEL needs none (it is
    matched by name + phone).

    The model's tag can carry an empty/"N/A" Time field (observed live
    2026-08-10: the tag had a correct Date but a blank Time), and
    his.parse_slot_datetime's fallback-on-unparseable behavior would silently
    book midnight instead of refusing — exactly the kind of fabricated slot the
    voice path has always banned by construction.
    """
    return (action or "").upper() in ("BOOK", "RESCHEDULE")
