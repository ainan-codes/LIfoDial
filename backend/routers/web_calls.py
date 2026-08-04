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
# pipeline.py so LiveKit dispatches OUR worker into the room. Imported (not
# re-declared) from the one dependency-free module all three processes share —
# see backend/agent/agent_name.py for why a drift here is invisible at runtime.
from backend.agent.agent_name import AGENT_NAME

# Retained refs so dispatch-watchers aren't GC'd mid-flight (audit R3:
# fire-and-forget create_task can be collected before it runs).
_dispatch_watchers: set = set()

# How long to wait for the dispatched worker to actually join before declaring
# the dispatch failed. A healthy join to a warm worker is 1-3s; 15s clears that
# comfortably without holding an alarm too long. (On 2026-07-24 a worker-redeploy
# downtime window left rooms agent-less and it went unnoticed until someone
# checked the LiveKit dashboard — this watchdog exists so that can't recur.)
#
# 2026-07-25: raised 15 -> 25. The room is now only created AFTER the worker
# answers HTTP (see agent_worker.ensure_worker_awake below), but a just-woken
# free instance still needs a few seconds to re-register with LiveKit and accept
# the job. 15s was tight enough to alarm on healthy-but-freshly-woken workers.
_AGENT_JOIN_DEADLINE_SECONDS = 25

# How often to look, inside that deadline. This used to be a single check AFTER
# sleeping the whole deadline, and that made the watchdog unable to tell the two
# things apart that it exists to distinguish:
#
#   "the agent never joined"                (real: caller hears silence)
#   "the call happened and the room is gone" (normal: any call shorter than 25s)
#
# The worker DELETES the room the moment the caller disconnects
# (pipeline.py's on_participant_disconnected), so a short call erases the evidence
# before a late poll can see it. Measured on 2026-07-31T12:18:21Z: the agent
# demonstrably joined (worker logged "Connected to <room>", "Agent ready 4.56s",
# then queued the greeting), the caller hung up ~2s in, the worker deleted the
# room, and this watchdog reported `participants=0 room_gone=True` as
# "Agent never joined ... may be crash-looping, or agent_name may not match".
#
# That false alarm cost real debugging time chasing a cold start that did not
# exist. Polling instead means we observe the agent while it is actually present
# and return silently, which is both correct and cheaper: a healthy join shows up
# on the first or second poll, so the common case is 1-2 API calls, not 1 late one.
_AGENT_POLL_INTERVAL_SECONDS = 2.0


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
        import time as _time

        from livekit import api as livekit_api

        deadline = _time.monotonic() + _AGENT_JOIN_DEADLINE_SECONDS
        agent_seen = False
        caller_seen = False
        room_gone = False
        parts: list = []

        # One API session for the whole watch, reused across polls.
        async with livekit_api.LiveKitAPI(lk_url, lk_key, lk_secret) as lk:
            while True:
                try:
                    res = await lk.room.list_participants(
                        livekit_api.ListParticipantsRequest(room=room_name)
                    )
                    parts = list(res.participants)
                except Exception:
                    # The room is gone. Either the call ended and the worker
                    # deleted it, or nobody ever sustained a join. Which one is
                    # decided below by what we saw while it existed — NOT by the
                    # fact that it is gone, which is the mistake this replaces.
                    room_gone = True
                    break

                for p in parts:
                    if (getattr(p, "identity", "") or "").startswith("user-"):
                        caller_seen = True
                    else:
                        agent_seen = True

                # Dispatch worked. This is the overwhelmingly common outcome and it
                # must stay completely silent, or the alarm becomes noise.
                if agent_seen:
                    return

                if _time.monotonic() >= deadline:
                    break
                await asyncio.sleep(_AGENT_POLL_INTERVAL_SECONDS)

        if agent_seen:
            return

        # ── Room vanished before we ever saw the agent ────────────────────────
        # Not evidence of a dispatch failure. A dispatch failure leaves the CALLER
        # sitting in the room hearing silence, and LiveKit's empty_timeout only
        # reaps rooms that are empty — so a room that disappears means everyone
        # left, i.e. the caller hung up. Report it, but do not claim a cause we
        # have not established, and do not page anyone.
        if room_gone:
            logger.warning(
                "[DISPATCH-WATCH] Room '%s' was deleted before the agent was "
                "observed (call_id=%s agent_id=%s tenant_id=%s caller_seen=%s). "
                "Almost always a caller who hung up within %ds — the worker deletes "
                "the room on disconnect. NOT treated as a dispatch failure. If "
                "callers report silence on SHORT calls, check the worker log for "
                "'Connected to %s' to see whether the agent actually joined.",
                room_name, call_id, agent_id, tenant_id, caller_seen,
                _AGENT_JOIN_DEADLINE_SECONDS, room_name,
            )
            return

        # Report whether the worker URL is even configured, and whether we
        # believed it warm at dispatch time. Without this the alarm blamed
        # "down/deregistered or agent_name mismatched" for what was actually a
        # free-tier cold start, which sent debugging down the wrong path.
        from backend.services import agent_worker

        # State a NEXT STEP, not a cause. The previous wording asserted
        # "crash-looping on boot, or agent_name may not match" on every alarm, and
        # both were already excluded before the message was even written:
        # _probe_once validates the reported agent_name against ours and logs a
        # dedicated ERROR when they differ, and a crash-looping worker could not
        # have answered the pre-warm probe that gates room creation. On
        # 2026-07-31 that confident-but-wrong text sent debugging after a ~55s
        # Render cold start on a host that does not sleep at all. An alarm that
        # guesses is worse than one that reports only what it observed.
        if not agent_worker.is_configured():
            cause = (
                "AGENT_WORKER_URL is NOT set, so the worker is never pre-warmed and "
                "the room is dispatched blind. Set AGENT_WORKER_URL on the backend "
                "service so call setup can verify the worker before creating a room."
            )
        else:
            cause = (
                "The worker answered its pre-warm probe (so the process is up and its "
                "agent_name matches — a mismatch logs its own ERROR from "
                "agent_worker._probe_once) yet no agent participant appeared. Look at "
                f"the WORKER log for room '{room_name}': if 'Agent entrypoint' is "
                "absent, LiveKit never delivered the job; if it is present but "
                f"'Connected to {room_name}' is not, the job failed during setup. "
                f"Worker state: {agent_worker.worker_base_url()}/worker"
            )

        msg = (
            f"[DISPATCH-ALARM] No agent participant in room '{room_name}' after "
            f"{_AGENT_JOIN_DEADLINE_SECONDS}s of polling, and the room is still alive "
            f"(call_id={call_id} agent_id={agent_id} tenant_id={tenant_id} "
            f"participants={len(parts)} caller_seen={caller_seen}). "
            f"Callers in this room hear silence. {cause}"
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


@router.post("/voice-worker/warm")
async def warm_voice_worker(user: CurrentUser = None):
    """Start waking the voice worker and return IMMEDIATELY.

    The dashboard calls this the moment the Test Agent panel opens, so the
    free-tier cold start (~55s, see backend/services/agent_worker.py) overlaps
    with the admin reading the screen instead of landing on them after they press
    Start. Returns the current belief, never blocks, never fails a request.
    """
    from backend.services import agent_worker

    started = agent_worker.start_background_warm()
    return {**agent_worker.state(), "started": started}


@router.get("/voice-worker/status")
async def voice_worker_status(user: CurrentUser = None):
    """Non-blocking read of whether the voice worker is believed ready."""
    from backend.services import agent_worker

    return agent_worker.state()


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

    # No credit gate here. Credits are not enforced in this MVP phase — a call is
    # never refused for balance/suspension reasons, for any clinic. See
    # backend/services/credit_service.py for the single place that decision lives.

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
        # THE one language. The tts_language/stt_language keys below are the
        # derived mirrors, sent only so an agent worker still running an older
        # revision (which reads them) keeps working across a deploy.
        "language": agent.language,
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

    # ── Wake the worker BEFORE creating the room ────────────────────────────
    # `lifodial-agent` is on Render's free plan and spins down after ~15min idle;
    # a spun-down worker is DEREGISTERED from LiveKit, so RoomAgentDispatch has
    # nothing to dispatch to and the caller hears silence. Measured cold start is
    # ~55s against a 15s join deadline, so the first call after any idle period
    # failed 100% of the time — that is the [DISPATCH-ALARM] above.
    #
    # Blocking here (instead of dispatching hopefully) means the room is only
    # ever created once the worker can actually accept the job. Warm calls cost
    # ~0.2s.
    #
    # If it never comes up, REFUSE rather than dispatch hopefully. Issuing a token
    # anyway is what produced the worst failure mode this product has: the browser
    # connects, the visualiser spins, the caller talks into a room no agent is in,
    # and 25s later a [DISPATCH-ALARM] appears in a log nobody is reading. A 503
    # with a real reason is strictly better than silence that looks like a broken
    # microphone. (Three consecutive calls failed exactly this way on 2026-07-29:
    # 10:25, 10:26, 11:18 — see backend/services/agent_worker.py::_probe_once.)
    from backend.services import agent_worker

    if agent_worker.is_configured():
        if not await agent_worker.ensure_worker_awake():
            raise HTTPException(
                status_code=503,
                detail=(
                    "The voice service is still starting up and can't take a call yet. "
                    "Please try again in a minute."
                ),
            )
    else:
        logger.warning(
            "AGENT_WORKER_URL is not set — dispatching without a pre-warm check. If the "
            "worker is spun down this room will have no agent."
        )

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
