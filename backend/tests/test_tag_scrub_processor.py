"""
Tests for backend/agent/processors/tag_scrub.py — the guarantee that a machine
tag is never SPOKEN to a caller.

The voice path has no equivalent of the chat path's reply post-processing:
whatever the LLM emits goes straight into TTS. The LLM is shown
[BOOKING_RESULT …] and [AVAILABILITY_NOTE] system messages every time a
booking resolves, and models echo bracketed tokens they were just shown — the
chat path leaked exactly that to a patient in production on 2026-08-11.

Because the LLM streams, the hard case is a tag SPLIT ACROSS FRAMES, which is
what most of these tests cover.

Run: python -m pytest backend/tests/test_tag_scrub_processor.py -v
"""

import pytest

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from backend.agent.processors.tag_scrub import TagScrubProcessor, scrub_spoken_text


class _Recorder(TagScrubProcessor):
    """TagScrubProcessor that records what it pushes instead of pushing it."""

    def __init__(self):
        super().__init__()
        self.spoken: list[str] = []
        self.pushed: list = []

    async def push_frame(self, frame, direction=FrameDirection.DOWNSTREAM):
        self.pushed.append(frame)
        text = getattr(frame, "text", None)
        if text is not None:
            self.spoken.append(text)


async def _run(chunks: list[str]) -> str:
    """Feed `chunks` through as one streamed LLM response; return what TTS
    would actually have been asked to say."""
    proc = _Recorder()
    for chunk in chunks:
        await proc.process_frame(LLMTextFrame(text=chunk), FrameDirection.DOWNSTREAM)
    await proc.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
    return "".join(proc.spoken)


# ── The pure scrubber ─────────────────────────────────────────────────────────

def test_scrub_spoken_text_removes_each_tag_kind():
    assert "BOOKING_RESULT" not in scrub_spoken_text(
        "[BOOKING_RESULT success=true] Your appointment is confirmed.")
    assert "AVAILABILITY_NOTE" not in scrub_spoken_text(
        "[AVAILABILITY_NOTE] Dr Rajesh is open at 3 PM.")
    assert "ACTION" not in scrub_spoken_text("Okay. [ ACTION: BOOK|a|b|c|d|e|f ]")


def test_scrub_spoken_text_leaves_ordinary_brackets_alone():
    said = "Dr Rajesh (General Physician) is in room [3] today."
    assert scrub_spoken_text(said) == said


# ── Streaming ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tag_inside_one_chunk_is_removed():
    spoken = await _run(["[BOOKING_RESULT success=true] Your appointment is confirmed."])
    assert "BOOKING_RESULT" not in spoken
    assert "Your appointment is confirmed." in spoken


@pytest.mark.asyncio
async def test_tag_split_across_chunks_is_still_removed():
    """The realistic failure: the model streams the tag a few tokens at a time,
    so no single frame ever contains the whole thing."""
    spoken = await _run(["All set. ", "[BOOK", "ING_RESULT succ", "ess=true]", " See you at 3."])
    assert "BOOKING_RESULT" not in spoken
    assert "All set." in spoken
    assert "See you at 3." in spoken


@pytest.mark.asyncio
async def test_ordinary_words_are_never_swallowed():
    spoken = await _run(["Dr Rajesh ", "(General Physician) ", "is free at 3 PM."])
    assert spoken == "Dr Rajesh (General Physician) is free at 3 PM."


@pytest.mark.asyncio
async def test_a_bracket_that_is_not_a_machine_tag_is_spoken():
    spoken = await _run(["Come to ", "[the main building] ", "at 3."])
    assert "the main building" in spoken


@pytest.mark.asyncio
async def test_unterminated_tag_at_end_of_response_is_dropped_not_spoken():
    """The model hit its token cap mid-tag. There is no closing bracket coming,
    and "ACTION colon RESCHEDULE pipe Ainan" must never be read aloud."""
    spoken = await _run(["Okay. ", "[ ACTION: RESCHEDULE|Ainan|90909"])
    assert "ACTION" not in spoken
    assert "Ainan" not in spoken
    assert "Okay." in spoken


@pytest.mark.asyncio
async def test_unterminated_ordinary_bracket_is_still_spoken():
    spoken = await _run(["Please come to ", "[the annexe"])
    assert "the annexe" in spoken


@pytest.mark.asyncio
async def test_a_reply_that_is_nothing_but_a_tag_speaks_nothing():
    spoken = await _run(["[BOOKING_RESULT success=true]"])
    assert spoken.strip() == ""


@pytest.mark.asyncio
async def test_standalone_tts_utterances_pass_through_scrubbed():
    """The emergency message BookingProcessor pushes is a TTSSpeakFrame — a
    complete utterance, so it must be forwarded immediately, never held."""
    proc = _Recorder()
    await proc.process_frame(
        TTSSpeakFrame("[BOOKING_RESULT success=true] Please call 108 right away."),
        FrameDirection.DOWNSTREAM,
    )
    assert len(proc.pushed) == 1
    assert proc.spoken[0] == "Please call 108 right away."


@pytest.mark.asyncio
async def test_non_text_frames_pass_through_untouched():
    proc = _Recorder()
    end = LLMFullResponseEndFrame()
    await proc.process_frame(end, FrameDirection.DOWNSTREAM)
    assert proc.pushed == [end]
