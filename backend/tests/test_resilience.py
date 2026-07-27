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
async def test_dead_primary_falls_back_to_next_healthy():
    """Gemini dead, Groq healthy → pick Groq with its default model."""
    async def fake_probe(provider, key):
        return provider == "groq"
    with patch.object(R, "_probe", fake_probe), \
         patch.object(R, "_resolve_key", _fake_resolve_key):
        prov, key, model = await select_llm_provider({"llm_model": "gemini-2.5-flash"})
    assert prov == "groq"
    assert model == "llama-3.3-70b-versatile"


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
async def test_no_provider_reachable_raises():
    async def fake_probe(provider, key):
        return False
    with patch.object(R, "_probe", fake_probe), \
         patch.object(R, "_resolve_key", _fake_resolve_key):
        with pytest.raises(RuntimeError):
            await select_llm_provider({"llm_model": "gemini-2.5-flash"})


def test_fallback_phrase_language():
    assert "one moment" in fallback_phrase("en-IN").lower()
    assert fallback_phrase("hi-IN") != fallback_phrase("en-IN")
    # unknown language → default (english)
    assert fallback_phrase("zz-ZZ") == fallback_phrase("en-IN")


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
    assert task.spoken == ["I'm having a little trouble right now, one moment please."]


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
