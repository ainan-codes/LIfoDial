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
    EndFrame,
    Frame,
    MetricsFrame,
    TranscriptionFrame,
    TTSStartedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)


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
    ) -> None:
        super().__init__()

        self._tenant_id = tenant_id
        self._agent_id = agent_id
        self._call_meta = call_meta

        # ── Runtime state ─────────────────────────────────────────────────────
        self._call_record_id: Optional[str] = call_meta.get("call_record_id")
        self._call_start_time: float = time.time()
        self._turn_count: int = 0
        self._transcript: list[dict] = []

        # Latency tracking from Pipecat MetricsFrame
        self._latency_samples: list[float] = []  # total ms per turn (ttfb)

        # Store last TTS start time for response latency calc
        self._last_tts_start: Optional[float] = None

        logger.info(
            "CallLoggerProcessor init | tenant=%s agent=%s call_id=%s",
            tenant_id, agent_id, self._call_record_id,
        )

    # ── FrameProcessor interface ──────────────────────────────────────────────

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Intercept lifecycle frames and log to DB. Always push frame downstream."""
        try:
            if isinstance(frame, TranscriptionFrame) and frame.text:
                await self._on_user_speech(frame.text)

            elif isinstance(frame, TTSStartedFrame):
                self._last_tts_start = time.time()

            elif isinstance(frame, MetricsFrame):
                self._on_metrics(frame)

            elif isinstance(frame, EndFrame):
                # Schedule finalization as background task so we don't delay the EndFrame
                asyncio.create_task(self._finalize_call())

        except Exception as exc:
            # Never let logging errors crash the voice pipeline
            logger.error("CallLoggerProcessor error on frame %s: %s", type(frame).__name__, exc)

        await self.push_frame(frame, direction)

    # ── Internal handlers ─────────────────────────────────────────────────────

    async def _on_user_speech(self, text: str) -> None:
        """Record each user utterance in the in-memory transcript."""
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
        Write final call stats to DB and trigger background jobs.
        Called once on EndFrame — all DB writes are fire-and-forget.
        """
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

        # DB finalization
        asyncio.create_task(
            _finalize_call_record(
                call_record_id=self._call_record_id,
                duration_seconds=duration_seconds,
                turn_count=self._turn_count,
                avg_latency_ms=avg_latency_ms,
                transcript=self._transcript,
            )
        )

        # Credit deduction
        if self._tenant_id:
            asyncio.create_task(
                _deduct_call_credits(
                    tenant_id=self._tenant_id,
                    duration_seconds=duration_seconds,
                    call_record_id=self._call_record_id,
                )
            )

        # Post-call Gemini evaluation (non-blocking)
        if self._call_record_id:
            asyncio.create_task(
                _run_post_call_evaluation(self._call_record_id)
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


async def _run_post_call_evaluation(call_record_id: str) -> None:
    """Run Gemini post-call evaluation in the background."""
    try:
        from backend.db import AsyncSessionLocal
        from backend.services.call_evaluator import evaluate_call

        async with AsyncSessionLocal() as db:
            await evaluate_call(call_record_id, db)

        logger.info("Post-call evaluation completed for call %s", call_record_id)
    except Exception as exc:
        logger.error("_run_post_call_evaluation error: %s", exc, exc_info=True)
