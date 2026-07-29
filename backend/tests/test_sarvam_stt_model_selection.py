"""Sarvam STT model selection must default to TRANSCRIBE, never to TRANSLATE.

saaras:v2.5 is Sarvam's speech-to-text-*translate* model: pipecat builds it
against speech_to_text_translate_streaming and it returns English for any input
language. Verified against the live API on 2026-07-29 with identical audio —

    saaras:v2.5 → "Hello, I need an appointment with the doctor tomorrow morning."
    saaras:v3   → "नमस्ते, मुझे कल सुबह डॉक्टर से अपॉइंटमेंट चाहिए।"

pipeline.py used to coerce every unrecognised model id to saaras:v2.5, which
made English-only transcription the effective default for any agent that had not
explicitly chosen a model — and broke both the "answer in the caller's language"
rule and LanguageSwitchProcessor (it detects language from the transcript's
script, and a translated transcript has no Devanagari in it).
"""
import pytest

from backend.agent.pipeline import (
    SARVAM_STT_DEFAULT,
    SARVAM_STT_MODELS,
    resolve_sarvam_stt_model,
)

TRANSLATE_MODEL = "saaras:v2.5"


def test_the_default_is_a_transcribe_model():
    assert SARVAM_STT_DEFAULT == "saaras:v3"
    assert SARVAM_STT_DEFAULT != TRANSLATE_MODEL


@pytest.mark.parametrize("requested", [None, "", "   "])
def test_unset_model_never_falls_into_translate(requested):
    assert resolve_sarvam_stt_model(requested) == SARVAM_STT_DEFAULT


@pytest.mark.parametrize("legacy,expected", [
    ("saaras:v1", "saaras:v3"),
    ("saaras:v2", "saaras:v3"),       # the old stored default for every agent
    ("saarika:v1", "saarika:v2.5"),
    ("saarika:v2", "saarika:v2.5"),
])
def test_retired_ids_are_upgraded_not_translated(legacy, expected):
    assert resolve_sarvam_stt_model(legacy) == expected


@pytest.mark.parametrize("foreign", ["nova-3", "nova-2", "whisper-1", "best", "scribe_v1"])
def test_another_providers_model_id_does_not_become_translate(foreign):
    """Switching stt_provider Deepgram → Sarvam leaves stt_model='nova-3'."""
    assert resolve_sarvam_stt_model(foreign) == SARVAM_STT_DEFAULT


@pytest.mark.parametrize("model", sorted(SARVAM_STT_MODELS))
def test_an_explicit_supported_choice_is_honoured(model):
    """Including saaras:v2.5 — translating is allowed, just never implicit."""
    assert resolve_sarvam_stt_model(model) == model


def test_choosing_translate_explicitly_is_warned_about(caplog):
    with caplog.at_level("WARNING"):
        assert resolve_sarvam_stt_model(TRANSLATE_MODEL, room_name="r1") == TRANSLATE_MODEL
    assert "TRANSLATE" in caplog.text
    assert "saaras:v3" in caplog.text


def test_every_resolved_model_is_buildable_by_pipecat():
    """Guards against handing SarvamSTTService a model it has no config for —
    which raises inside the job, so the agent never joins and the caller hears
    dead air with nothing useful logged."""
    from pipecat.services.sarvam.stt import MODEL_CONFIGS

    candidates = list(SARVAM_STT_MODELS) + [
        None, "", "saaras:v2", "saarika:v2", "nova-3", "gibberish",
    ]
    for c in candidates:
        assert resolve_sarvam_stt_model(c) in MODEL_CONFIGS
