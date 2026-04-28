"""
backend/services/call_evaluator.py — Automatic Post-Call AI Evaluation
Scores every completed call using Gemini after it ends.
Stores scores directly into call_records.
"""
import json
import logging
import re

import asyncio
import functools

from google import genai as google_genai

from backend.config import settings

logger = logging.getLogger(__name__)


# ── Evaluation criteria reference ─────────────────────────────────────────────

EVALUATION_CRITERIA = {
    "goal_completion": "Did the AI complete the patient's core requested task?",
    "naturalness": "Did the conversation flow naturally, like a human receptionist?",
    "efficiency": "Was the task completed in a reasonable number of turns?",
    "language_accuracy": "Did AI correctly detect and respond in the patient's language?",
    "error_recovery": "Did AI handle unclear or repeated inputs gracefully?",
}


async def evaluate_call(call_record_id: str, db) -> dict:
    """
    Auto-evaluate a completed call using Gemini.
    Reads the transcript from CallRecord, generates scores,
    then stores summary/sentiment/scores back into the record.

    Returns the evaluation dict (or {} on failure).
    """
    from backend.models.call_record import CallRecord
    from sqlalchemy import select, update

    result = await db.execute(
        select(CallRecord).where(CallRecord.id == call_record_id)
    )
    call = result.scalar_one_or_none()
    if not call:
        logger.warning(f"evaluate_call: record {call_record_id} not found")
        return {}

    transcript = call.transcript or []
    if not transcript:
        logger.info(f"evaluate_call: no transcript for {call_record_id} — skipping")
        return {}

    transcript_text = "\n".join(
        f"{m.get('role', '?').upper()}: {m.get('text', '')}"
        for m in transcript
    )
    duration = call.duration_seconds or 0
    turn_count = len(transcript)

    google_genai_client = google_genai.Client(api_key=settings.gemini_api_key)

    loop = asyncio.get_event_loop()

    async def _generate_eval(prompt: str) -> str:
        try:
            resp = await loop.run_in_executor(
                None,
                functools.partial(
                    google_genai_client.models.generate_content,
                    model="gemini-2.0-flash",
                    contents=prompt,
                )
            )
            return resp.text.strip() if resp.text else ""
        except Exception as e:
            logger.error(f"evaluate_call: Gemini API error for {call_record_id}: {e}")
            return ""

    prompt = f"""
Evaluate this AI clinic receptionist call transcript.
Return ONLY valid JSON (no markdown fences, no extra text).

{{
  "overall_score": 0-100,
  "goal_completion_score": 0-100,
  "naturalness_score": 0-100,
  "efficiency_score": 0-100,
  "language_accuracy_score": 0-100,
  "error_recovery_score": 0-100,
  "outcome": "booked|resolved|unresolved|transferred|abandoned",
  "sentiment": "positive|neutral|negative",
  "summary": "2 sentence summary of what happened and how AI performed",
  "improvement": "one specific, actionable suggestion to improve this agent",
  "booking_successful": true or false,
  "detected_language": "language code like hi-IN or en-IN"
}}

Call info: {duration}s duration, {turn_count} transcript turns

Transcript:
{transcript_text}
"""

    try:
        raw = await _generate_eval(prompt)
        if not raw:
            evaluation = _fallback_evaluation()
        else:
            # Strip any accidental markdown fences
            raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
            evaluation = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"evaluate_call: JSON parse error for {call_record_id}: {e}")
        evaluation = _fallback_evaluation()
    except Exception as e:
        logger.error(f"evaluate_call: Gemini error for {call_record_id}: {e}")
        evaluation = _fallback_evaluation()

    # ── Persist into call_records ──────────────────────────────────────────
    try:
        update_kwargs: dict = {}
        if evaluation.get("summary"):
            update_kwargs["summary"] = evaluation["summary"]
        if evaluation.get("sentiment"):
            update_kwargs["sentiment"] = evaluation["sentiment"]
        if evaluation.get("outcome"):
            update_kwargs["intent_detected"] = evaluation["outcome"]
        if evaluation.get("booking_successful") is not None:
            update_kwargs["booking_successful"] = evaluation["booking_successful"]
        if evaluation.get("detected_language"):
            update_kwargs["detected_language"] = evaluation["detected_language"]
        # Store full evaluation JSON in intent_detected field as fallback
        # (production builds should add an `evaluation_json` column)

        if update_kwargs:
            await db.execute(
                update(CallRecord)
                .where(CallRecord.id == call_record_id)
                .values(**update_kwargs)
            )
            await db.commit()
            logger.info(
                f"evaluate_call: stored evaluation for {call_record_id} "
                f"(score={evaluation.get('overall_score')})"
            )
    except Exception as e:
        logger.error(f"evaluate_call: DB update failed for {call_record_id}: {e}")

    # Attach the record ID to the result for traceability
    evaluation["call_record_id"] = call_record_id
    return evaluation


def _fallback_evaluation() -> dict:
    """Returns a safe default when Gemini evaluation fails."""
    return {
        "overall_score": 0,
        "goal_completion_score": 0,
        "naturalness_score": 0,
        "efficiency_score": 0,
        "language_accuracy_score": 0,
        "error_recovery_score": 0,
        "outcome": "unresolved",
        "sentiment": "neutral",
        "summary": "Evaluation could not be generated.",
        "improvement": "Check API key and transcript availability.",
        "booking_successful": False,
        "detected_language": "unknown",
    }


async def get_agent_evaluation_stats(agent_id: str, db, days: int = 7) -> dict:
    """
    Aggregate evaluation stats across all calls for an agent in the last N days.
    Returns avg scores, booking rate, top issues, etc.
    Used by the Agent Health dashboard tab.
    """
    from backend.models.call_record import CallRecord
    from sqlalchemy import select, and_
    from datetime import datetime, timedelta

    since = datetime.utcnow() - timedelta(days=days)

    result = await db.execute(
        select(CallRecord).where(
            and_(
                CallRecord.agent_id == agent_id,
                CallRecord.status == "completed",
                CallRecord.created_at >= since,
            )
        ).order_by(CallRecord.created_at.desc()).limit(200)
    )
    calls = result.scalars().all()

    if not calls:
        return {
            "agent_id": agent_id,
            "period_days": days,
            "total_calls": 0,
            "avg_score": None,
            "booking_success_rate": None,
            "sentiment_breakdown": {},
            "top_issues": [],
        }

    total = len(calls)
    booked = sum(1 for c in calls if c.booking_successful)
    positive = sum(1 for c in calls if c.sentiment == "positive")
    neutral = sum(1 for c in calls if c.sentiment == "neutral")
    negative = sum(1 for c in calls if c.sentiment == "negative")

    # We don't have individual scores per call without the evaluation_json column
    # Show counts and rates that we DO have stored
    return {
        "agent_id": agent_id,
        "period_days": days,
        "total_calls": total,
        "calls_booked": booked,
        "booking_success_rate": round(booked / total * 100, 1) if total else 0,
        "sentiment_breakdown": {
            "positive": positive,
            "neutral": neutral,
            "negative": negative,
        },
        "outcome_breakdown": {
            outcome: sum(1 for c in calls if c.intent_detected == outcome)
            for outcome in ["booked", "resolved", "unresolved", "transferred", "abandoned"]
        },
    }
