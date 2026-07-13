"""
backend/routers/latency.py — Agent Latency Stats endpoint.
GET /agents/{id}/latency-stats — returns latency distributions per agent.
"""
import logging
from fastapi import APIRouter, HTTPException
from sqlalchemy import select, func as sqlfunc, and_
from datetime import datetime, timedelta

from backend.auth import CurrentUser
from backend.db import async_session
from backend.models.agent_config import AgentConfig
from backend.models.call_record import CallRecord

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/agents/{agent_id}/latency-stats")
async def get_latency_stats(agent_id: str, days: int = 7, user: CurrentUser = None) -> dict:
    """
    Return latency statistics for an agent based on stored avg_latency_ms
    across recent completed calls.

    In production, the pipeline logs per-turn latency into call records.
    Here we compute P50/P95/P99 approximations from available data.
    """
    try:
        async with async_session() as db:
            agent_result = await db.execute(
                select(AgentConfig).where(AgentConfig.id == agent_id)
            )
            agent = agent_result.scalar_one_or_none()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            user.require_owns(str(agent.tenant_id))

            since = datetime.utcnow() - timedelta(days=days)
            result = await db.execute(
                select(CallRecord.avg_latency_ms, CallRecord.turn_count)
                .where(
                    and_(
                        CallRecord.agent_id == agent_id,
                        CallRecord.status == "completed",
                        CallRecord.avg_latency_ms.is_not(None),
                        CallRecord.created_at >= since,
                    )
                )
                .order_by(CallRecord.created_at.desc())
                .limit(200)
            )
            rows = result.all()

        if not rows:
            return {
                "agent_id": agent_id,
                "data_available": False,
                "message": "No completed calls with latency data yet. Make some web calls first.",
                "target_ms": 800,
            }

        latencies = sorted([float(r.avg_latency_ms) for r in rows if r.avg_latency_ms])
        n = len(latencies)

        def percentile(data, p):
            if not data:
                return 0
            idx = int(len(data) * p / 100)
            return round(data[min(idx, len(data) - 1)], 0)

        avg = round(sum(latencies) / n, 0)
        p50 = percentile(latencies, 50)
        p95 = percentile(latencies, 95)
        p99 = percentile(latencies, 99)
        target_ms = 800

        # Breakdown estimates (rough ratios from Sarvam+Gemini benchmarks)
        stt_avg = round(avg * 0.26, 0)   # ~26% of RTT
        llm_avg = round(avg * 0.46, 0)   # ~46% of RTT
        tts_avg = round(avg * 0.28, 0)   # ~28% of RTT

        return {
            "agent_id": agent_id,
            "period_days": days,
            "sample_size": n,
            "data_available": True,
            "avg_latency_ms": avg,
            "p50_ms": p50,
            "p95_ms": p95,
            "p99_ms": p99,
            "target_ms": target_ms,
            "status": "on_target" if avg <= target_ms else "above_target",
            "breakdown": {
                "stt_avg": stt_avg,
                "llm_avg": llm_avg,
                "tts_avg": tts_avg,
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Latency stats error for agent %s: %s", agent_id, e)
        raise HTTPException(status_code=500, detail=str(e))
