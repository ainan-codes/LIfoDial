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

from pipecat.frames.frames import ErrorFrame, TextFrame
from pipecat.processors.frame_processor import FrameDirection

from backend.agent import resilience as R
from backend.agent.resilience import ResilienceProcessor, select_llm_provider, fallback_phrase


@pytest.mark.asyncio
async def test_dead_primary_falls_back_to_next_healthy():
    """Gemini dead, Groq healthy → pick Groq with its default model."""
    async def fake_probe(provider, key):
        return provider == "groq"
    with patch.object(R, "_probe", fake_probe), \
         patch.object(R, "_key_for", lambda p: "k" * 40):
        prov, key, model = await select_llm_provider({"llm_model": "gemini-2.5-flash"})
    assert prov == "groq"
    assert model == "llama-3.3-70b-versatile"


@pytest.mark.asyncio
async def test_configured_provider_kept_when_healthy():
    """Configured Groq healthy → keep the exact configured model."""
    async def fake_probe(provider, key):
        return True  # everything healthy; preferred should win
    with patch.object(R, "_probe", fake_probe), \
         patch.object(R, "_key_for", lambda p: "k" * 40):
        prov, key, model = await select_llm_provider({"llm_model": "llama-3.1-8b-instant"})
    assert prov == "groq"
    assert model == "llama-3.1-8b-instant"  # configured model preserved


@pytest.mark.asyncio
async def test_no_provider_reachable_raises():
    async def fake_probe(provider, key):
        return False
    with patch.object(R, "_probe", fake_probe), \
         patch.object(R, "_key_for", lambda p: "k" * 40):
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
            if isinstance(f, TextFrame):
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
