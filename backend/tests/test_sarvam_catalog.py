"""
Tests for the Sarvam TTS catalogue (backend/services/sarvam_catalog.py).

The catalogue exists because three lists disagreed about what Sarvam can do, and
every one of the disagreements was invisible until a user clicked something:

  * The Voice Library's Language filter was a hardcoded six-option list. Sarvam
    speaks eleven languages, so Malayalam, Kannada, Marathi, Bengali, Gujarati,
    Punjabi and Odia were unreachable — and two of those (Kannada, Marathi)
    already had voices tagged for them sitting in the catalogue unusable.
  * backend/routers/providers.py listed a speaker, `niharika`, that Sarvam does
    not recognise. Its "Play Sample" always failed.
  * backend/services/model_registry.py — which feeds the agent-creation wizard —
    listed a *different* 21 speakers including `sophia`, also not real, while
    omitting 17 that are.
  * The wizard defaulted to speaker `anushka` on model `bulbul:v3`. Those two
    are incompatible (v2 and v3 rosters are disjoint), so every new agent was
    created with a voice that could not synthesize.

So the tests that matter here are the cross-checks: that each surface serves the
same catalogue, and that no default is internally contradictory.

The facts asserted below were established by probing the live Sarvam API on
2026-08-03 (invalid-model / invalid-language / invalid-speaker validation errors
plus real synthesis of all 11 languages). These tests are offline — they guard
the wiring, not Sarvam's uptime. See test_language_codes_are_accepted_by_sarvam
for the opt-in live check.

Run: python -m pytest backend/tests/test_sarvam_catalog.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest

from backend.services.sarvam_catalog import (
    BULBUL_V2_VOICES,
    BULBUL_V3_VOICES,
    SARVAM_ALL_VOICES,
    SARVAM_DEFAULT_VOICE_BY_MODEL,
    SARVAM_TTS_BETA_LANGUAGE_CODES,
    SARVAM_TTS_LANGUAGE_CODES,
    SARVAM_TTS_LANGUAGES,
    SARVAM_VOICES,
    default_voice_for_model,
    is_valid_voice_for_model,
    normalize_language,
    voices_for_model,
)


# ── Languages ─────────────────────────────────────────────────────────────────

def test_the_eleven_ga_languages_are_exactly_what_sarvam_synthesizes():
    """Verified live: these 11 return audio, the other 12 schema-valid codes
    return "Please request beta access"."""
    assert set(SARVAM_TTS_LANGUAGE_CODES) == {
        "hi-IN", "en-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN",
        "mr-IN", "bn-IN", "gu-IN", "pa-IN", "od-IN",
    }


def test_malayalam_and_kannada_are_offered():
    """The whole point of the change: these were unreachable in the UI."""
    assert "ml-IN" in SARVAM_TTS_LANGUAGE_CODES
    assert "kn-IN" in SARVAM_TTS_LANGUAGE_CODES


def test_beta_gated_languages_are_never_offered_as_available():
    """Fabricating these is the failure mode the task called out explicitly:
    they validate against the schema but 400 on every real request."""
    assert not SARVAM_TTS_BETA_LANGUAGE_CODES & set(SARVAM_TTS_LANGUAGE_CODES)
    for code in ("as-IN", "ur-IN", "sa-IN", "ne-IN"):
        assert code not in SARVAM_TTS_LANGUAGE_CODES


def test_odia_uses_sarvams_spelling_not_iso():
    """Sarvam TTS wants `od-IN`; `or-IN` (used by this repo's STT catalogue) is
    a validation error, so the two must not be confused."""
    assert "od-IN" in SARVAM_TTS_LANGUAGE_CODES
    assert "or-IN" not in SARVAM_TTS_LANGUAGE_CODES
    assert normalize_language("or-IN") == "od-IN"


def test_every_language_has_a_display_name():
    assert [l["code"] for l in SARVAM_TTS_LANGUAGES] == SARVAM_TTS_LANGUAGE_CODES
    assert all(l["name"] and l["name"] != l["code"] for l in SARVAM_TTS_LANGUAGES)


def test_normalize_language_falls_back_for_codes_sarvam_rejects():
    # These reach the TTS layer from the STT picker and the clinic form.
    assert normalize_language("ar-SA") == "hi-IN"
    assert normalize_language("ar-AE") == "hi-IN"
    assert normalize_language("en-US") == "hi-IN"
    assert normalize_language(None) == "hi-IN"
    assert normalize_language("ml-IN") == "ml-IN"


# ── Speakers ──────────────────────────────────────────────────────────────────

def test_speaker_rosters_match_the_api():
    """44 real speakers: 37 on v3, 7 on v2, no overlap."""
    assert len(BULBUL_V3_VOICES) == 37
    assert len(BULBUL_V2_VOICES) == 7
    assert len(SARVAM_ALL_VOICES) == 44
    v3 = {v["id"] for v in BULBUL_V3_VOICES}
    v2 = {v["id"] for v in BULBUL_V2_VOICES}
    assert not (v3 & v2), "v2 and v3 rosters are disjoint — a shared id is a 400"


def test_speakers_sarvam_does_not_recognise_are_gone():
    """`niharika` and `sophia` shipped in two different lists and 400 on use."""
    ids = {v["id"] for v in SARVAM_ALL_VOICES}
    assert "niharika" not in ids
    assert "sophia" not in ids


def test_gender_split_matches_sarvams_published_roster():
    males = [v for v in BULBUL_V3_VOICES if v["gender"] == "male"]
    females = [v for v in BULBUL_V3_VOICES if v["gender"] == "female"]
    assert len(males) == 23
    assert len(females) == 14
    assert {v["gender"] for v in SARVAM_ALL_VOICES} == {"male", "female"}


def test_no_duplicate_speaker_ids():
    ids = [v["id"] for v in SARVAM_ALL_VOICES]
    assert len(ids) == len(set(ids))


def test_every_voices_primary_language_tag_is_a_language_sarvam_speaks():
    for v in SARVAM_ALL_VOICES:
        assert v["language"] in SARVAM_TTS_LANGUAGE_CODES, v["id"]


# ── Model / speaker compatibility ─────────────────────────────────────────────

def test_voices_for_model_filters_by_model():
    assert len(voices_for_model("bulbul:v3")) == 37
    assert len(voices_for_model("bulbul:v2")) == 7
    # Unfiltered serves the bulbul:v3 roster — what the Voice Library shows.
    assert voices_for_model(None) == SARVAM_VOICES == BULBUL_V3_VOICES


def test_cross_model_speakers_are_rejected():
    """The exact mismatch that made the wizard's default voice unusable."""
    assert is_valid_voice_for_model("shubh", "bulbul:v3")
    assert not is_valid_voice_for_model("anushka", "bulbul:v3")
    assert is_valid_voice_for_model("anushka", "bulbul:v2")
    assert not is_valid_voice_for_model("shubh", "bulbul:v2")
    assert not is_valid_voice_for_model(None, "bulbul:v3")


@pytest.mark.parametrize("model", ["bulbul:v3", "bulbul:v2"])
def test_each_models_default_voice_is_valid_for_that_model(model):
    """Regression: bulbul:v3 + anushka shipped as the wizard's default."""
    assert is_valid_voice_for_model(default_voice_for_model(model), model)
    assert is_valid_voice_for_model(SARVAM_DEFAULT_VOICE_BY_MODEL[model], model)


# ── Every surface serves the same catalogue ───────────────────────────────────

def test_providers_router_reexports_the_catalogue():
    from backend.routers import providers

    assert providers.SARVAM_VOICES is SARVAM_VOICES
    assert providers.SARVAM_TTS_LANGUAGE_CODES == SARVAM_TTS_LANGUAGE_CODES


def test_agent_wizard_and_voice_library_describe_the_same_speakers():
    """model_registry feeds the agent-creation wizard, sarvam_catalog feeds the
    Voice Library. They were 21-vs-38 with two fictional speakers between them."""
    from backend.services.model_registry import SARVAM_VOICES_DATA

    for model, catalog in (("bulbul:v3", BULBUL_V3_VOICES), ("bulbul:v2", BULBUL_V2_VOICES)):
        block = SARVAM_VOICES_DATA[model]
        wizard_ids = {v["id"] for v in block["male_voices"] + block["female_voices"]}
        assert wizard_ids == {v["id"] for v in catalog}, model
        assert block["language_codes"] == SARVAM_TTS_LANGUAGE_CODES, model


def test_wizard_exposes_exactly_one_default_voice_per_model():
    from backend.services.model_registry import SARVAM_VOICES_DATA

    for model in ("bulbul:v3", "bulbul:v2"):
        block = SARVAM_VOICES_DATA[model]
        defaults = [v for v in block["male_voices"] + block["female_voices"] if v.get("default")]
        assert len(defaults) == 1, model
        assert is_valid_voice_for_model(defaults[0]["id"], model)


def test_preview_language_whitelist_is_the_catalogue():
    """backend/routers/voices.py silently rewrites unsupported codes to hi-IN.
    Its local copy had drifted to include `raj-IN`, which Sarvam never accepted."""
    from backend.routers.voices import SARVAM_V3_SUPPORTED_LANGS

    assert SARVAM_V3_SUPPORTED_LANGS == frozenset(SARVAM_TTS_LANGUAGE_CODES)
    assert "raj-IN" not in SARVAM_V3_SUPPORTED_LANGS


# ── Live check (opt-in) ───────────────────────────────────────────────────────

# Opt-in, not key-presence-gated: a conftest loads .env, so gating on the key
# alone silently made the whole offline suite depend on Sarvam's availability and
# spend credits on every run. It flaked exactly that way (a rate-limited request
# under full-suite concurrency) before this was tightened.
#
#   SARVAM_LIVE_TESTS=1 python -m pytest backend/tests/test_sarvam_catalog.py -v
@pytest.mark.skipif(
    not (os.environ.get("SARVAM_LIVE_TESTS") and os.environ.get("SARVAM_API_KEY")),
    reason="live Sarvam probe; set SARVAM_LIVE_TESTS=1 with SARVAM_API_KEY to run",
)
@pytest.mark.parametrize("language", ["ml-IN", "kn-IN"])
def test_language_codes_are_accepted_by_sarvam(language):
    """Proves the newly-offered languages really synthesize, rather than merely
    passing our own schema. Deliberately hits the same endpoint the Play Sample
    button uses."""
    import time

    import httpx

    last = None
    for attempt in range(3):
        last = httpx.post(
            "https://api.sarvam.ai/text-to-speech/stream",
            headers={
                "api-subscription-key": os.environ["SARVAM_API_KEY"],
                "Content-Type": "application/json",
            },
            json={
                "text": "Hello, I am your AI receptionist.",
                "target_language_code": language,
                "speaker": default_voice_for_model("bulbul:v3"),
                "model": "bulbul:v3",
                "output_audio_codec": "mp3",
                "speech_sample_rate": 22050,
            },
            timeout=60,
        )
        # Retry throttling only — a 400 means the language really is rejected,
        # which is the thing this test exists to catch, so fail fast on it.
        if last.status_code != 429:
            break
        time.sleep(2 * (attempt + 1))

    assert last.status_code == 200, f"{last.status_code}: {last.text[:300]}"
    assert len(last.content) > 1024, "suspiciously small audio payload"
