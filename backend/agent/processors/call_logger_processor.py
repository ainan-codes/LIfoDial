"""
backend/agent/processors/call_logger_processor.py

Pipecat FrameProcessor for call lifecycle logging.

Handles:
  - Creating a CallRecord in PostgreSQL when the call starts
  - Incrementing turn count and capturing transcript on each user utterance
  - Writing final stats (duration, avg latency, turn count, transcript) on call end
  - Triggering credit deduction after the call ends (background task)
  - Triggering Gemini post-call evaluation (background task)

This processor is transparent — every frame is pushed downstream unchanged.
All DB writes are async and non-blocking.
"""

import asyncio
import logging
import time
import uuid
from typing import Optional

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    MetricsFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)

_CONSENT_DECLINE_WORDS: frozenset[str] = frozenset({
    "no", "nope", "not okay", "not ok", "don't", "do not", "i don't consent",
    "nahi", "mat karo",
})

# ── Closing-intent detection ──────────────────────────────────────────────────
# The agent's configured End Call Phrases stay the source of truth; this list
# EXTENDS them with natural closing language a caller actually uses, because no
# clinic admin will ever enumerate every way a person says goodbye.
#
# Deliberately EXCLUDED: bare "thank you" / "thanks" / "dhanyavaad" / "shukriya".
# Callers thank the agent constantly mid-conversation ("thanks, and what time is
# the doctor free?") — treating that as a hangup would cut live calls.
#
# English + Hindi/Hinglish/Urdu only, i.e. the languages actually seen on these
# calls. Other languages should be added through the End Call Phrases setting
# rather than guessed at here: a wrong phrase in a language nobody verified is a
# call that hangs up on a patient mid-sentence.
_BUILTIN_CLOSING_PHRASES: tuple[str, ...] = (
    "bye", "bye bye", "goodbye", "good bye",
    "alvida", "khuda hafiz", "phir milenge",
    "that's all", "thats all", "that's it", "thats it",
    "nothing else", "no more questions",
    "i'm done", "im done", "we're done", "were done",
    "talk to you later", "call you later",
    "bas itna hi", "bas yahi",
)

# Words that turn a "bye" into something that is NOT a farewell. "by the way" is
# the one that matters: STT routinely transcribes it as "bye the way".
_CLOSING_TRAP_FOLLOWERS: frozenset[str] = frozenset({"the", "then"})

# How close to the end of the utterance a phrase must sit to count as closing
# intent, and how short an utterance may be to count anywhere in it.
_CLOSING_TAIL_WORDS = 3
_CLOSING_SHORT_UTTERANCE_WORDS = 5


def _normalise_for_matching(text: str) -> list[str]:
    """Lowercase, strip punctuation, split into words."""
    cleaned = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in text.lower())
    return cleaned.split()


def is_closing_utterance(text: str, configured_phrases: list[str] | None = None) -> bool:
    """True when ``text`` reads as an actual goodbye, not a passing mention.

    Positional rule — a phrase counts only when it is either:

      * inside the last ``_CLOSING_TAIL_WORDS`` words of the utterance (people
        say goodbye at the END of what they are saying), or
      * anywhere inside a short utterance (<= ``_CLOSING_SHORT_UTTERANCE_WORDS``
        words), which is what a bare "ok bye" or "theek hai bye" looks like.

    This is what stops "I don't want to say goodbye before I book the slot" or a
    mid-sentence "bye the way" from hanging up on a live caller, while still
    catching "haan theek hai, bye". Questions are also rejected outright: an
    utterance ending in "?" is asking for something, not leaving.
    """
    if not text or not text.strip():
        return False

    if text.strip().endswith("?"):
        return False

    words = _normalise_for_matching(text)
    if not words:
        return False

    phrases = [p.strip().lower() for p in (configured_phrases or []) if p and p.strip()]
    phrases.extend(_BUILTIN_CLOSING_PHRASES)

    total = len(words)
    tail_start = max(0, total - _CLOSING_TAIL_WORDS)
    short_utterance = total <= _CLOSING_SHORT_UTTERANCE_WORDS

    for phrase in phrases:
        p_words = _normalise_for_matching(phrase)
        if not p_words:
            continue
        span = len(p_words)
        for i in range(total - span + 1):
            if words[i:i + span] != p_words:
                continue
            # "bye the way" / "bye then ..." → not a farewell.
            following = words[i + span] if i + span < total else ""
            if span == 1 and following in _CLOSING_TRAP_FOLLOWERS:
                continue
            if short_utterance or (i + span - 1) >= tail_start:
                return True
    return False


async def speak_and_end_call(task, message: str, wait_playback=None, max_wait: float = 25.0) -> None:
    """Queue a final TTS message, wait for it to actually finish, end the call.

    Shared by CallLoggerProcessor (closing-intent match) and pipeline.py's
    max-duration / silence-timeout watchdogs — all of them are "graceful hangup"
    triggers that should behave identically.

    Args:
        task: The PipelineTask to cancel once the goodbye has been heard.
        message: What to say before hanging up. May be empty (hang up silently).
        wait_playback: Optional zero-arg coroutine function that resolves when the
            bot's audio has genuinely finished playing —
            ``CallLoggerProcessor.wait_playback_complete``. When omitted, falls
            back to a character-count estimate.
        max_wait: Upper bound on the wait, so a TTS failure can never leave the
            call open forever.

    The old implementation slept on a ~14-chars/second ESTIMATE, which cuts long
    goodbyes off mid-word and lingers pointlessly after short ones. The real
    signal is BotStoppedSpeakingFrame, which pipecat emits from the output
    transport's audio task in queue order — i.e. after the audio has actually
    drained, not when synthesis was requested.
    """
    try:
        if message:
            # TTSSpeakFrame, not TextFrame — only TTSSpeakFrame is synthesized as a
            # standalone utterance outside an LLM response turn.
            await task.queue_frames([TTSSpeakFrame(message, append_to_context=False)])

            if wait_playback is not None:
                finished = await wait_playback(timeout=max_wait)
                if not finished:
                    logger.warning(
                        "Goodbye playback did not report completion within %.0fs — "
                        "ending the call anyway.", max_wait,
                    )
            else:
                # No playback signal available (e.g. a caller-side unit test):
                # fall back to the old rough estimate.
                await asyncio.sleep(min(max(len(message) / 14.0, 1.5), 12.0))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("Failed to queue end-of-call message: %s", exc)
    finally:
        await task.cancel()


class CallLoggerProcessor(FrameProcessor):
    """
    Transparent FrameProcessor that logs the full call lifecycle to PostgreSQL.

    Constructor args:
        tenant_id (str): UUID of the clinic tenant.
        agent_id (str): UUID of the AgentConfig record.
        call_meta (dict): Call metadata from LiveKit room (caller_phone, etc.)
    """

    def __init__(
        self,
        tenant_id: str,
        agent_id: Optional[str],
        call_meta: dict,
        agent_config: Optional[dict] = None,
    ) -> None:
        super().__init__()

        self._tenant_id = tenant_id
        self._agent_id = agent_id
        self._call_meta = call_meta
        self._agent_config = agent_config or {}

        # ── Runtime state ─────────────────────────────────────────────────────
        self._call_record_id: Optional[str] = call_meta.get("call_record_id")
        self._call_start_time: float = time.time()
        self._turn_count: int = 0
        self._transcript: list[dict] = []

        # Wall-clock timestamp from which caller silence is measured — read by
        # pipeline.py's silence-timeout watchdog (Call Behavior "Silence Timeout").
        self.last_activity_ts: float = time.time()

        # ── Agent playback state (FIX 1 + agent-initiated hangup) ─────────────
        # True between BotStartedSpeakingFrame and BotStoppedSpeakingFrame, which
        # the output transport pushes BOTH downstream and upstream — this
        # processor sits upstream of transport.output() and receives the upstream
        # copy. BotStoppedSpeakingFrame is emitted from the transport's audio task
        # in queue order, so it means "the audio actually drained", not "synthesis
        # was requested".
        #
        # The silence watchdog must not count while this is True: the caller is
        # not silent, they are listening. A 20s answer with a 20s timeout used to
        # hang up on a caller who had done nothing wrong.
        self.bot_speaking: bool = False
        self._playback_complete = asyncio.Event()
        self._playback_complete.set()  # nothing is playing at construction

        # Text of the agent utterance currently being spoken, accumulated from
        # TTSTextFrames so a closing phrase can be recognised in the agent's OWN
        # words (the LLM says "Thank you for calling. Goodbye!" of its own accord —
        # that is what left the reported call open).
        self._agent_utterance: list[str] = []

        # Set by pipeline.py right after PipelineTask construction — lets this
        # processor end the call directly on an end_call_phrases match.
        self.task = None
        self._end_call_phrases = [
            p.strip().lower() for p in (self._agent_config.get("end_call_phrases") or []) if p and p.strip()
        ]
        self._end_call_message = self._agent_config.get("end_call_message") or "Thank you for calling. Goodbye!"
        self._ending_call = False
        # The configured message is itself a closing phrase for agent-side
        # matching — if the LLM speaks it, the call is over.
        self._agent_closing_phrases = self._end_call_phrases + [self._end_call_message.strip().lower()]

        # Recording Consent Plan ("require" mode) — set via begin_consent_gate()
        # from pipeline.py once the consent question has been asked.
        self._consent_pending = False
        self._consent_decline_message: str = ""
        self._consent_resume_message: Optional[str] = None

        # Latency tracking from Pipecat MetricsFrame.
        # MUST live in __init__: these were previously (incorrectly) initialized
        # inside begin_consent_gate(), so every call whose consent plan wasn't
        # "require" hit AttributeError in _on_metrics/_finalize_call and never
        # finalized duration/transcript/latency (audit FIX 3).
        self._latency_samples: list[float] = []  # total ms per turn (ttfb)

        # Store last TTS start time for response latency calc
        self._last_tts_start: Optional[float] = None

        # Finalize exactly once, whether the call ends via EndFrame (graceful)
        # or CancelFrame (caller hangup → task.cancel()). Keying only on EndFrame
        # meant a real hangup never finalized the CallRecord (audit FIX 3).
        self._finalized: bool = False
        # Finalization runs as a task (so the End/Cancel frame is NOT blocked
        # from propagating — blocking it stalls pipeline teardown). The
        # entrypoint awaits wait_finalized() in its finally so the job process
        # stays alive until the write actually lands.
        self._finalize_task = None

        logger.info(
            "CallLoggerProcessor init | tenant=%s agent=%s call_id=%s",
            tenant_id, agent_id, self._call_record_id,
        )

    def begin_consent_gate(self, decline_message: str, resume_message: Optional[str]) -> None:
        """Start gating on the patient's answer to the recording-consent question.
        The next utterance is treated as the yes/no answer instead of normal
        conversation (booking, end-call phrases, etc. are skipped for it)."""
        self._consent_pending = True
        self._consent_decline_message = decline_message
        self._consent_resume_message = resume_message

    # ── FrameProcessor interface ──────────────────────────────────────────────

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Intercept lifecycle frames and log to DB. Always push frame downstream."""
        # REQUIRED first (pipecat 1.5): handle system frames + mark started.
        await super().process_frame(frame, direction)
        try:
            # NOTE: user transcriptions do NOT arrive here. This processor sits
            # after `tts` (it needs TTSStartedFrame / MetricsFrame, which only
            # exist downstream of the TTS service), and context_aggregator.user()
            # — which is upstream of it — CONSUMES TranscriptionFrame without
            # pushing it downstream (pipecat 1.5.0 llm_response_universal.py
            # :794-799). So this processor never saw a single user utterance:
            # turn_count stayed 0 and the transcript stayed empty on every call.
            # User speech now arrives via note_user_utterance(), called by the
            # tap that pipeline.py places between `stt` and the aggregator — the
            # same placement fix the transcript publisher needed.
            if isinstance(frame, TTSStartedFrame):
                self._last_tts_start = time.time()
                self._agent_utterance = []

            elif isinstance(frame, TTSTextFrame):
                # Accumulate what the agent is about to say. Sarvam pushes one
                # TTSTextFrame per aggregated sentence, so a multi-sentence answer
                # arrives as a few frames.
                text = (getattr(frame, "text", "") or "").strip()
                if text:
                    self._agent_utterance.append(text)

            elif isinstance(frame, BotStartedSpeakingFrame):
                self.bot_speaking = True
                self._playback_complete.clear()

            elif isinstance(frame, BotStoppedSpeakingFrame):
                # Real end of playback. Caller silence is measured from HERE — not
                # from when the agent started talking.
                self.bot_speaking = False
                self.last_activity_ts = time.time()
                self._playback_complete.set()
                await self._maybe_end_after_agent_goodbye()

            elif isinstance(frame, MetricsFrame):
                self._on_metrics(frame)

            elif isinstance(frame, (EndFrame, CancelFrame)):
                # Kick off finalization WITHOUT blocking the frame (a blocked
                # End/Cancel frame stalls pipeline teardown). The entrypoint
                # awaits wait_finalized() so the process outlives this task.
                # Handles BOTH graceful end and hangup-cancel.
                if self._finalize_task is None:
                    self._finalize_task = asyncio.create_task(self._finalize_call())

        except Exception as exc:
            # Never let logging errors crash the voice pipeline
            logger.error("CallLoggerProcessor error on frame %s: %s", type(frame).__name__, exc)

        await self.push_frame(frame, direction)

    async def wait_finalized(self, timeout: float = 10.0) -> bool:
        """Await the finalization write. Called from the entrypoint's finally so
        the job process doesn't exit before duration/transcript/latency persist.
        If no End/Cancel frame was ever seen (hard teardown), finalize inline as
        a last resort. Returns True if finalization completed within `timeout`."""
        if self._finalize_task is None:
            await self._finalize_call()
            return True
        try:
            await asyncio.wait_for(asyncio.shield(self._finalize_task), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.warning("Finalization did not complete within %.0fs.", timeout)
            return False

    async def wait_playback_complete(self, timeout: float = 25.0) -> bool:
        """Await the end of the agent's current audio playback.

        Returns True if playback finished (or nothing was playing), False on
        timeout. Used by speak_and_end_call so a goodbye is never cut off
        mid-word and the call never lingers after it.
        """
        try:
            await asyncio.wait_for(self._playback_complete.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def note_user_utterance(self, text: str) -> None:
        """Record a finalised user utterance. Called by the pipeline's transcript
        tap, which sits where TranscriptionFrames actually exist (between `stt`
        and the user aggregator) — see the note in process_frame()."""
        await self._on_user_speech(text)

    async def _maybe_end_after_agent_goodbye(self) -> None:
        """Hang up when the AGENT just finished saying goodbye.

        This is the reported bug: the LLM produced "Thank you for calling.
        Goodbye!" on its own and the call stayed open, so the caller carried on
        talking to an agent that had already signed off. Playback has genuinely
        finished by the time this runs (BotStoppedSpeakingFrame), so the call can
        end immediately without waiting for any further caller input.

        Skipped when _ending_call is already set: in that case speak_and_end_call
        owns the hangup and is about to cancel the task itself — reacting here as
        well would cancel twice.
        """
        if self._ending_call or self.task is None:
            return

        spoken = " ".join(self._agent_utterance).strip()
        self._agent_utterance = []
        if not spoken or not is_closing_utterance(spoken, self._agent_closing_phrases):
            return

        self._ending_call = True
        logger.info("Agent said goodbye ('%s') — ending call now.", spoken[:80])
        # Playback already drained, so no closing message to speak: just end.
        asyncio.create_task(self.task.cancel())

    # ── Internal handlers ─────────────────────────────────────────────────────

    async def _on_user_speech(self, text: str) -> None:
        """Record each user utterance in the in-memory transcript."""
        self.last_activity_ts = time.time()
        self._turn_count += 1

        entry = {
            "turn": self._turn_count,
            "role": "user",
            "text": text,
            "timestamp": time.time(),
        }
        self._transcript.append(entry)

        logger.info(
            "Turn %d | Patient: %s",
            self._turn_count,
            text[:80] + ("..." if len(text) > 80 else ""),
        )

        # Persist turn count incrementally so partial data survives crashes
        if self._call_record_id:
            asyncio.create_task(
                _update_call_record_turns(self._call_record_id, self._turn_count)
            )

        text_lower = text.lower().strip()

        # Recording Consent Plan ("require" mode) — this utterance IS the
        # yes/no answer to the consent question; don't let booking or
        # end-call-phrase logic see it as normal conversation.
        if self._consent_pending:
            self._consent_pending = False
            if any(w in text_lower for w in _CONSENT_DECLINE_WORDS):
                self._ending_call = True
                logger.info("Recording consent declined — ending call politely.")
                if self.task is not None:
                    asyncio.create_task(
                        speak_and_end_call(
                            self.task,
                            self._consent_decline_message,
                            wait_playback=self.wait_playback_complete,
                        )
                    )
            else:
                # Anything else is treated as consent granted — an ambiguous
                # reply shouldn't trap the caller in a re-prompt loop.
                logger.info("Recording consent granted — resuming normal flow.")
                if self._consent_resume_message and self.task is not None:
                    asyncio.create_task(self.task.queue_frames([
                        TTSSpeakFrame(self._consent_resume_message, append_to_context=False)
                    ]))
            return

        # Caller-initiated hangup. The configured End Call Phrases remain the
        # source of truth; is_closing_utterance() extends them with natural
        # closing language ("ok bye", "theek hai bye", "that's all") and applies
        # the positional guard that keeps a mid-sentence "bye" from ending a live
        # call. The previous check was a bare substring test over the configured
        # phrases only, so "bye the way" would have hung up and "ok bye" would not.
        if (
            not self._ending_call
            and self.task is not None
            and is_closing_utterance(text, self._end_call_phrases)
        ):
            self._ending_call = True
            logger.info("Caller said goodbye ('%s') — ending call.", text[:80])
            asyncio.create_task(
                speak_and_end_call(
                    self.task,
                    self._end_call_message,
                    wait_playback=self.wait_playback_complete,
                )
            )

    def _on_metrics(self, frame: MetricsFrame) -> None:
        """Capture TTFB (time-to-first-byte) latency from Pipecat's MetricsFrame."""
        try:
            # MetricsFrame.data is a list of Metric objects with .name and .value
            for metric in getattr(frame, "data", []):
                name = getattr(metric, "name", "")
                value = getattr(metric, "value", None)
                if "ttfb" in name.lower() and value is not None:
                    self._latency_samples.append(float(value) * 1000)  # Convert s → ms
        except Exception as exc:
            logger.debug("MetricsFrame parse error (non-critical): %s", exc)

    async def _finalize_call(self) -> None:
        """
        Write final call stats to DB and trigger background jobs. Runs exactly
        once (EndFrame or CancelFrame). The core record write and credit
        deduction are AWAITED so they survive job teardown; only the slow,
        external post-call Gemini evaluation stays a background task.
        """
        if self._finalized:
            return
        self._finalized = True

        if not self._call_record_id:
            logger.info("No call_record_id — skipping finalization.")
            return

        duration_seconds = int(time.time() - self._call_start_time)
        avg_latency_ms: Optional[float] = (
            sum(self._latency_samples) / len(self._latency_samples)
            if self._latency_samples else None
        )

        logger.info(
            "Call ended | id=%s duration=%ds turns=%d avg_latency=%.0fms",
            self._call_record_id,
            duration_seconds,
            self._turn_count,
            avg_latency_ms or 0,
        )

        # Core record write — AWAITED (not fire-and-forget) so duration/turns/
        # transcript/latency/status actually persist before teardown.
        await _finalize_call_record(
            call_record_id=self._call_record_id,
            duration_seconds=duration_seconds,
            turn_count=self._turn_count,
            avg_latency_ms=avg_latency_ms,
            transcript=self._transcript,
        )

        # Credit deduction — AWAITED (billing correctness).
        if self._tenant_id:
            await _deduct_call_credits(
                tenant_id=self._tenant_id,
                duration_seconds=duration_seconds,
                call_record_id=self._call_record_id,
            )

        # Post-call Gemini evaluation — slow + external, keep in the background
        # (gated on the Analysis tab toggles). May not finish on abrupt teardown;
        # that's acceptable, the core record is already persisted above.
        summary_on = bool(self._agent_config.get("summary_enabled", True))
        eval_on = bool(self._agent_config.get("success_evaluation_enabled", True))
        if self._call_record_id and (summary_on or eval_on):
            asyncio.create_task(
                _run_post_call_evaluation(self._call_record_id, summary_on, eval_on)
            )


class UserTranscriptTap(FrameProcessor):
    """Feeds finalised user utterances to a CallLoggerProcessor.

    Exists purely because of frame placement. The logger must sit AFTER `tts` to
    see TTSStartedFrame / TTSTextFrame / MetricsFrame / Bot*SpeakingFrame, but
    TranscriptionFrames never get that far: context_aggregator.user() consumes
    them without pushing downstream. So the logger's turn count and transcript
    were always empty (`turns=0` on every call, including calls with a dozen
    turns).

    This tap sits between `stt` and the aggregator, where the frames exist, and
    hands the text sideways to the logger. Fully transparent — every frame is
    pushed on unchanged, and a logger error can never break the pipeline.
    """

    def __init__(self, call_logger: "CallLoggerProcessor") -> None:
        super().__init__()
        self._call_logger = call_logger

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        # REQUIRED first (pipecat 1.5): handle system frames + mark started.
        await super().process_frame(frame, direction)

        # Finalised transcriptions only. InterimTranscriptionFrame is a running
        # hypothesis that gets revised, so counting it would inflate turn_count
        # and fill the transcript with half-words.
        if isinstance(frame, TranscriptionFrame) and (frame.text or "").strip():
            try:
                await self._call_logger.note_user_utterance(frame.text)
            except Exception as exc:
                logger.error("UserTranscriptTap: logging utterance failed: %s", exc)

        await self.push_frame(frame, direction)


# ── Background DB helpers ──────────────────────────────────────────────────────
# All functions below run as asyncio.create_task() — they never block the voice call.

async def _update_call_record_turns(call_record_id: str, turn_count: int) -> None:
    """Incrementally update turn count on the CallRecord row."""
    try:
        from backend.db import AsyncSessionLocal
        from backend.models.call_record import CallRecord
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(CallRecord).where(CallRecord.id == call_record_id)
            )
            record = result.scalar_one_or_none()
            if record:
                record.turn_count = turn_count
                await db.commit()
    except Exception as exc:
        logger.debug("_update_call_record_turns error (non-critical): %s", exc)


async def _finalize_call_record(
    call_record_id: str,
    duration_seconds: int,
    turn_count: int,
    avg_latency_ms: Optional[float],
    transcript: list[dict],
) -> None:
    """Write final call stats, status, and transcript to the CallRecord row."""
    try:
        import json
        from datetime import datetime, timezone

        from backend.db import AsyncSessionLocal
        from backend.models.call_record import CallRecord
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(CallRecord).where(CallRecord.id == call_record_id)
            )
            record = result.scalar_one_or_none()
            if not record:
                logger.warning("CallRecord %s not found — cannot finalize.", call_record_id)
                return

            record.ended_at = datetime.now(timezone.utc)
            record.duration_seconds = duration_seconds
            record.turn_count = turn_count
            record.avg_latency_ms = int(avg_latency_ms) if avg_latency_ms else None
            record.status = "completed"
            record.transcript = transcript  # JSON column or TEXT depending on model

            await db.commit()
            logger.info("CallRecord %s finalized.", call_record_id)

    except Exception as exc:
        logger.error("_finalize_call_record error: %s", exc, exc_info=True)


async def _deduct_call_credits(
    tenant_id: str,
    duration_seconds: int,
    call_record_id: Optional[str],
) -> None:
    """Deduct per-minute credits from clinic balance after call ends."""
    try:
        from backend.db import AsyncSessionLocal
        from backend.services.credit_service import CreditService

        async with AsyncSessionLocal() as db:
            result = await CreditService.deduct_call_credits(
                db,
                tenant_id=tenant_id,
                duration_seconds=duration_seconds,
                call_id=call_record_id,
            )
            await db.commit()

        logger.info(
            "Credit deduction: tenant=%s deducted=₹%.2f balance=₹%.2f duration=%ds",
            tenant_id,
            result.get("deducted", 0),
            result.get("balance_after", 0),
            duration_seconds,
        )
    except Exception as exc:
        logger.error("_deduct_call_credits error: %s", exc, exc_info=True)


async def _run_post_call_evaluation(call_record_id: str, summary_enabled: bool = True, eval_enabled: bool = True) -> None:
    """Run Gemini post-call evaluation in the background."""
    try:
        from backend.db import AsyncSessionLocal
        from backend.services.call_evaluator import evaluate_call

        async with AsyncSessionLocal() as db:
            await evaluate_call(call_record_id, db, summary_enabled=summary_enabled, eval_enabled=eval_enabled)

        logger.info("Post-call evaluation completed for call %s", call_record_id)
    except Exception as exc:
        logger.error("_run_post_call_evaluation error: %s", exc, exc_info=True)
