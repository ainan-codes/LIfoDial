"""
backend/routers/web_calls.py — Web call token generation + outbound call endpoints.
Enables browser-based voice calls to AI agents via LiveKit.
"""
import asyncio
import json
import uuid
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.auth import CurrentUser
from backend.db import get_db
from backend.models.agent_config import AgentConfig
from backend.models.tenant import Tenant
from backend.models.call_record import CallRecord
from backend.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agents", tags=["web-calls"])


# Must match WorkerOptions(agent_name=...) in backend/agent/__main__.py and
# pipeline.py so LiveKit dispatches OUR worker into the room.
AGENT_NAME = "lifodial-inbound-agent"

# Retained refs so dispatch-watchers aren't GC'd mid-flight (audit R3:
# fire-and-forget create_task can be collected before it runs).
_dispatch_watchers: set = set()

# How long to wait for the dispatched worker to actually join before declaring
# the dispatch failed. A healthy join to a warm worker is 1-3s; 15s clears that
# comfortably without holding an alarm too long. (On 2026-07-24 a worker-redeploy
# downtime window left rooms agent-less and it went unnoticed until someone
# checked the LiveKit dashboard — this watchdog exists so that can't recur.)
_AGENT_JOIN_DEADLINE_SECONDS = 15


async def _alert_if_agent_absent(
    room_name: str, call_id: str, agent_id: str, tenant_id: str,
    lk_url: str, lk_key: str, lk_secret: str,
) -> None:
    """~15s after a room is created, verify the dispatched Pipecat worker joined.
    If not, emit a LOUD alarm (ERROR log + Sentry) so 'room created but no agent
    joined' is never again silent. Best-effort; never affects the call itself.

    Heuristic: the only human joins with identity 'user-...' (set on the token
    below), so any participant whose identity is NOT 'user-*' is the agent.
    """
    try:
        await asyncio.sleep(_AGENT_JOIN_DEADLINE_SECONDS)
        from livekit import api as livekit_api

        parts = []
        room_gone = False
        async with livekit_api.LiveKitAPI(lk_url, lk_key, lk_secret) as lk:
            try:
                res = await lk.room.list_participants(
                    livekit_api.ListParticipantsRequest(room=room_name)
                )
                parts = list(res.participants)
            except Exception:
                # Room already vanished (empty_timeout) → nobody sustained a join.
                room_gone = True

        agent_present = any(
            not (getattr(p, "identity", "") or "").startswith("user-") for p in parts
        )
        if agent_present:
            return

        msg = (
            f"[DISPATCH-ALARM] Agent never joined room '{room_name}' within "
            f"{_AGENT_JOIN_DEADLINE_SECONDS}s (call_id={call_id} agent_id={agent_id} "
            f"tenant_id={tenant_id} participants={len(parts)} room_gone={room_gone}). "
            f"Worker is likely down/deregistered or agent_name mismatched — callers "
            f"in this room hear silence."
        )
        logger.error(msg)
        try:
            import sentry_sdk
            sentry_sdk.capture_message(msg, level="error")
        except Exception:
            pass
    except asyncio.CancelledError:
        raise
    except Exception as e:  # never let the watchdog crash anything
        logger.warning("[DISPATCH-ALARM] watcher error for %s: %s", room_name, e)


def _spawn_dispatch_watcher(**kwargs) -> None:
    task = asyncio.create_task(_alert_if_agent_absent(**kwargs))
    _dispatch_watchers.add(task)
    task.add_done_callback(_dispatch_watchers.discard)


@router.post("/{agent_id}/web-call-token")
async def create_web_call_token(
    agent_id: str,
    test_mode: bool = False,
    user: CurrentUser = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Creates a LiveKit room + returns token for a browser web call, and explicitly
    dispatches the Pipecat worker into that room (via RoomAgentDispatch on the
    token — the worker registers under an agent_name, so it does NOT auto-join).

    test_mode=True marks this as an in-dashboard "Test Agent" session: it is
    flagged in room metadata + the call record (for no-billing/labeling) and lets
    the worker bypass the publish gate so an admin can test an unpublished agent.
    This is the SAME real-time pipeline used for real calls — not a separate path.
    """
    # Load agent config
    result = await db.execute(
        select(AgentConfig).where(AgentConfig.id == agent_id)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agent not found")
    user.require_owns(str(agent.tenant_id))

    # Load tenant
    tenant_result = await db.execute(
        select(Tenant).where(Tenant.id == agent.tenant_id)
    )
    tenant = tenant_result.scalar_one_or_none()

    # ── Pre-call credit gate (audit P4) ─────────────────────────────────────
    # Reject up front with a clear error so the browser shows "insufficient
    # credit" immediately, instead of issuing a token for a call the worker
    # will then silently decline (the pipeline enforces the same gate as the
    # authoritative choke point). test_mode bypasses it, same as the pipeline.
    if not test_mode:
        from backend.services.credit_service import CreditService

        max_dur = int(getattr(agent, "max_duration_seconds", None) or 300)
        gate = await CreditService.check_call_allowed(db, str(agent.tenant_id), max_dur)
        if not gate["allowed"]:
            detail = (
                "Clinic account suspended (insufficient credit) — top up to resume calls."
                if gate["reason"] == "credit_suspended"
                else (
                    f"Insufficient credit to start a call. Balance ₹{gate['balance']:.2f}, "
                    f"need ₹{gate['required']:.2f} to cover a full-length call."
                )
            )
            raise HTTPException(status_code=402, detail=detail)

    # Create unique room name for this call
    prefix = "testcall" if test_mode else "webcall"
    room_name = f"{prefix}-{agent_id[:8]}-{uuid.uuid4().hex[:8]}"

    # Room metadata — agent reads this to configure itself.
    # Include ALL provider fields so the pipeline doesn't have to re-query the DB
    # (it still loads from DB, but this is the quick fallback).
    metadata = json.dumps({
        "tenant_id": str(agent.tenant_id),
        "agent_id": agent_id,
        "clinic_name": tenant.clinic_name if tenant else "Clinic",
        "first_message": agent.first_message,
        "system_prompt": agent.system_prompt,
        "tts_voice": agent.tts_voice,
        "tts_language": agent.tts_language,
        "tts_model": agent.tts_model,
        "tts_provider": getattr(agent, "tts_provider", "sarvam") or "sarvam",
        "stt_model": agent.stt_model,
        "stt_language": getattr(agent, "stt_language", None) or agent.tts_language,
        "stt_provider": getattr(agent, "stt_provider", "sarvam") or "sarvam",
        "llm_model": agent.llm_model,
        "llm_provider": getattr(agent, "llm_provider", "groq") or "groq",
        "call_type": "test" if test_mode else "web",
        "test_mode": test_mode,
    })

    # Check if LiveKit keys are configured
    lk_url = settings.livekit_url
    lk_key = settings.livekit_api_key
    lk_secret = settings.livekit_api_secret

    if not lk_key or not lk_secret or lk_url == "wss://your-project.livekit.cloud":
        # Return a mock token for development/demo without LiveKit
        call_id = str(uuid.uuid4())
        call = CallRecord(
            id=call_id,
            tenant_id=str(agent.tenant_id),
            agent_id=agent_id,
            call_type="test" if test_mode else "web",
            livekit_room_name=room_name,
            started_at=datetime.now(timezone.utc),
            status="in_progress",
        )
        db.add(call)
        # commit handled by get_db context manager

        return {
            "token": "",
            "roomName": room_name,
            "wsUrl": lk_url,
            "callId": call_id,
            "demo": True,
            "test_mode": test_mode,
            "message": "LiveKit not configured — web call will use demo mode",
        }

    try:
        from livekit import api as livekit_api

        # Create room with metadata & agent dispatch (closed cleanly via async with)
        async with livekit_api.LiveKitAPI(lk_url, lk_key, lk_secret) as lk:
            await lk.room.create_room(
                livekit_api.CreateRoomRequest(
                    name=room_name,
                    metadata=metadata,
                    empty_timeout=15,  # Vanish empty rooms in 15s (instead of 300s)
                    # THREE participants join a web call, not two: the browser
                    # caller, the livekit-agents job participant from ctx.connect()
                    # (identity 'agent-AJ_*', kind=AGENT, publishes no tracks), and
                    # Pipecat's LiveKitTransport (identity 'lifodial-agent-*'),
                    # which is the one that actually carries the agent's audio.
                    max_participants=3,
                    agents=[livekit_api.RoomAgentDispatch(agent_name=AGENT_NAME)],
                )
            )

        # Generate browser token for admin/patient, WITH an explicit agent
        # dispatch so the Pipecat worker (registered under AGENT_NAME) is pulled
        # into this room. Without this, a named-agent worker never auto-joins —
        # this was the missing piece that left rooms agent-less.
        token = livekit_api.AccessToken(lk_key, lk_secret)
        token.with_identity(f"user-{uuid.uuid4().hex[:6]}")
        token.with_name("Web Call User")
        token.with_grants(
            livekit_api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
            )
        )
        token.with_room_config(
            livekit_api.RoomConfiguration(
                agents=[livekit_api.RoomAgentDispatch(agent_name=AGENT_NAME)]
            )
        )
        token.with_ttl(timedelta(seconds=3600))  # SDK expects a timedelta, not an int

        jwt_token = token.to_jwt()
    except Exception as e:
        logger.error(f"LiveKit room creation failed: {e}")
        raise HTTPException(500, f"Failed to create call room: {str(e)}")

    # Create call record
    call_id = str(uuid.uuid4())
    call = CallRecord(
        id=call_id,
        tenant_id=str(agent.tenant_id),
        agent_id=agent_id,
        call_type="test" if test_mode else "web",
        livekit_room_name=room_name,
        started_at=datetime.now(timezone.utc),
        status="in_progress",
    )
    db.add(call)

    # Watchdog: alarm LOUDLY if the dispatched worker never actually joins this
    # room (logs + Sentry). Non-blocking; the token is returned immediately.
    _spawn_dispatch_watcher(
        room_name=room_name, call_id=call_id, agent_id=agent_id,
        tenant_id=str(agent.tenant_id), lk_url=lk_url, lk_key=lk_key, lk_secret=lk_secret,
    )

    return {
        "token": jwt_token,
        "roomName": room_name,
        "wsUrl": lk_url,
        "callId": call_id,
        "test_mode": test_mode,
    }


@router.post("/{agent_id}/outbound-call")
async def make_outbound_call(
    agent_id: str,
    body: dict,
    user: CurrentUser = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Admin dials a real phone number from dashboard.
    Uses LiveKit SIP to call the number.
    """
    phone_number = body.get("phone_number", "").strip()
    if not phone_number:
        raise HTTPException(400, "phone_number required")

    # Basic phone validation
    if not phone_number.startswith("+") or len(phone_number) < 10:
        raise HTTPException(400, "Invalid phone number format. Use +country_code...")

    # Load agent
    result = await db.execute(
        select(AgentConfig).where(AgentConfig.id == agent_id)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agent not found")
    user.require_owns(str(agent.tenant_id))

    room_name = f"outbound-{agent_id[:8]}-{uuid.uuid4().hex[:8]}"

    # Create call record
    call_id = str(uuid.uuid4())
    call = CallRecord(
        id=call_id,
        tenant_id=str(agent.tenant_id),
        agent_id=agent_id,
        call_type="outbound",
        patient_number=phone_number,
        patient_number_masked=phone_number[:4] + "XX XXXX" + phone_number[-2:],
        livekit_room_name=room_name,
        started_at=datetime.now(timezone.utc),
        status="dialing",
    )
    db.add(call)

    # SIP trunk check
    if not agent.sip_provider:
        return {
            "status": "pending",
            "callId": call_id,
            "room_name": room_name,
            "phone_number": phone_number,
            "message": "SIP trunk not configured for this agent. Configure telephony first.",
        }

    return {
        "status": "dialing",
        "callId": call_id,
        "room_name": room_name,
        "phone_number": phone_number,
        "message": f"Calling {phone_number}...",
    }


@router.get("/{agent_id}/call-records")
async def get_call_records(
    agent_id: str,
    user: CurrentUser = None,
    db: AsyncSession = Depends(get_db),
):
    """Get all call records for an agent."""
    agent_result = await db.execute(select(AgentConfig).where(AgentConfig.id == agent_id))
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(404, "Agent not found")
    user.require_owns(str(agent.tenant_id))

    result = await db.execute(
        select(CallRecord)
        .where(CallRecord.agent_id == agent_id)
        .order_by(CallRecord.created_at.desc())
        .limit(50)
    )
    records = result.scalars().all()
    return [
        {
            "id": r.id,
            "call_type": r.call_type,
            "patient_number_masked": r.patient_number_masked,
            "livekit_room_name": r.livekit_room_name,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "ended_at": r.ended_at.isoformat() if r.ended_at else None,
            "duration_seconds": r.duration_seconds,
            "status": r.status,
            "end_reason": r.end_reason,
            "outcome": r.outcome,
            "transcript": r.transcript,
            "summary": r.summary,
            "sentiment": r.sentiment,
            "detected_language": r.detected_language,
        }
        for r in records
    ]
