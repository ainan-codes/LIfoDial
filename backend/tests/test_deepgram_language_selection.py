"""
Pins Deepgram model/language selection to what the REAL Deepgram API accepts.

Every expectation here was verified with live probes on 2026-07-28. Deepgram's own
400 body names the required tier:

    "No such model/language/tier combination found. You could try the 'general'
     model (language: ta, Nova-3 tier)."

    nova-3 + en/hi/ta/te/kn/mr/bn/gu -> 200
    nova-3 + ml, pa                  -> 400 (Deepgram supports neither at all)
    nova-2 + ta/te/kn/ml/mr/bn/pa/gu -> 400
    nova-2 + hi, en-*                -> 200

The shipped code had this INVERTED: it defaulted Indic languages to nova-2 and
actively downgraded an explicit nova-3 choice to nova-2 — precisely the rejected
combination. The resulting 400 was invisible because pipecat's Deepgram
_connection_handler swallows it in a bare `except` and retries in a `while True`
with no backoff, so the agent greeted the caller and then never transcribed.

These tests assert the SELECTION RULES rather than calling the network, so they
stay fast and offline — but the constants they guard came from real probes.

Run: python -m pytest backend/tests/test_deepgram_language_selection.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest

from backend.agent.pipeline import (
    _DG_LANG_MAP,
    _DG_NOVA3_MULTI_LANGS,
    _DG_UNSUPPORTED_LANGS,
)

# Exactly what the live probes accepted on nova-3.
NOVA3_OK = {"en", "hi", "ta", "te", "kn", "mr", "bn", "gu"}
# Rejected by nova-2 — must never be sent there.
NOVA2_REJECTS = {"ta", "te", "kn", "ml", "mr", "bn", "pa", "gu"}


def _selected_language(stt_language: str, model: str = "nova-3") -> str:
    """Mirror of the pipeline's dg_lang decision (pipeline.py deepgram branch)."""
    dg_lang = _DG_LANG_MAP.get(stt_language, "en-IN")
    base = dg_lang.split("-")[0]
    if model.startswith("nova-3"):
        return "multi" if base in _DG_NOVA3_MULTI_LANGS else base
    return dg_lang


# ── The constants must match reality ─────────────────────────────────────────

def test_unsupported_set_is_exactly_what_deepgram_cannot_do():
    assert _DG_UNSUPPORTED_LANGS == {"ml", "pa"}


def test_no_unsupported_language_is_also_marked_nova3_multi():
    assert not (_DG_UNSUPPORTED_LANGS & _DG_NOVA3_MULTI_LANGS)


def test_every_multi_language_is_actually_nova3_capable():
    assert _DG_NOVA3_MULTI_LANGS <= NOVA3_OK


def test_lang_map_covers_every_language_the_product_offers():
    """Settings and ConfigureTab both offer these ten codes."""
    for code in ("en-IN", "hi-IN", "ta-IN", "te-IN", "kn-IN",
                 "ml-IN", "mr-IN", "bn-IN", "gu-IN", "pa-IN"):
        assert code in _DG_LANG_MAP, f"{code} missing from _DG_LANG_MAP"


# ── Selection never produces a combination Deepgram rejects ──────────────────

@pytest.mark.parametrize("stt_language", [
    "en-IN", "hi-IN", "ta-IN", "te-IN", "kn-IN", "mr-IN", "bn-IN", "gu-IN",
])
def test_supported_languages_resolve_to_a_valid_nova3_target(stt_language):
    selected = _selected_language(stt_language, "nova-3")
    assert selected == "multi" or selected in NOVA3_OK, (
        f"{stt_language} -> {selected!r} is not a valid nova-3 language"
    )


@pytest.mark.parametrize("stt_language", ["ta-IN", "te-IN", "kn-IN", "mr-IN", "bn-IN", "gu-IN"])
def test_indic_languages_are_pinned_not_forced_to_multi(stt_language):
    """nova-3 'multi' does not cover these — they must use their own code."""
    assert _selected_language(stt_language, "nova-3") == _DG_LANG_MAP[stt_language]


@pytest.mark.parametrize("stt_language", ["en-IN", "hi-IN"])
def test_english_and_hindi_use_multi_for_free_code_switching(stt_language):
    assert _selected_language(stt_language, "nova-3") == "multi"


@pytest.mark.parametrize("stt_language", ["ml-IN", "pa-IN"])
def test_deepgram_unsupported_languages_are_flagged_for_provider_switch(stt_language):
    """The pipeline must move these to Sarvam rather than send a doomed request."""
    base = _DG_LANG_MAP[stt_language].split("-")[0]
    assert base in _DG_UNSUPPORTED_LANGS


def test_the_old_inverted_default_would_have_been_rejected():
    """Documents the regression: the previous default sent Indic -> nova-2."""
    for stt_language in ("ta-IN", "te-IN", "kn-IN", "mr-IN", "bn-IN", "gu-IN"):
        old_default = "nova-3" if _DG_LANG_MAP[stt_language].startswith("en") else "nova-2"
        assert old_default == "nova-2"
        assert _DG_LANG_MAP[stt_language].split("-")[0] in NOVA2_REJECTS, (
            "this combination is exactly what Deepgram answers 400 to"
        )
