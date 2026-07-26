"""Interim-transcript throttling in LiveKitTranscriptPublisher.

Deepgram emits interims several times a second; each publish is a JSON encode
plus a WebRTC data message. On the 0.1-CPU free-tier worker that competes with
real-time audio, so interims are rate-limited. Finals must NEVER be throttled —
dropping one would leave the widget showing a stale partial forever.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_publisher_throttle.db")

import asyncio

import pytest
from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection

from backend.agent.processors.transcript_publisher import LiveKitTranscriptPublisher

TS = "2026-07-26T00:00:00Z"


def _make():
    pub = LiveKitTranscriptPublisher(transport=None)
    published: list[tuple[str, bool, int]] = []

    async def fake_publish(text, final, seq):
        published.append((text, final, seq))

    pub._safe_publish_user = fake_publish  # type: ignore[method-assign]
    pushed = []

    async def capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    pub.push_frame = capture  # type: ignore[method-assign]
    return pub, published, pushed


async def _drain():
    """Let the fire-and-forget publish tasks run."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_rapid_interims_are_throttled():
    pub, published, _ = _make()
    for i in range(10):
        await pub.process_frame(
            InterimTranscriptionFrame(text=f"partial {i}", user_id="u", timestamp=TS),
            FrameDirection.DOWNSTREAM,
        )
    await _drain()

    assert pub.interims_published == 1, "only the first interim should get through"
    assert pub.interims_skipped == 9
    assert len(published) == 1


@pytest.mark.asyncio
async def test_finals_are_never_throttled():
    pub, published, _ = _make()
    for i in range(5):
        await pub.process_frame(
            TranscriptionFrame(text=f"final {i}", user_id="u", timestamp=TS),
            FrameDirection.DOWNSTREAM,
        )
    await _drain()

    assert [p[0] for p in published] == [f"final {i}" for i in range(5)]
    assert all(p[1] is True for p in published)
    assert pub.interims_skipped == 0


@pytest.mark.asyncio
async def test_final_still_published_right_after_a_throttled_interim():
    """The exact regression to fear: an interim eats the budget and the final
    that follows it milliseconds later gets dropped, freezing the widget."""
    pub, published, _ = _make()
    await pub.process_frame(
        InterimTranscriptionFrame(text="hel", user_id="u", timestamp=TS),
        FrameDirection.DOWNSTREAM,
    )
    await pub.process_frame(
        InterimTranscriptionFrame(text="hello wor", user_id="u", timestamp=TS),
        FrameDirection.DOWNSTREAM,
    )
    await pub.process_frame(
        TranscriptionFrame(text="hello world", user_id="u", timestamp=TS),
        FrameDirection.DOWNSTREAM,
    )
    await _drain()

    assert ("hello world", True, 2) in published
    assert pub.interims_skipped == 1


@pytest.mark.asyncio
async def test_interim_allowed_again_after_the_gap():
    pub, published, _ = _make()
    await pub.process_frame(
        InterimTranscriptionFrame(text="one", user_id="u", timestamp=TS),
        FrameDirection.DOWNSTREAM,
    )
    pub._last_interim_ts -= pub._INTERIM_MIN_GAP_SECS + 0.01  # simulate elapsed time
    await pub.process_frame(
        InterimTranscriptionFrame(text="one two", user_id="u", timestamp=TS),
        FrameDirection.DOWNSTREAM,
    )
    await _drain()

    assert pub.interims_published == 2


@pytest.mark.asyncio
async def test_sequence_numbers_increase_only_for_published_messages():
    pub, published, _ = _make()
    for i in range(4):
        await pub.process_frame(
            InterimTranscriptionFrame(text=f"p{i}", user_id="u", timestamp=TS),
            FrameDirection.DOWNSTREAM,
        )
    await pub.process_frame(
        TranscriptionFrame(text="done", user_id="u", timestamp=TS),
        FrameDirection.DOWNSTREAM,
    )
    await _drain()

    seqs = [p[2] for p in published]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


@pytest.mark.asyncio
async def test_every_frame_is_still_forwarded_even_when_throttled():
    """Throttling must not swallow frames — the user aggregator is downstream."""
    pub, _, pushed = _make()
    frames = [
        InterimTranscriptionFrame(text="a b c", user_id="u", timestamp=TS),
        InterimTranscriptionFrame(text="a b c d", user_id="u", timestamp=TS),
        TranscriptionFrame(text="a b c d e", user_id="u", timestamp=TS),
    ]
    for f in frames:
        await pub.process_frame(f, FrameDirection.DOWNSTREAM)
    await _drain()

    assert pushed == frames
