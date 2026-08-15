"""
Tests for provider failover + never-silence (audit FIX 2).

Covers:
  - select_llm_provider skips a dead primary and picks the next healthy provider,
    keeping the configured model only when the configured provider wins.
  - RuntimeError when NO provider is reachable (caller treats as fatal).
  - ResilienceProcessor speaks a fallback phrase on ErrorFrame, debounces a
    burst into one utterance, and honors the hard cap.

Run: python -m pytest backend/tests/test_resilience.py -v
"""

import asyncio
from unittest.mock import patch

import pytest

from pipecat.frames.frames import ErrorFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection

from backend.agent import resilience as R
from backend.agent.resilience import ResilienceProcessor, select_llm_provider, fallback_phrase


@pytest.fixture(autouse=True)
def _reset_llm_selection_cache():
    """select_llm_provider caches its result in a module-level dict keyed by
    provider+model (see resilience._selection_cache) — without a reset, a test
    reusing the same model string as an earlier test would silently get that
    earlier test's cached provider instead of exercising its own mocked probe."""
    R.reset_llm_selection_cache()
    yield
    R.reset_llm_selection_cache()


async def _fake_resolve_key(provider):
    return "k" * 40


@pytest.mark.asyncio
async def test_an_unconfigured_primary_falls_back_to_the_next_healthy():
    """A provider with NO KEY is a setup gap, and that IS grounds to fall back.

    Contrast test_a_probe_failure_never_changes_the_operators_choice below: the
    distinction this pair draws is the whole selection policy — "not set up" and
    "did not answer a 3-second GET" are not the same condition and must not have
    the same consequence.
    """
    async def fake_probe(provider, key):
        return provider == "groq"

    async def no_key_for_gemini(provider):
        return "" if provider == "gemini" else "k" * 40

    with patch.object(R, "_probe", fake_probe), \
         patch.object(R, "_resolve_key", no_key_for_gemini):
        prov, key, model = await select_llm_provider({"llm_model": "gemini-2.5-flash"})
    assert prov == "groq"
    assert model == "llama-3.3-70b-versatile"


@pytest.mark.asyncio
async def test_a_probe_failure_never_changes_the_operators_choice():
    """The operator picked a provider and a model. A probe blip must not move
    the call to a different VENDOR and silently drop the chosen model with it.

    The probe is a list-models GET on a 1.5s connect / 3s read timeout. It fails
    for slow DNS, one edge 503, a cold TLS handshake — none of which mean the
    provider cannot serve this call. And it cannot detect the failure that
    actually happens, because list-models returns 200 for a key whose token
    budget is fully spent (see resilience._probe). Reported 2026-08-15: a model
    was selected in the dashboard and calls ran on a different provider's default.
    """
    async def every_probe_fails(provider, key):
        return False

    with patch.object(R, "_probe", every_probe_fails), \
         patch.object(R, "_resolve_key", _fake_resolve_key):
        prov, key, model = await select_llm_provider(
            {"llm_provider": "groq", "llm_model": "openai/gpt-oss-120b"})
    assert prov == "groq", "a probe blip moved the call to another vendor"
    assert model == "openai/gpt-oss-120b", "the operator's chosen model was discarded"


@pytest.mark.asyncio
async def test_configured_provider_kept_when_healthy():
    """Configured Groq healthy → keep the exact configured model."""
    async def fake_probe(provider, key):
        return True  # everything healthy; preferred should win
    with patch.object(R, "_probe", fake_probe), \
         patch.object(R, "_resolve_key", _fake_resolve_key):
        prov, key, model = await select_llm_provider({"llm_model": "llama-3.1-8b-instant"})
    assert prov == "groq"
    assert model == "llama-3.1-8b-instant"  # configured model preserved


@pytest.mark.asyncio
async def test_no_provider_configured_at_all_raises():
    """Nothing has a key — there is genuinely nothing to run the call on."""
    async def fake_probe(provider, key):
        return False

    async def no_keys(provider):
        return ""

    with patch.object(R, "_probe", fake_probe), \
         patch.object(R, "_resolve_key", no_keys):
        with pytest.raises(RuntimeError):
            await select_llm_provider({"llm_model": "gemini-2.5-flash"})


def test_fallback_phrase_language():
    assert fallback_phrase("en-IN").strip()
    assert fallback_phrase("hi-IN") != fallback_phrase("en-IN")
    # unknown language → default (english)
    assert fallback_phrase("zz-ZZ") == fallback_phrase("en-IN")


# ── The phrases must not promise what nothing delivers ────────────────────────

def test_no_fallback_phrase_promises_a_followup():
    """These phrases used to say "one moment please" / "kripya thodi der rukiye" /
    "ஒரு நிமிடம் காத்திருங்கள்" — a promise of a later message, in a place where
    nothing can send one.

    _speak_fallback is reached only AFTER _try_another_model has declined or run
    out of models, so at that point nothing further is coming for this turn at
    all: the caller waits, hears nothing, and hangs up. This is the identical
    defect the codebase already bans in MODEL output via
    action_tag.promises_followup — it was simply living in our own constants,
    where no test looked.

    Note this could only ever have been caught by widening that detector first: it
    had patterns for English, Hindi and Marathi only, so of the eight offending
    phrases it recognised exactly one.
    """
    from backend.services.action_tag import promises_followup

    offenders = {
        lang: phrase for lang, phrase in R._FALLBACK_PHRASES.items()
        if promises_followup(phrase)
    }
    assert not offenders, (
        f"{len(offenders)} fallback phrase(s) promise a follow-up nothing sends: {offenders}"
    )


def test_every_outbound_constant_resolves_its_turn():
    """The same rule, over every constant this product speaks or writes without
    the model's involvement. These are exactly the strings that get used when the
    model is the broken part, so they are the last place a stranding phrase can
    hide — and each lives in a different module, which is how one of them kept the
    bug for a year while the others were audited."""
    from backend.agent import spoken_fallback
    from backend.services import tag_recovery
    from backend.services.action_tag import promises_followup

    tables: list[tuple[str, str, str]] = [
        (f"resilience/{lang}", lang, phrase)
        for lang, phrase in R._FALLBACK_PHRASES.items()
    ] + [
        (f"end_call/{lang}", lang, phrase)
        for lang, phrase in R._END_CALL_PHRASES.items()
    ] + [
        (f"spoken_fallback/{lang}/{key}", lang, spoken_fallback.sentence(key, lang))
        for lang in spoken_fallback.supported_languages()
        for key in (spoken_fallback.BOOKED, spoken_fallback.CANCELLED,
                    spoken_fallback.RESCHEDULED, spoken_fallback.ACTION_FAILED,
                    spoken_fallback.NOT_UNDERSTOOD)
    ] + [
        (f"needs_details/{lang}", lang, tag_recovery.needs_details_reply(lang))
        for lang in tag_recovery.supported_languages()
    ]

    offenders = [where for where, _lang, text in tables if promises_followup(text)]
    assert not offenders, f"outbound constants that promise a follow-up: {offenders}"
    for where, _lang, text in tables:
        assert text.strip(), f"{where} is empty — that is silence, not a fallback"


class _SpyTask:
    def __init__(self):
        self.spoken = []
    async def queue_frames(self, frames):
        for f in frames:
            # Must be TTSSpeakFrame specifically: a bare TextFrame queued at the
            # task source is NOT synthesized by TTSService outside an LLM response
            # turn, so asserting on TextFrame here would pass while the caller
            # actually heard silence.
            if isinstance(f, TTSSpeakFrame):
                self.spoken.append(f.text)


@pytest.mark.asyncio
async def test_errorframe_speaks_fallback_not_silence():
    proc = ResilienceProcessor(language="en-IN", min_gap_seconds=8.0, max_fallbacks=4)
    task = _SpyTask()
    proc.bind_task(task)
    # push_frame is a no-op stub for the test (no downstream linked)
    with patch.object(proc, "push_frame", new=_noop):
        await proc.process_frame(ErrorFrame(error="boom"), FrameDirection.DOWNSTREAM)
    # Compared against the constant, not a copy of its text: this test's subject
    # is that SOMETHING is spoken, and a literal here just breaks whenever the
    # wording is corrected (as it was when the phrase stopped promising a wait).
    assert task.spoken == [fallback_phrase("en-IN")]


@pytest.mark.asyncio
async def test_burst_of_errors_debounced_to_one():
    proc = ResilienceProcessor(language="en-IN", min_gap_seconds=8.0, max_fallbacks=4)
    task = _SpyTask()
    proc.bind_task(task)
    with patch.object(proc, "push_frame", new=_noop):
        for _ in range(5):
            await proc.process_frame(ErrorFrame(error="boom"), FrameDirection.DOWNSTREAM)
    # 5 rapid ErrorFrames within the min-gap window → exactly one spoken phrase
    assert len(task.spoken) == 1


@pytest.mark.asyncio
async def test_cap_enforced_even_when_gap_passes():
    proc = ResilienceProcessor(language="en-IN", min_gap_seconds=0.0, max_fallbacks=2)
    task = _SpyTask()
    proc.bind_task(task)
    with patch.object(proc, "push_frame", new=_noop):
        for _ in range(5):
            await proc.process_frame(ErrorFrame(error="boom"), FrameDirection.DOWNSTREAM)
    assert len(task.spoken) == 2  # capped


async def _noop(*args, **kwargs):
    return None
