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
    CancelFrame,
    EndFrame,
    Frame,
    MetricsFrame,
    TextFrame,
    TranscriptionFrame,
    TTSStartedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)

_CONSENT_DECLINE_WORDS: frozenset[str] = frozenset({
    "no", "nope", "not okay", "not ok", "don't", "do not", "i don't consent",
    "nahi", "mat karo",
})


async def speak_and_end_call(task, message: str) -> None:
    """Queue a final TTS message, give it time to play out, then end the call.

    Shared by CallLoggerProcessor (end_call_phrases match) and pipeline.py's
    max-duration / silence-timeout watchdogs — all three are "graceful hangup"
    triggers that should behave identically.
    """
    try:
        if message:
            await task.queue_frames([TextFrame(message)])
            # Rough speaking-time estimate (~14 chars/sec) so we don't hang up
            # mid-sentence, clamped to a sane range for very short/long messages.
            estimated_seconds = min(max(len(message) / 14.0, 1.5), 12.0)
            await asyncio.sleep(estimated_seconds)
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

        # Wall-clock timestamp of the last user utterance — read by pipeline.py's
        # silence-timeout watchdog (Call Behavior "Silence Timeout" setting).
        self.last_activity_ts: float = time.time()

        # Set by pipeline.py right after PipelineTask construction — lets this
        # processor end the call directly on an end_call_phrases match.
        self.task = None
        self._end_call_phrases = [
            p.strip().lower() for p in (self._agent_config.get("end_call_phrases") or []) if p and p.strip()
        ]
        self._end_call_message = self._agent_config.get("end_call_message") or "Thank you for calling. Goodbye!"
        self._ending_call = False

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
            if isinstance(frame, TranscriptionFrame) and frame.text:
                await self._on_user_speech(frame.text)

            elif isinstance(frame, TTSStartedFrame):
                self._last_tts_start = time.time()

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
                    asyncio.create_task(speak_and_end_call(self.task, self._consent_decline_message))
            else:
                # Anything else is treated as consent granted — an ambiguous
                # reply shouldn't trap the caller in a re-prompt loop.
                logger.info("Recording consent granted — resuming normal flow.")
                if self._consent_resume_message and self.task is not None:
                    asyncio.create_task(self.task.queue_frames([TextFrame(self._consent_resume_message)]))
            return

        # End-call phrase detection — was previously stored (Call Behavior tab)
        # but never matched against anything the patient actually said.
        if (
            not self._ending_call
            and self.task is not None
            and any(phrase in text_lower for phrase in self._end_call_phrases)
        ):
            self._ending_call = True
            logger.info("End-call phrase matched in utterance: '%s'", text[:80])
            asyncio.create_task(speak_and_end_call(self.task, self._end_call_message))

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
