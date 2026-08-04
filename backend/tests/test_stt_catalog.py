"""
Tests for backend/services/stt_catalog.py — the STT language single source of truth.

These lock in facts established by LIVE probes against the provider APIs on
2026-08-03 with the keys in .env (the probe commands are quoted in each test's
docstring so they can be re-run when a provider changes). They are deliberately
assertions about REAL provider behaviour, not about our own defaults: the bug this
module exists to prevent was a dropdown that offered options no provider accepted.
"""
import pytest

from backend.services import stt_catalog as C


# ── The production bug ────────────────────────────────────────────────────────
def test_no_option_can_exceed_the_column():
    """Every offered code must fit agent_configs.stt_language (varchar(20)).

    The regression: the dropdown offered "Multilingual (English/Hindi/Regional)"
    as a VALUE, so saving raised
        asyncpg.exceptions.StringDataRightTruncationError:
            value too long for type character varying(20)
    """
    for provider in [*C.STT_PROVIDERS, "a_provider_added_next_year"]:
        for model in ("saarika:v2.5", "saaras:v3", "saaras:v2.5", "nova-3", "nova-2", None):
            for opt in C.stt_language_options(provider, model):
                assert len(opt["code"]) <= C.MAX_CODE_LEN, (provider, model, opt)
                assert " " not in opt["code"], f"{opt['code']!r} looks like a label"
                assert "(" not in opt["code"], f"{opt['code']!r} looks like a label"


def test_the_exact_label_that_broke_production_is_not_a_code():
    label = "Multilingual (English/Hindi/Regional)"
    assert len(label) > C.MAX_CODE_LEN
    # It must never be storable...
    for provider in C.STT_PROVIDERS:
        assert not any(
            o["code"] == label for o in C.stt_language_options(provider, "saaras:v3")
        )
    # ...and if such a row already exists it degrades to auto rather than crashing.
    assert C.canonicalize(label) == C.AUTO


@pytest.mark.parametrize("legacy", ["auto-detect", "unknown", "multi", "", "auto", None])
def test_every_auto_spelling_folds_to_one_canonical_value(legacy):
    """Old rows and the /platform/sarvam/languages payload keep working."""
    assert C.canonicalize(legacy) == C.AUTO


# ── Sarvam: live-probed, and MODEL-dependent ──────────────────────────────────
# Probe: POST https://api.sarvam.ai/speech-to-text with a bogus language_code
# enumerates 24 schema-valid values; then each was sent for real per model.
#   saarika:v2.5 -> 200 for unknown + 11 languages, and
#                   "Language 'as-IN' is not supported by saarika:v2.5 model." for the rest
#   saaras:v3    -> 200 for all 24
def test_sarvam_saarika_serves_eleven_languages():
    codes = set(C.supported_codes("sarvam", "saarika:v2.5"))
    assert codes == {C.AUTO, *C.SARVAM_STT_BASE_LANGS}
    assert len(C.SARVAM_STT_BASE_LANGS) == 11
    for saaras_only in C.SARVAM_STT_SAARAS_EXTRA_LANGS:
        assert saaras_only not in codes


def test_sarvam_saaras_serves_twenty_three_languages():
    codes = C.supported_codes("sarvam", "saaras:v3")
    assert len(codes) == 24  # 23 languages + auto
    for extra in C.SARVAM_STT_SAARAS_EXTRA_LANGS:
        assert extra in codes


def test_sarvam_odia_is_od_in_not_or_in():
    """Sarvam STT's 24 accepted codes include od-IN and NOT or-IN.

    backend/routers/providers.py::STT_MODELS and
    backend/routers/platform.py::sarvam_languages both used to claim or-IN, which
    Sarvam rejects — so Odia never worked on either surface.
    """
    assert "od-IN" in C.SARVAM_STT_BASE_LANGS
    assert "or-IN" not in C.SARVAM_STT_BASE_LANGS
    # The language_switcher detects Odia script as "or-IN"; that must still land
    # on a code Sarvam accepts rather than being dropped or falling back to Hindi.
    assert C.canonicalize("or-IN") == "od-IN"
    assert C.to_provider_code("sarvam", "saaras:v3", "or-IN") == "od-IN"


def test_sarvam_does_not_support_arabic():
    """ar-SA was fabricated in STT_MODELS; Sarvam rejects it on every model."""
    assert "ar-SA" not in C.supported_codes("sarvam", "saaras:v3")
    assert not C.is_supported("sarvam", "saaras:v3", "ar-SA")


def test_saaras_v25_takes_no_language_at_all():
    """pipecat marks saaras:v2.5 supports_language=False and raises if given one."""
    assert C.stt_language_options("sarvam", "saaras:v2.5") == [
        {"code": C.AUTO, "name": "Auto-detect"}
    ]
    for code in ("hi-IN", "kn-IN", C.AUTO):
        assert C.to_provider_code("sarvam", "saaras:v2.5", code) is None


def test_sarvam_auto_uses_sarvams_own_unknown_token():
    assert C.to_provider_code("sarvam", "saaras:v3", C.AUTO) == "unknown"


# ── Deepgram: live-probed via its real list-languages endpoint ────────────────
# Probe: GET https://api.deepgram.com/v1/models, aggregated per canonical_name.
#   nova-3-general -> 119 codes incl. hi ta te kn mr bn gu ur ar-SA en-US
#                     and NOT ml, NOT pa
#   nova-2-general -> 71 codes, hi + en-* only among Indic
@pytest.mark.parametrize("code", ["kn-IN", "ta-IN", "te-IN", "mr-IN", "bn-IN", "gu-IN"])
def test_deepgram_nova3_serves_these_indic_languages(code):
    assert C.is_supported("deepgram", "nova-3", code)


@pytest.mark.parametrize("code", ["ml-IN", "pa-IN", "od-IN"])
def test_deepgram_serves_no_malayalam_punjabi_or_odia_on_any_tier(code):
    """Deepgram answers HTTP 400 for these; pipecat retries a 400 forever."""
    assert not C.is_supported("deepgram", "nova-3", code)
    assert not C.is_supported("deepgram", "nova-2", code)


@pytest.mark.parametrize("code", ["ta-IN", "te-IN", "kn-IN", "mr-IN", "bn-IN", "gu-IN"])
def test_deepgram_nova2_serves_no_indic_language_but_hindi(code):
    assert not C.is_supported("deepgram", "nova-2", code)
    assert C.is_supported("deepgram", "nova-2", "hi-IN")


def test_deepgram_indic_languages_are_pinned_not_collapsed_to_multi():
    """kn must reach Deepgram as "kn". "multi" is English+Hindi only."""
    assert C.to_provider_code("deepgram", "nova-3", "kn-IN") == "kn"
    assert C.to_provider_code("deepgram", "nova-3", "ta-IN") == "ta"
    # en/hi DO prefer multi — it code-switches in one socket, so a caller moving
    # English -> Hindi mid-call costs no websocket reconnect.
    assert C.to_provider_code("deepgram", "nova-3", "hi-IN") == "multi"
    assert C.to_provider_code("deepgram", "nova-3", C.AUTO) == "multi"


def test_deepgram_arabic_is_not_silently_english_or_hindi():
    """The second half of the reported bug.

    _safe_lang mapped ar-SA (and en-US, od-IN, auto-detect) to "hi-IN" for EVERY
    provider, then DEEPGRAM_LANG_MAP.get(..., "en-IN") collapsed anything it did
    not list to English. Selecting Arabic transcribed Hindi, silently.
    """
    assert C.is_supported("deepgram", "nova-3", "ar-SA")
    assert C.to_provider_code("deepgram", "nova-3", "ar-SA") == "ar"


# ── ElevenLabs: live-probed; ISO-639-3, not two-letter ───────────────────────
# Probe: POST https://api.elevenlabs.io/v1/speech-to-text with a bogus
# language_code enumerates ~150 THREE-letter codes (afr, amh, ..., hin, kan, ...).
# GET /v1/speech-to-text/models is a 404 and /v1/models lists only TTS/STS models,
# so there is no live endpoint for this provider.
@pytest.mark.parametrize(
    "ours,iso3",
    [("hi-IN", "hin"), ("kn-IN", "kan"), ("ta-IN", "tam"),
     ("ml-IN", "mal"), ("pa-IN", "pan"), ("od-IN", "ori")],
)
def test_elevenlabs_gets_three_letter_codes(ours, iso3):
    """The pipeline used to send code.split("-")[0] — "hi", "kn" — which Scribe
    does not accept, so choosing a language for ElevenLabs never took effect."""
    assert C.to_provider_code("elevenlabs", None, ours) == iso3


def test_elevenlabs_covers_the_languages_deepgram_cannot():
    for code in ("ml-IN", "pa-IN", "od-IN"):
        assert C.is_supported("elevenlabs", None, code)
        assert not C.is_supported("deepgram", "nova-3", code)


def test_elevenlabs_auto_means_blank():
    assert C.to_provider_code("elevenlabs", None, C.AUTO) is None


# ── Providers that take no language hint ──────────────────────────────────────
@pytest.mark.parametrize("provider", ["whisper", "openai", "assemblyai"])
def test_autodetect_only_providers_offer_no_fake_language_choice(provider):
    """pipeline.py builds these with no language parameter at all, so offering a
    picker would be offering a control that provably does nothing."""
    options = C.stt_language_options(provider, None)
    assert [o["code"] for o in options] == [C.AUTO]
    assert C.to_provider_code(provider, None, "kn-IN") is None


# ── Provider-agnostic behaviour ───────────────────────────────────────────────
def test_unknown_provider_gets_a_usable_list_not_an_empty_dropdown():
    """A provider added later by pasting an API key must not render a dead field."""
    options = C.stt_language_options("some_vendor_added_by_pasting_a_key", None)
    assert len(options) > 1
    assert options[0]["code"] == C.AUTO
    assert C.spec_for("some_vendor_added_by_pasting_a_key") is C.UNKNOWN_PROVIDER_SPEC
    # ...and it must be honest that this is a default, not a verified capability.
    assert "not in backend/services/stt_catalog.py" in C.UNKNOWN_PROVIDER_SPEC.note


def test_auto_detect_is_first_and_always_available():
    for provider in [*C.STT_PROVIDERS, "brand_new"]:
        options = C.stt_language_options(provider, "saaras:v3")
        assert options[0]["code"] == C.AUTO
        assert C.is_supported(provider, "saaras:v3", C.AUTO)


def test_every_offered_option_has_a_real_human_label():
    """A code with no name would render as a bare "sat-IN" in the dropdown."""
    for provider in C.STT_PROVIDERS:
        for model in ("saaras:v3", "saarika:v2.5", "nova-3"):
            for opt in C.stt_language_options(provider, model):
                if opt["code"] == C.AUTO:
                    continue
                assert opt["code"] in C.LANGUAGE_NAMES, f"{opt['code']} has no display name"
                assert opt["name"] == C.LANGUAGE_NAMES[opt["code"]]
                assert opt["name"] != opt["code"]


def test_provider_specific_sets_are_genuinely_different():
    """The old dropdown showed ONE list for every provider. These must not match."""
    sarvam = C.supported_codes("sarvam", "saaras:v3")
    deepgram = C.supported_codes("deepgram", "nova-3")
    assert sarvam != deepgram
    assert "od-IN" in sarvam and "od-IN" not in deepgram
    assert "ar-SA" in deepgram and "ar-SA" not in sarvam
