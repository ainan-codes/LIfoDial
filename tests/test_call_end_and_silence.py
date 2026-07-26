"""Turn counting, silence-timer gating, and goodbye auto-hangup.

Covers the four defects seen on a real call:
  * turns=0 logged on calls that clearly had turns
  * the silence timer counting the agent's own TTS playback as caller silence
  * the agent saying "Thank you for calling. Goodbye!" without the call ending
  * no caller-side goodbye detection at all
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_call_end.db")

import asyncio

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    TranscriptionFrame,
    TTSStartedFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from backend.agent.processors.call_logger_processor import (
    CallLoggerProcessor,
    UserTranscriptTap,
    is_closing_utterance,
    speak_and_end_call,
)

TS = "2026-07-26T00:00:00Z"
DOWN = FrameDirection.DOWNSTREAM


# ── Closing-intent detection ──────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "bye",
    "ok bye",
    "goodbye",
    "theek hai bye",
    "haan theek hai, bye bye",
    "that's all, thanks",
    "nothing else",
    "alvida",
    "ok doctor, talk to you later",
    "Thank you for calling. Goodbye!",     # the agent's own configured message
])
def test_closing_utterances_are_detected(text):
    assert is_closing_utterance(text) is True


@pytest.mark.parametrize("text", [
    # The trap that matters: STT writes "by the way" as "bye the way".
    "bye the way can you check tomorrow morning also for me",
    # Goodbye mentioned mid-sentence with no closing intent.
    "I don't want to say goodbye before the appointment is confirmed please",
    # A question is a request, never a farewell.
    "Aap English mein baat kar sakte ho kya?",
    "should I say bye to the doctor when I arrive there or not?",
    # Thanks alone is NOT closing intent — callers thank the agent constantly.
    "thank you",
    "thanks",
    "dhanyavaad",
    "thanks, and what time is Dr Sharma free on Monday",
    "",
    "   ",
])
def test_non_closing_utterances_are_not_detected(text):
    assert is_closing_utterance(text) is False


def test_configured_phrases_are_honoured():
    assert is_closing_utterance("kaam ho gaya", ["kaam ho gaya"]) is True
    assert is_closing_utterance("kaam ho gaya") is False, "not a built-in phrase"


def test_configured_phrase_still_obeys_the_positional_guard():
    """A configured phrase buried mid-sentence must not end a live call."""
    long_sentence = "kaam ho gaya matlab appointment confirm hai ya main phir se try karun batao"
    assert is_closing_utterance(long_sentence, ["kaam ho gaya"]) is False


# ── Turn counting (turns=0) ───────────────────────────────────────────────────

def _logger(**config):
    return CallLoggerProcessor(
        tenant_id="", agent_id=None, call_meta={}, agent_config=config,
    )


async def _push(proc, frame):
    pushed = []

    async def capture(f, direction=DOWN):
        pushed.append(f)

    proc.push_frame = capture  # type: ignore[method-assign]
    await proc.process_frame(frame, DOWN)
    return pushed


@pytest.mark.asyncio
async def test_transcription_frames_do_not_reach_the_logger_but_the_tap_counts_them():
    """The regression that produced turns=0: the logger sits downstream of the
    user aggregator, which eats TranscriptionFrames. The tap is the fix."""
    log = _logger()
    tap = UserTranscriptTap(log)

    pushed = []

    async def capture(f, direction=DOWN):
        pushed.append(f)

    tap.push_frame = capture  # type: ignore[method-assign]

    frame = TranscriptionFrame(text="mujhe appointment chahiye", user_id="u", timestamp=TS)
    await tap.process_frame(frame, DOWN)

    assert log._turn_count == 1
    assert log._transcript[0]["text"] == "mujhe appointment chahiye"
    assert pushed == [frame], "the tap must forward the frame unchanged"


@pytest.mark.asyncio
async def test_multiple_turns_are_counted():
    log = _logger()
    tap = UserTranscriptTap(log)
    tap.push_frame = lambda *a, **k: asyncio.sleep(0)  # type: ignore[method-assign]

    for text in ("hello", "monday please", "10 am works"):
        await tap.process_frame(
            TranscriptionFrame(text=text, user_id="u", timestamp=TS), DOWN
        )
    assert log._turn_count == 3
    assert [e["turn"] for e in log._transcript] == [1, 2, 3]


@pytest.mark.asyncio
async def test_blank_transcriptions_are_not_counted_as_turns():
    log = _logger()
    tap = UserTranscriptTap(log)
    tap.push_frame = lambda *a, **k: asyncio.sleep(0)  # type: ignore[method-assign]

    await tap.process_frame(TranscriptionFrame(text="   ", user_id="u", timestamp=TS), DOWN)
    assert log._turn_count == 0


# ── FIX 1: silence timer must not count agent playback ────────────────────────

@pytest.mark.asyncio
async def test_bot_speaking_flag_tracks_real_playback_frames():
    log = _logger()
    assert log.bot_speaking is False

    await _push(log, BotStartedSpeakingFrame())
    assert log.bot_speaking is True, "timer must be paused while the agent speaks"

    await _push(log, BotStoppedSpeakingFrame())
    assert log.bot_speaking is False


@pytest.mark.asyncio
async def test_playback_completion_resets_the_silence_clock():
    """The countdown must start when the agent STOPS, not when it starts."""
    log = _logger()
    await _push(log, BotStartedSpeakingFrame())
    log.last_activity_ts -= 60          # pretend a long answer just played
    await _push(log, BotStoppedSpeakingFrame())

    import time as _t
    assert _t.time() - log.last_activity_ts < 1.0


@pytest.mark.asyncio
async def test_wait_playback_complete_blocks_until_playback_ends():
    log = _logger()
    await _push(log, BotStartedSpeakingFrame())

    assert await log.wait_playback_complete(timeout=0.05) is False, "still playing"

    await _push(log, BotStoppedSpeakingFrame())
    assert await log.wait_playback_complete(timeout=0.05) is True


@pytest.mark.asyncio
async def test_silence_watchdog_does_not_fire_while_the_agent_is_speaking():
    """Drives the real watchdog with a 1s timeout while bot_speaking is True."""
    from backend.agent.pipeline import _enforce_silence_timeout

    class FakeTask:
        cancelled = False

        async def cancel(self):
            self.cancelled = True

        async def queue_frames(self, frames):
            pass

    log = _logger()
    log.bot_speaking = True
    log.last_activity_ts -= 30          # would have long since timed out
    task = FakeTask()

    watchdog = asyncio.create_task(_enforce_silence_timeout(task, log, 1, "Goodbye!"))
    await asyncio.sleep(2.5)            # several watchdog ticks
    watchdog.cancel()

    assert task.cancelled is False, "playback was misread as caller silence"


@pytest.mark.asyncio
async def test_silence_watchdog_fires_once_the_agent_has_finished():
    from backend.agent.pipeline import _enforce_silence_timeout

    class FakeTask:
        cancelled = False

        async def cancel(self):
            self.cancelled = True

        async def queue_frames(self, frames):
            pass

    log = _logger()
    log.bot_speaking = False
    log.last_activity_ts -= 30
    task = FakeTask()

    watchdog = asyncio.create_task(_enforce_silence_timeout(task, log, 1, ""))
    await asyncio.sleep(2.5)
    watchdog.cancel()

    assert task.cancelled is True


# ── FIX 3: auto-hangup ────────────────────────────────────────────────────────

class _RecordingTask:
    def __init__(self):
        self.cancelled = False
        self.spoken: list[str] = []

    async def cancel(self):
        self.cancelled = True

    async def queue_frames(self, frames):
        for f in frames:
            self.spoken.append(getattr(f, "text", ""))


@pytest.mark.asyncio
async def test_agent_goodbye_ends_the_call_after_playback():
    """The reported bug: agent says goodbye, call stays open."""
    log = _logger()
    task = _RecordingTask()
    log.task = task

    await _push(log, TTSStartedFrame())
    await _push(log, TTSTextFrame(aggregated_by="sentence", text="Thank you for calling. Goodbye!"))
    assert task.cancelled is False, "must not hang up mid-sentence"

    await _push(log, BotStoppedSpeakingFrame())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert task.cancelled is True


@pytest.mark.asyncio
async def test_ordinary_agent_reply_does_not_end_the_call():
    log = _logger()
    task = _RecordingTask()
    log.task = task

    await _push(log, TTSStartedFrame())
    await _push(log, TTSTextFrame(aggregated_by="sentence", text="Dr Sharma is free at 10 am and 4 pm on Monday."))
    await _push(log, BotStoppedSpeakingFrame())
    await asyncio.sleep(0)
    assert task.cancelled is False


@pytest.mark.asyncio
async def test_agent_utterance_resets_between_turns():
    """A goodbye in turn 1 must not linger and end the call after turn 2."""
    log = _logger()
    task = _RecordingTask()
    log.task = task

    await _push(log, TTSStartedFrame())
    await _push(log, TTSTextFrame(aggregated_by="sentence", text="Hello, how can I help you today?"))
    await _push(log, BotStoppedSpeakingFrame())
    await asyncio.sleep(0)

    await _push(log, TTSStartedFrame())
    await _push(log, TTSTextFrame(aggregated_by="sentence", text="Monday at 10 am is booked."))
    await _push(log, BotStoppedSpeakingFrame())
    await asyncio.sleep(0)
    assert task.cancelled is False


@pytest.mark.asyncio
async def test_caller_goodbye_speaks_closing_message_then_ends():
    log = _logger(end_call_message="Thank you for calling. Goodbye!")
    task = _RecordingTask()
    log.task = task

    await log.note_user_utterance("ok theek hai bye")
    # The hangup runs as a task; let it queue the message, then report playback done.
    await asyncio.sleep(0.05)
    assert task.spoken == ["Thank you for calling. Goodbye!"]

    log._playback_complete.set()
    await asyncio.sleep(0.05)
    assert task.cancelled is True


@pytest.mark.asyncio
async def test_caller_mid_sentence_bye_does_not_end_the_call():
    log = _logger()
    task = _RecordingTask()
    log.task = task

    await log.note_user_utterance("bye the way is Dr Sharma available on Tuesday as well")
    await asyncio.sleep(0.05)
    assert task.cancelled is False
    assert task.spoken == []


@pytest.mark.asyncio
async def test_hangup_happens_only_once():
    log = _logger()
    task = _RecordingTask()
    log.task = task

    await log.note_user_utterance("bye")
    await log.note_user_utterance("bye")          # duplicate final transcript
    await asyncio.sleep(0.05)
    assert len(task.spoken) == 1


@pytest.mark.asyncio
async def test_speak_and_end_call_waits_for_real_playback_not_an_estimate():
    task = _RecordingTask()
    gate = asyncio.Event()

    async def wait_playback(timeout=25.0):
        await gate.wait()
        return True

    runner = asyncio.create_task(
        speak_and_end_call(task, "Goodbye and take care!", wait_playback=wait_playback)
    )
    await asyncio.sleep(0.05)
    assert task.spoken == ["Goodbye and take care!"]
    assert task.cancelled is False, "must wait for playback to finish"

    gate.set()
    await runner
    assert task.cancelled is True


@pytest.mark.asyncio
async def test_speak_and_end_call_still_ends_if_playback_never_reports():
    """A TTS failure must not leave the call open forever."""
    task = _RecordingTask()

    async def never(timeout=25.0):
        return False

    await speak_and_end_call(task, "Goodbye!", wait_playback=never)
    assert task.cancelled is True
