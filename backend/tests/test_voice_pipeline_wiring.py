"""
Regression guards for the two wiring bugs that made every LiveKit web call
completely silent (agent joined the room, published an audio track, never spoke).

These are cheap structural/behavioural checks rather than a full pipeline run,
because the failure mode is silent: nothing errors, no exception is raised, the
caller simply hears nothing. Both bugs survived several rounds of fixes precisely
because nothing failed loudly.
"""
from pathlib import Path

import pytest
from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection

_PIPELINE_SRC = (
    Path(__file__).resolve().parents[1] / "agent" / "pipeline.py"
).read_text(encoding="utf-8")


def test_assistant_aggregator_is_last_in_pipeline():
    """context_aggregator.assistant() MUST come after transport.output().

    LLMAssistantAggregator consumes LLMFullResponse*/TextFrame frames without
    forwarding them (it buffers them to build the assistant context message).
    Placing it between `llm` and `tts` swallows the entire LLM response before TTS
    can synthesize it — the agent then never speaks a word on any turn.
    """
    body = _PIPELINE_SRC.split("pipeline = Pipeline([", 1)[1].split("])", 1)[0]
    order = [
        line.split("#")[0].strip().rstrip(",")
        for line in body.splitlines()
        if line.split("#")[0].strip()
    ]
    assert "context_aggregator.assistant()" in order, order
    assert "tts" in order and "transport.output()" in order, order
    assert order.index("context_aggregator.assistant()") > order.index("tts"), (
        "context_aggregator.assistant() must come AFTER tts, or it swallows the "
        f"LLM response and the agent is silent. Current order: {order}"
    )
    assert order.index("context_aggregator.assistant()") > order.index(
        "transport.output()"
    ), f"context_aggregator.assistant() must be last. Current order: {order}"


def test_call_logger_is_downstream_of_tts():
    """call_logger reacts to TTSStartedFrame (idle-clock reset) and the TTS
    MetricsFrame (TTFB) — both are pushed downstream BY tts, so it must sit after
    it or neither ever arrives."""
    body = _PIPELINE_SRC.split("pipeline = Pipeline([", 1)[1].split("])", 1)[0]
    order = [
        line.split("#")[0].strip().rstrip(",")
        for line in body.splitlines()
        if line.split("#")[0].strip()
    ]
    assert order.index("call_logger") > order.index("tts"), (
        f"call_logger must come after tts to see TTSStartedFrame/metrics: {order}"
    )


def test_greeting_uses_tts_speak_frame():
    """The first message must be queued as a TTSSpeakFrame.

    TTSService only synthesizes a bare TextFrame as part of an LLM response turn
    (flushed on LLMFullResponseEndFrame). A TextFrame queued at the task source has
    no surrounding response frames, so it is never spoken.
    """
    assert "TTSSpeakFrame(effective_first_message" in _PIPELINE_SRC, (
        "the greeting must be queued as TTSSpeakFrame, not TextFrame"
    )


@pytest.mark.asyncio
async def test_assistant_aggregator_really_swallows_llm_text():
    """Pin the upstream behaviour this ordering depends on.

    If a future pipecat release makes LLMAssistantAggregator forward these frames,
    this test fails and tells us the ordering constraint has changed.
    """
    pair = LLMContextAggregatorPair(LLMContext(messages=[]))
    assistant = pair.assistant()

    pushed = []

    async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    assistant.push_frame = _capture  # type: ignore[method-assign]

    for frame in (
        LLMFullResponseStartFrame(),
        TextFrame("hello from the LLM"),
        LLMFullResponseEndFrame(),
    ):
        await assistant.process_frame(frame, FrameDirection.DOWNSTREAM)

    assert not any(isinstance(f, TextFrame) for f in pushed), (
        "LLMAssistantAggregator forwarded a TextFrame — the pipeline ordering "
        "constraint documented in pipeline.py may no longer apply; re-verify."
    )


@pytest.mark.asyncio
async def test_tts_speak_frame_is_forwarded_by_assistant_aggregator():
    """Sanity check the escape hatch: TTSSpeakFrame is not swallowed, which is why
    the greeting/end-call/fallback injections use it."""
    pair = LLMContextAggregatorPair(LLMContext(messages=[]))
    assistant = pair.assistant()

    pushed = []

    async def _capture(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    assistant.push_frame = _capture  # type: ignore[method-assign]
    await assistant.process_frame(
        TTSSpeakFrame("greeting", append_to_context=False), FrameDirection.DOWNSTREAM
    )
    assert any(isinstance(f, TTSSpeakFrame) for f in pushed)
