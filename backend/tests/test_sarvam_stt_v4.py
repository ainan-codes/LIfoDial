"""
saaras:v4 is selectable, saaras:v3 still is, and v3 is still the default.

Everything asserted here was checked against the live Sarvam API on 2026-08-06
before the model was wired in:

  * the transcribe endpoint's own error names the real set:
      "Supported models: 'saarika:v2.5', 'saaras:v3', 'saaras:v4'."
  * v3 and v4 transcribed the SAME Malayalam WAV to the same verbatim text, with
    the same response keys and the same accepted parameters.
  * saaras:v4-multispk and saaras:v3-realtime appear in Sarvam's request validator
    but are REJECTED by the transcribe endpoint, so they are deliberately absent.

The load-bearing detail is pipecat: 1.5.0 raises ValueError for any model outside
its MODEL_CONFIGS, and it has no v4 entry — so offering v4 without registering it
would break the call rather than degrade. See
backend/agent/pipeline.py::_register_sarvam_v4_with_pipecat.

Run: python -m pytest backend/tests/test_sarvam_stt_v4.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-sarvam-v4")
os.environ.setdefault("ENVIRONMENT", "development")

import pytest


def test_v4_is_selectable_and_v3_is_still_first():
    """Additive, and the default is unchanged — models_for()[0] is both the picker
    default and the repair value for an unknown stored model."""
    from backend.services.agent_defaults import models_for

    models = models_for("stt", "sarvam")
    assert models[0] == "saaras:v3", "the default STT model moved; that is not additive"
    assert "saaras:v4" in models
    assert "saarika:v2.5" in models


def test_v4_survives_a_save_and_v3_still_does():
    """normalize_provider_choice is the write-time validator: a model it does not
    recognise is silently replaced, so this is what makes v4 actually storable."""
    from backend.services.agent_defaults import normalize_provider_choice

    assert normalize_provider_choice("stt", "sarvam", "saaras:v4") == ("sarvam", "saaras:v4")
    assert normalize_provider_choice("stt", "sarvam", "saaras:v3") == ("sarvam", "saaras:v3")
    # And a blank choice still lands on the default, not on v4.
    assert normalize_provider_choice("stt", "sarvam", "")[1] == "saaras:v3"


def test_models_the_endpoint_rejects_are_not_offered():
    """Sarvam's validator lists these; the transcribe endpoint 400s on them. An
    agent saved onto one could not take a call."""
    from backend.routers.platform import SARVAM_STT_MODELS
    from backend.services.agent_defaults import models_for

    for phantom in ("saaras:v4-multispk", "saaras:v3-realtime", "saarika:flash"):
        assert phantom not in models_for("stt", "sarvam")
        assert phantom not in SARVAM_STT_MODELS
    # 'saaras:v2' / 'saarika:v2' are 400s too — offered before, not any more.
    for dead in ("saaras:v2", "saarika:v2"):
        assert dead not in SARVAM_STT_MODELS
        assert dead not in models_for("stt", "sarvam")


def test_pipecat_can_actually_build_v4():
    """The whole reason the shim exists. Without a MODEL_CONFIGS entry, pipecat's
    constructor raises ValueError and the caller hears dead air."""
    from pipecat.services.sarvam.stt import MODEL_CONFIGS

    from backend.agent import pipeline  # noqa: F401  (import registers the shim)

    assert "saaras:v4" in MODEL_CONFIGS, "saaras:v4 was offered but pipecat cannot build it"
    v3, v4 = MODEL_CONFIGS["saaras:v3"], MODEL_CONFIGS["saaras:v4"]
    # Registered as a copy of v3's capabilities, which is what the live A/B showed.
    assert v4.supports_language == v3.supports_language is True
    assert v4.supports_mode == v3.supports_mode is True
    assert v4.default_mode == "transcribe", "v4 must transcribe, not translate"
    assert v4.use_translate_endpoint is False
    assert v4 is not v3, "must be its own config object, not an alias of v3's"


def test_the_pipeline_accepts_v4_and_still_defaults_to_v3():
    from backend.agent.pipeline import (
        SARVAM_STT_DEFAULT,
        SARVAM_STT_MODELS,
        resolve_sarvam_stt_model,
    )

    assert SARVAM_STT_DEFAULT == "saaras:v3"
    assert "saaras:v4" in SARVAM_STT_MODELS
    assert resolve_sarvam_stt_model("saaras:v4") == "saaras:v4"
    assert resolve_sarvam_stt_model("saaras:v3") == "saaras:v3"
    # Legacy ids still heal rather than raise.
    assert resolve_sarvam_stt_model("saaras:v2") == "saaras:v3"
    assert resolve_sarvam_stt_model(None) == "saaras:v3"


def test_v4_serves_the_same_languages_as_v3():
    """stt_catalog keys off the 'saaras' prefix, so v4 inherits the 23-language
    list. If that ever changes, a Malayalam agent on v4 would lose its language."""
    from backend.services import stt_catalog

    v3 = stt_catalog.stt_language_options("sarvam", "saaras:v3")
    v4 = stt_catalog.stt_language_options("sarvam", "saaras:v4")
    assert v4 == v3 and len(v4) > 11
    codes = {o["code"] if isinstance(o, dict) else o for o in v4}
    for expected in ("ml-IN", "kn-IN", "hi-IN", "en-IN"):
        assert expected in codes, f"{expected} missing from saaras:v4's languages"


def test_v4_appears_in_the_dashboard_catalogues():
    from backend.routers.platform import PROVIDERS
    from backend.routers.providers import STT_MODELS

    sarvam = next(p for p in PROVIDERS["stt"] if p["id"] == "sarvam")
    assert sarvam["models"][0] == "saaras:v3", "models[0] is the dashboard default"
    assert "saaras:v4" in sarvam["models"]

    ids = {m["id"] for m in STT_MODELS}
    assert {"saaras:v3", "saaras:v4"} <= ids
    # Exactly one model is flagged recommended, and it is still v3.
    recommended = [m["id"] for m in STT_MODELS if m.get("recommended")]
    assert recommended == ["saaras:v3"]


@pytest.mark.parametrize("model", ["saaras:v3", "saaras:v4"])
def test_a_malayalam_agent_can_be_built_on_either_model(model):
    """The pair a Malayalam clinic actually needs: Sarvam STT + an ml-IN language.
    to_provider_code returning None would mean the language is dropped."""
    from backend.services import stt_catalog

    assert stt_catalog.to_provider_code("sarvam", model, "ml-IN") == "ml-IN"
    assert stt_catalog.to_provider_code("sarvam", model, "kn-IN") == "kn-IN"
