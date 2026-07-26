"""Tests for mid-call language switching (backend/agent/processors/language_switcher.py).

Covers the detector and the processor's switch behaviour: which frames it emits,
in which direction, and — just as important — when it stays put.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_language_switcher.db")

import pytest
from pipecat.frames.frames import (
    STTUpdateSettingsFrame,
    TextFrame,
    TranscriptionFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from backend.agent.processors.language_switcher import (
    LanguageSwitchProcessor,
    detect_language_from_text,
)

HINDI = "नमस्ते, मुझे कल का अपॉइंटमेंट चाहिए"
HINGLISH = "मुझे Dr. Sharma के साथ appointment चाहिए"
TAMIL = "வணக்கம், எனக்கு நாளை அப்பாயிண்ட்மென்ட் வேண்டும்"
ENGLISH = "Hello, I want to book an appointment tomorrow"


@pytest.mark.parametrize(
    "text,expected",
    [
        (ENGLISH, "en-IN"),
        (HINDI, "hi-IN"),
        (TAMIL, "ta-IN"),
        ("ഹായ്, എനിക്ക് ഒരു അപ്പോയിന്റ്മെന്റ് വേണം", "ml-IN"),
        ("హాయ్, నాకు రేపు అపాయింట్‌మెంట్ కావాలి", "te-IN"),
        # Hinglish: Latin wins on raw count, but the caller is speaking Hindi.
        (HINGLISH, "hi-IN"),
        # Too short to justify retuning two services.
        ("ok", ""),
        ("haan", ""),
        ("", ""),
    ],
)
def test_detect_language(text, expected):
    assert detect_language_from_text(text) == expected


class _FakeSettings:
    """Stands in for a pipecat service Settings delta class."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeService:
    Settings = _FakeSettings


def _make(switch_stt=True, initial="en-IN", confirm_turns=1):
    proc = LanguageSwitchProcessor(
        tts=_FakeService(),
        stt=_FakeService(),
        initial_language=initial,
        stt_language_map=lambda code: {"hi-IN": "hi", "ta-IN": "ta"}.get(code, ""),
        switch_stt=switch_stt,
        confirm_turns=confirm_turns,
    )
    pushed: list[tuple] = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append((frame, direction))

    proc.push_frame = capture  # type: ignore[method-assign]
    return proc, pushed


async def _say(proc, text):
    await proc._maybe_switch(text)


@pytest.mark.asyncio
async def test_switch_emits_tts_downstream_and_stt_upstream():
    proc, pushed = _make()
    await _say(proc, HINDI)

    tts = [(f, d) for f, d in pushed if isinstance(f, TTSUpdateSettingsFrame)]
    stt = [(f, d) for f, d in pushed if isinstance(f, STTUpdateSettingsFrame)]

    assert len(tts) == 1 and tts[0][1] == FrameDirection.DOWNSTREAM
    assert tts[0][0].delta.kwargs == {"language": "hi-IN"}      # TTS speaks BCP-47
    assert len(stt) == 1 and stt[0][1] == FrameDirection.UPSTREAM
    assert stt[0][0].delta.kwargs == {"language": "hi"}         # STT gets provider code
    assert proc.current_language == "hi-IN"
    assert proc.switch_count == 1


@pytest.mark.asyncio
async def test_same_language_never_switches():
    proc, pushed = _make(initial="en-IN")
    await _say(proc, ENGLISH)
    assert pushed == []
    assert proc.switch_count == 0


@pytest.mark.asyncio
async def test_short_utterance_never_switches():
    proc, pushed = _make()
    await _say(proc, "ok")
    await _say(proc, "haan")
    assert pushed == []


@pytest.mark.asyncio
async def test_switch_stt_false_only_retunes_tts():
    """Multilingual STT (nova-3 multi / saaras) must not be reconnected."""
    proc, pushed = _make(switch_stt=False)
    await _say(proc, TAMIL)

    assert any(isinstance(f, TTSUpdateSettingsFrame) for f, _ in pushed)
    assert not any(isinstance(f, STTUpdateSettingsFrame) for f, _ in pushed)


@pytest.mark.asyncio
async def test_unmapped_stt_language_skips_stt_but_still_switches_tts():
    proc, pushed = _make()
    await _say(proc, "ഹായ്, എനിക്ക് ഒരു അപ്പോയിന്റ്മെന്റ് വേണം")  # ml-IN not in the map

    assert any(isinstance(f, TTSUpdateSettingsFrame) for f, _ in pushed)
    assert not any(isinstance(f, STTUpdateSettingsFrame) for f, _ in pushed)
    assert proc.current_language == "ml-IN"


@pytest.mark.asyncio
async def test_confirm_turns_debounces():
    proc, pushed = _make(confirm_turns=2)
    await _say(proc, HINDI)
    assert pushed == []                      # one turn is not enough
    await _say(proc, HINDI)
    assert proc.current_language == "hi-IN"  # second consecutive turn commits


@pytest.mark.asyncio
async def test_switch_back_and_forth():
    proc, _ = _make()
    await _say(proc, HINDI)
    assert proc.current_language == "hi-IN"
    await _say(proc, ENGLISH)
    assert proc.current_language == "en-IN"
    assert proc.switch_count == 2


@pytest.mark.asyncio
async def test_on_switch_callback_fires_and_failure_is_contained():
    calls = []
    proc = LanguageSwitchProcessor(
        tts=_FakeService(), stt=None, initial_language="en-IN",
        on_switch=calls.append,
    )
    proc.push_frame = lambda *a, **k: _noop()  # type: ignore[method-assign]
    await proc._maybe_switch(HINDI)
    assert calls == ["hi-IN"]

    boom = LanguageSwitchProcessor(
        tts=_FakeService(), stt=None, initial_language="en-IN",
        on_switch=lambda _: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    boom.push_frame = lambda *a, **k: _noop()  # type: ignore[method-assign]
    await boom._maybe_switch(HINDI)           # must not raise
    assert boom.current_language == "hi-IN"


async def _noop():
    return None


@pytest.mark.asyncio
async def test_non_transcription_frames_pass_through_untouched():
    proc, pushed = _make()
    frame = TextFrame("some agent text")
    await proc.process_frame(frame, FrameDirection.DOWNSTREAM)
    assert [f for f, _ in pushed] == [frame]


@pytest.mark.asyncio
async def test_transcription_frame_is_forwarded_as_well_as_switching():
    """The user aggregator and transcript publisher sit downstream — swallowing
    the transcription here would break the live transcript and the whole turn."""
    proc, pushed = _make()
    frame = TranscriptionFrame(text=HINDI, user_id="u1", timestamp="2026-07-26T00:00:00Z")
    await proc.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert frame in [f for f, _ in pushed]
    assert any(isinstance(f, TTSUpdateSettingsFrame) for f, _ in pushed)
