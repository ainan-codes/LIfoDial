"""
Tests for the TTS preview sample sentences (backend/services/tts_samples.py).

"Play Sample" used to send the language code and the spoken text from two
unrelated places, so they routinely disagreed: the Voice Library sent the
voice's English catalogue blurb ("Soft Hindi female"), the agent's Voice
Configuration sent the agent's own greeting, and both backend endpoints
defaulted to a fixed English sentence. Filtering to Kannada and pressing play
therefore produced English words with Kannada phonetics.

The rule these tests defend: the language alone decides the text, and an
explicit caller-supplied text still wins.

Run: python -m pytest backend/tests/test_tts_samples.py -v
"""
import os
import unicodedata

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest

from backend.services.sarvam_catalog import SARVAM_TTS_LANGUAGE_CODES
from backend.services.tts_samples import (
    DEFAULT_SAMPLE_TEXT,
    TTS_SAMPLE_TEXT,
    sample_text_for,
)

#: The 13 options the Voice Library's Language filter offers.
UI_LANGUAGES = [
    "hi-IN", "en-IN", "en-US", "ta-IN", "te-IN", "ar-SA",
    "kn-IN", "ml-IN", "mr-IN", "bn-IN", "gu-IN", "pa-IN", "od-IN",
]

#: Unicode script each language must actually be written in. This is the check
#: that would have caught the previous romanised set: "Namaskara! Naanu nimage
#: hege sahaya maadali?" is Kannada words in LATIN script, which makes a voice
#: sound like it is reading English with an accent.
EXPECTED_SCRIPT = {
    "hi-IN": "DEVANAGARI", "mr-IN": "DEVANAGARI",
    "ta-IN": "TAMIL", "te-IN": "TELUGU", "kn-IN": "KANNADA",
    "ml-IN": "MALAYALAM", "bn-IN": "BENGALI", "gu-IN": "GUJARATI",
    "pa-IN": "GURMUKHI", "od-IN": "ORIYA", "ar-SA": "ARABIC",
    "en-IN": "LATIN", "en-US": "LATIN",
}


def _scripts(text: str) -> set[str]:
    """Unicode script names of the letters in `text`."""
    out = set()
    for ch in text:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        for script in ("DEVANAGARI", "TAMIL", "TELUGU", "KANNADA", "MALAYALAM",
                       "BENGALI", "GUJARATI", "GURMUKHI", "ORIYA", "ARABIC"):
            if name.startswith(script):
                out.add(script)
                break
        else:
            if "LATIN" in name:
                out.add("LATIN")
    return out


@pytest.mark.parametrize("code", UI_LANGUAGES)
def test_every_ui_language_has_a_sample(code):
    assert sample_text_for(code).strip(), code


@pytest.mark.parametrize("code", UI_LANGUAGES)
def test_sample_is_written_in_the_right_script(code):
    """The regression that motivated this module: romanised text is not a
    sample of the language, it is a sample of the accent."""
    text = sample_text_for(code)
    assert _scripts(text) == {EXPECTED_SCRIPT[code]}, (
        f"{code}: expected {EXPECTED_SCRIPT[code]}, got {_scripts(text)} for {text!r}"
    )


@pytest.mark.parametrize("code", UI_LANGUAGES)
def test_samples_are_short_enough_for_a_preview(code):
    # Sarvam caps a request at 2500 chars; a preview should be a couple of
    # seconds, not a paragraph.
    assert 10 <= len(sample_text_for(code)) <= 120, code


def test_every_language_gets_a_distinct_sample_not_the_english_default():
    """Each non-English language must have its OWN sentence — the old wizard
    map covered 4 languages and silently served English for the other 9."""
    for code in UI_LANGUAGES:
        if code.startswith("en-"):
            continue
        assert sample_text_for(code) != DEFAULT_SAMPLE_TEXT, code


def test_every_sarvam_language_is_covered():
    """No Sarvam TTS language may fall back to English."""
    for code in SARVAM_TTS_LANGUAGE_CODES:
        if code.startswith("en-"):
            continue
        assert code in TTS_SAMPLE_TEXT, code
        assert sample_text_for(code) != DEFAULT_SAMPLE_TEXT, code


def test_gendered_languages_avoid_marking_the_speakers_gender():
    """Hindi/Marathi/Gujarati/Punjabi mark speaker gender on the verb, so a
    'how can I help you' sentence would be wrong for half the voices. These use
    a neutral copula instead — assert the gendered forms are absent."""
    gendered_forms = {
        "hi-IN": ["सकती", "सकता"],
        "mr-IN": ["शकते", "शकतो"],
        "gu-IN": ["શકું છું"],
        "pa-IN": ["ਸਕਦੀ", "ਸਕਦਾ"],
    }
    for code, forms in gendered_forms.items():
        text = sample_text_for(code)
        for form in forms:
            assert form not in text, f"{code} sample marks gender ({form}): {text}"


def test_unknown_and_junk_languages_fall_back_to_english_not_a_crash():
    """ElevenLabs reports free-text accents; the STT picker offers auto-detect.
    A bad code must degrade to English, never break the button."""
    for junk in (None, "", "   ", "american", "auto-detect", "zz-ZZ", "Multilingual"):
        assert sample_text_for(junk) == DEFAULT_SAMPLE_TEXT, junk


def test_english_variants_all_speak_english():
    for code in ("en", "en-GB", "en-AU", "en-IN", "en-US"):
        assert sample_text_for(code) == TTS_SAMPLE_TEXT["en-IN"]


def test_odia_accepts_both_spellings():
    """Sarvam TTS uses `od-IN`; this repo's STT catalogue uses `or-IN`."""
    assert sample_text_for("or-IN") == sample_text_for("od-IN")


def test_no_stale_romanised_samples_remain_in_the_providers_router():
    """The romanised table used to live in backend/routers/providers.py. If a
    copy reappears there, the two will drift again."""
    from pathlib import Path

    # Comments are stripped: the fix deliberately quotes one of these strings to
    # explain what was wrong with it, and that is documentation, not a table.
    code_lines = [
        line for line in Path("backend/routers/providers.py").read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    src = "\n".join(code_lines)
    for romanised in ("Namaskara! Naanu", "Vanakkam! Naan", "Nomoskar! Ami"):
        assert romanised not in src, f"romanised sample resurfaced: {romanised}"
