"""A voice booking must be completable in the language the agent speaks.

The voice booking FSM matched caller speech against lowercase ASCII keyword
sets — doctor names, "yes", "my name is", r"\\d+ ?(am|pm|baje)". Sarvam's STT
returns an Indic call's words in the caller's own script, so on a Hindi call
(the configuration that shipped to production) EVERY step of the flow missed:

    "सलमान"      never matched the roster row "Salman"    -> no doctor
    "हाँ"        never matched "haan"                     -> no confirmation
    "मेरा नाम"   never matched "mera naam"                -> no patient name
    "ग्यारह बजे" never matched r"\\d+ ?baje"               -> no slot

Four independent hard stops, so no Indian-language voice call could book
anything at all — while the same clinic's English/romanised calls could.

Matching now runs on script-independent consonant skeletons
(services/indic_text.py) through ONE shared matcher
(services/doctor_match.py), used by both the voice FSM and the chat channel's
[ACTION:] tag resolution.

Run: python -m pytest backend/tests/test_indic_booking_language.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest

from backend.agent.processors.booking_processor import (
    _CANCEL_WORDS,
    _CONFIRM_WORDS,
    _DAY_PATTERN,
    _day_word_to_english,
    _SLOT_PATTERN,
    _said,
)
from backend.services.doctor_match import match_doctor, match_doctor_name
from backend.services.indic_text import (
    consonant_skeleton,
    normalise_spoken_numbers,
)

# The live roster at Indiana Hospital Mangalore on 2026-08-11.
DOCTORS = [
    {"id": "d1", "name": "Rajesh", "specialization": "General Physician", "is_available": True},
    {"id": "d2", "name": "Salman", "specialization": "Cardiologist", "is_available": True},
    {"id": "d3", "name": "Nazima khan", "specialization": "Paediatrician", "is_available": True},
]


# ── Doctor names, every script the product serves ─────────────────────────────

@pytest.mark.parametrize("language,utterance,expected", [
    ("Hindi",     "मुझे सलमान से अपॉइंटमेंट चाहिए",      "Salman"),
    ("Hindi",     "नज़ीमा खान को दिखाना है",              "Nazima khan"),
    ("Marathi",   "मला सलमान हवे आहेत",                   "Salman"),
    ("Malayalam", "എനിക്ക് സൽമാൻ ഡോക്ടറെ കാണണം",           "Salman"),
    ("Malayalam", "നസീമ ഖാൻ ഡോക്ടർ",                      "Nazima khan"),
    ("Kannada",   "ನನಗೆ ರಾಜೇಶ್ ಬೇಕು",                     "Rajesh"),
    ("Tamil",     "எனக்கு ராஜேஷ் வேண்டும்",               "Rajesh"),
    ("Telugu",    "నాకు సల్మాన్ కావాలి",                  "Salman"),
    ("Bengali",   "আমি রাজেশ ডাক্তারকে চাই",              "Rajesh"),
    ("Punjabi",   "ਮੈਨੂੰ ਸਲਮਾਨ ਚਾਹੀਦਾ ਹੈ",                "Salman"),
    ("Gujarati",  "મને સલમાન જોઈએ",                       "Salman"),
    ("Odia",      "ମୋତେ ସଲମାନ ଦରକାର",                     "Salman"),
    ("English",   "I'd like to see Dr Salman",            "Salman"),
])
def test_doctor_matched_by_name_in_any_script(language, utterance, expected):
    doc, how = match_doctor(utterance, DOCTORS)
    assert doc is not None, f"{language}: no doctor matched {utterance!r}"
    assert doc["name"] == expected, f"{language}: matched {doc['name']} not {expected}"
    assert how == "name"


@pytest.mark.parametrize("language,utterance,expected", [
    ("Hindi",     "मुझे कार्डियोलॉजिस्ट से मिलना है", "Salman"),
    ("Hindi",     "कार्डियोलॉजी डिपार्टमेंट में",     "Salman"),
    ("Malayalam", "കാർഡിയോളജിസ്റ്റ് വേണം",             "Salman"),
    ("Kannada",   "ಪೀಡಿಯಾಟ್ರಿಶಿಯನ್ ಬೇಕು",              "Nazima khan"),
    ("English",   "I need a cardiologist",             "Salman"),
    ("English",   "do you have a cardiology department", "Salman"),
])
def test_doctor_matched_by_speciality_across_scripts(language, utterance, expected):
    """An English speciality word spoken inside an Indian-language sentence comes
    back transcribed phonetically ("कार्डियोलॉजी"), which shares no substring
    with "Cardiologist" until the loanword fold is applied."""
    doc, how = match_doctor(utterance, DOCTORS)
    assert doc is not None, f"{language}: no doctor matched {utterance!r}"
    assert doc["name"] == expected
    assert how == "specialization"


@pytest.mark.parametrize("utterance", [
    "मुझे बुखार है",                     # "I have a fever"
    "सीने में दर्द हो रहा है",            # "I'm having chest pain"
    "क्या आप हिंदी में बात कर सकते हैं",   # "can you speak Hindi?"
    "मेरा नाम अमित है",                  # "my name is Amit"
    "जनरल फिजिशियन से मिलना है",          # a speciality, but not a NAME
    "എനിക്ക് പനി ഉണ്ട്",
    "I have a headache",
    "what are your working hours",
    "my appointment is confirmed thank you",
])
def test_no_doctor_invented_from_unrelated_speech(utterance):
    """Matching the wrong doctor is worse than matching none — a booking would
    go to the wrong person. Notably "जनरल फिजिशियन" once matched the NAME
    "Nazima" because the sibilant fold was allowed to span two words."""
    doc, how = match_doctor(utterance, DOCTORS)
    assert how != "name", f"{utterance!r} was matched to a doctor NAME: {doc}"


def test_on_leave_doctor_is_reported_but_not_armed():
    roster = [
        {"id": "x", "name": "Salman", "specialization": "Cardiologist",
         "is_available": False, "leave_reason": "on leave"},
    ]
    doc, how = match_doctor("मुझे सलमान से मिलना है", roster)
    assert doc is not None and how == "name_unavailable", (
        "An on-leave doctor must still be RECOGNISED (so the agent can say "
        "they are on leave) while never arming a booking."
    )


def test_available_doctor_wins_over_an_on_leave_one_with_the_same_speciality():
    roster = [
        {"id": "a", "name": "Asha", "specialization": "Cardiologist", "is_available": False},
        {"id": "b", "name": "Bala", "specialization": "Cardiologist", "is_available": True},
    ]
    doc, how = match_doctor("I need a cardiologist", roster)
    assert doc["name"] == "Bala" and how == "specialization"


# ── The chat channel's [ACTION:] tag resolves through the same matcher ─────────

@pytest.mark.parametrize("tag_value,expected", [
    ("Salman", "Salman"),
    ("Dr. Salman", "Salman"),
    ("सलमान", "Salman"),          # the LLM wrote the name in Devanagari
    ("സൽമാൻ", "Salman"),
    ("ರಾಜೇಶ್", "Rajesh"),
    ("Cardiologist", "Salman"),
    ("Someone Else Entirely", None),
])
def test_action_tag_doctor_name_resolves_in_any_script(tag_value, expected):
    doc = match_doctor_name(tag_value, DOCTORS)
    assert (doc["name"] if doc else None) == expected


# ── Confirmation, refusal, and the words that end a booking ───────────────────

@pytest.mark.parametrize("language,utterance", [
    ("Hindi",     "हाँ"),
    ("Hindi",     "हाँ ठीक है"),
    ("Hindi",     "बिल्कुल, बुक कर दीजिए"),
    ("Marathi",   "हो बरोबर"),
    ("Malayalam", "അതെ"),
    ("Malayalam", "ശരി"),
    ("Kannada",   "ಹೌದು"),
    ("Tamil",     "சரி"),
    ("Telugu",    "అవును"),
    ("Bengali",   "হ্যাঁ"),
    ("Gujarati",  "હા"),
    ("Punjabi",   "ਹਾਂ"),
    ("Odia",      "ହଁ"),
    ("English",   "yes please"),
    ("Romanised", "haan theek hai"),
])
def test_confirmation_heard_in_any_language(language, utterance):
    assert _said(utterance, _CONFIRM_WORDS), f"{language}: {utterance!r} not heard as a yes"


@pytest.mark.parametrize("utterance", [
    "नहीं", "ना करो", "ഇല്ല", "ಇಲ್ಲ", "இல்லை", "না", "no thanks", "nahi",
])
def test_refusal_heard_in_any_language(utterance):
    assert _said(utterance, _CANCEL_WORDS)


@pytest.mark.parametrize("utterance", [
    "हाँ",           # a yes must not read as a no
    "अते",
    "yes go ahead",
])
def test_a_yes_is_not_also_heard_as_a_no(utterance):
    assert not _said(utterance, _CANCEL_WORDS), f"{utterance!r} read as a refusal"


def test_short_romanised_words_are_not_matched_inside_longer_ones():
    """"ha" is in _CONFIRM_WORDS and is a substring of "what happened" — a bare
    substring test would read an unrelated sentence as the caller confirming a
    booking."""
    assert not _said("what happened to my appointment", _CONFIRM_WORDS)
    assert not _said("I have a headache", _CONFIRM_WORDS)


# ── Spoken times ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("language,utterance,expect_hour", [
    ("Hindi",      "ग्यारह बजे",             "11"),
    ("Hindi",      "कल तीन बजे",             "3"),
    ("Hindi",      "११ बजे",                 "11"),     # native digit shapes
    ("Marathi",    "अकरा वाजता",             "11"),
    ("Malayalam",  "പതിനൊന്ന് മണി",           "11"),
    ("Kannada",    "ಹನ್ನೊಂದು ಗಂಟೆಗೆ",         "11"),
    ("Tamil",      "பதினொன்று மணி",          "11"),
    ("Telugu",     "పదకొండు గంటలకు",         "11"),
    ("Bengali",    "এগারো টা",               "11"),
    ("English",    "eleven o'clock",         "11"),
])
def test_spoken_clock_time_becomes_a_matchable_digit(language, utterance, expect_hour):
    normalised = normalise_spoken_numbers(utterance)
    match = _SLOT_PATTERN.search(normalised)
    assert match, f"{language}: no time found in {normalised!r} (from {utterance!r})"
    assert expect_hour in match.group(0), (
        f"{language}: found {match.group(0)!r}, expected the hour {expect_hour}"
    )


def test_an_already_numeric_time_is_left_alone():
    assert normalise_spoken_numbers("3:30 pm") == "3:30 pm"


@pytest.mark.parametrize("utterance,expected_day", [
    ("कल ग्यारह बजे", "Tomorrow"),
    ("आज शाम", "Today"),
    ("നാളെ", "Tomorrow"),
    ("ನಾಳೆ", "Tomorrow"),
    ("நாளை", "Tomorrow"),
    ("আগামীকাল", "Tomorrow"),
    ("tomorrow at 3", "Tomorrow"),
])
def test_native_day_words_translate_to_what_the_parser_understands(utterance, expected_day):
    """A Devanagari day word captured verbatim would not be understood by
    his.parse_slot_datetime, which silently falls back to today — so a caller
    asking for tomorrow got today."""
    match = _DAY_PATTERN.search(utterance)
    assert match, f"no day word found in {utterance!r}"
    raw = match.group(0).strip()
    assert _day_word_to_english(raw) == expected_day


# ── The skeleton itself ───────────────────────────────────────────────────────

@pytest.mark.parametrize("latin,indic", [
    ("Salman", "सलमान"),
    ("Salman", "സൽമാൻ"),      # Malayalam chillu letters, outside the normal range
    ("Salman", "సల్మాన్"),
    ("Rajesh", "राजेश"),
    ("Rajesh", "ರಾಜೇಶ್"),
    ("Rajesh", "ராஜேஷ்"),      # Tamil has its own consonant layout
])
def test_the_same_name_in_two_scripts_has_the_same_skeleton(latin, indic):
    assert consonant_skeleton(latin) == consonant_skeleton(indic)


def test_distinct_names_keep_distinct_skeletons():
    names = ["Salman", "Rajesh", "Nazima", "Priya", "Anil", "Menon", "Nair"]
    skeletons = [consonant_skeleton(n) for n in names]
    assert len(set(skeletons)) == len(skeletons), (
        f"the skeleton is too lossy to tell these names apart: "
        f"{dict(zip(names, skeletons))}"
    )


# ── The requested time must survive all the way into the DB row ───────────────

def test_baje_is_parseable_as_a_time():
    """booking_processor's slot regex has always ACCEPTED "11 baje" and
    his._try_parse_time has always REJECTED it, so the extracted slot was thrown
    away and parse_slot_datetime fell back to "now" — which the availability
    gate then refused. The two halves now agree."""
    from backend.services.his import is_time_str_parseable

    for spoken in ("11 baje", "11 bajey", "11 o'clock", "11", "9:30 baje"):
        assert is_time_str_parseable(spoken), f"{spoken!r} still unparseable"


def test_a_bare_hour_is_read_as_clinic_hours():
    """A bare hour carries no AM/PM. Clinics here run 9 AM - 7 PM, so 1-7 is the
    afternoon and 8-12 the morning."""
    from backend.services.his import _try_parse_time

    assert _try_parse_time("11 baje").hour == 11      # 11 AM
    assert _try_parse_time("3 baje").hour == 15       # 3 PM
    assert _try_parse_time("9 baje").hour == 9        # 9 AM
    assert _try_parse_time("7 baje").hour == 19       # 7 PM
    # An explicit marker still wins over the heuristic.
    assert _try_parse_time("11 PM").hour == 23
    assert _try_parse_time("14:30").hour == 14


def test_nonsense_is_still_refused():
    from backend.services.his import _try_parse_time

    for junk in ("", "N/A", "sometime", "99 baje", "tomorrow"):
        assert _try_parse_time(junk) is None, f"{junk!r} was accepted as a time"


def test_the_commit_receives_the_day_and_time_separately():
    """The voice commit used to pass the combined display string
    ("Tomorrow 11 baje") as slot_time with no date, so parse_slot_datetime could
    read neither field and wrote TODAY at NOW — while the availability gate had
    validated tomorrow at 11. Verified live: rows landed at 12:32 UTC instead of
    the requested slot."""
    import inspect

    from backend.agent.processors import booking_processor as bp

    src = inspect.getsource(bp.BookingProcessor._commit_and_inject_result)
    assert 'slot_date=self.booking_state.get("pending_slot_day_str")' in src
    assert 'slot_time=self.booking_state.get("pending_slot_time_str")' in src

    # And the helper must forward it, or the fix stops at the door.
    helper = inspect.getsource(bp._commit_booking_to_db)
    assert "slot_date=slot_date" in helper


def test_an_indic_patient_name_keeps_its_vowel_marks():
    r"""re.sub(r"[^\w]") looks Unicode-safe and is not: \w excludes combining
    marks, so "विनोद" was stored as "वनद" — verified in a live row."""
    from backend.agent.processors.booking_processor import BookingProcessor

    bp = BookingProcessor(
        tenant={"id": "t", "doctors": []},
        agent_config={},
        call_meta={"caller_phone": "+910000000000"},
    )
    bp._try_extract_name("मेरा नाम विनोद है", "मेरा नाम विनोद है")
    assert bp.booking_state["patient_name"] == "विनोद"


@pytest.mark.parametrize("utterance,expected", [
    ("എന്റെ പേര് അനു", "അനു"),
    ("ನನ್ನ ಹೆಸರು ರಮೇಶ್", "ರಮೇಶ್"),
    ("my name is vinod", "Vinod"),
])
def test_patient_name_captured_in_any_script(utterance, expected):
    from backend.agent.processors.booking_processor import BookingProcessor

    bp = BookingProcessor(
        tenant={"id": "t", "doctors": []},
        agent_config={},
        call_meta={"caller_phone": "+910000000000"},
    )
    bp._try_extract_name(utterance, utterance.lower())
    assert bp.booking_state["patient_name"] == expected
