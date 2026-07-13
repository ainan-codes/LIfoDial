"""
backend/routers/ws.py — WebSocket endpoints for live call monitoring and streaming STT.
"""
import json
import logging
import asyncio
import base64
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select, func
from backend.redis_client import get_session
from backend.security import decode_access_token
from backend.services.sarvam_streaming import create_streaming_stt
from backend.db import AsyncSessionLocal
from backend.models.call_record import CallRecord
from backend.models.appointment import Appointment
from backend.models.doctor import Doctor
from backend.models.tenant import Tenant

logger = logging.getLogger(__name__)
router = APIRouter()

# Active WebSocket connections per tenant
connections: dict[str, list[WebSocket]] = {}

# Calls stuck in "in_progress" past this age are treated as crashed/orphaned,
# not live — mirrors the same guard used by GET /admin/overview.
_STALE_CALL_MINUTES = 30

# Background poll loop driving the superadmin dashboard's "platform" socket
# group. There's no pub/sub (Redis/Supabase Realtime) wired up yet, so
# short-interval DB polling stands in for it — fine at current scale.
_platform_broadcaster_task: "asyncio.Task | None" = None
_last_seen_booking_id: str | None = None


async def _count_active_calls(tenant_id: str | None) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_STALE_CALL_MINUTES)
    async with AsyncSessionLocal() as db:
        stmt = select(func.count(CallRecord.id)).where(
            CallRecord.status == "in_progress",
            CallRecord.started_at >= cutoff,
        )
        if tenant_id and tenant_id != "platform":
            stmt = stmt.where(CallRecord.tenant_id == tenant_id)
        return (await db.execute(stmt)).scalar() or 0


async def _latest_booking() -> dict | None:
    async with AsyncSessionLocal() as db:
        stmt = (
            select(Appointment, Tenant.clinic_name, Doctor.name)
            .join(Tenant, Appointment.tenant_id == Tenant.id)
            .outerjoin(Doctor, Appointment.doctor_id == Doctor.id)
            .where(Appointment.status != "cancelled")
            .order_by(Appointment.created_at.desc())
            .limit(1)
        )
        row = (await db.execute(stmt)).first()
        if not row:
            return None
        appt, clinic_name, doctor_name = row
        return {
            "id": appt.id,
            "patient_name": appt.patient_name,
            "patient_phone": appt.patient_phone,
            "clinic_name": clinic_name,
            "doctor": doctor_name or "—",
            "slot_time": appt.slot_time.isoformat() if appt.slot_time else None,
        }


async def _broadcast_platform_stats():
    """Poll DB every few seconds and push live active-call count / newest
    booking to connected 'platform' sockets (the superadmin dashboard)."""
    global _last_seen_booking_id
    try:
        while connections.get("platform"):
            try:
                active = await _count_active_calls("platform")
                await broadcast_event("platform", {"type": "call.active_count", "active_calls": active})

                booking = await _latest_booking()
                if booking and booking["id"] != _last_seen_booking_id:
                    _last_seen_booking_id = booking["id"]
                    await broadcast_event("platform", {"type": "booking.created", "booking": booking})
            except Exception as exc:
                logger.error(f"_broadcast_platform_stats poll error: {exc}")
            await asyncio.sleep(4.0)
    finally:
        global _platform_broadcaster_task
        _platform_broadcaster_task = None


@router.websocket("/ws/calls/{tenant_id}")
async def live_calls_ws(websocket: WebSocket, tenant_id: str, token: str | None = None):
    """
    WebSocket endpoint for the frontend dashboard to receive live call events.
    Clients subscribe by tenant_id and receive JSON events for:
    - call_started, call_ended, booking_confirmed, etc.

    Requires a valid session token passed as ?token=<jwt> (browsers can't set
    custom headers on the WS handshake). A clinic token may only subscribe to
    its own tenant_id; superadmin tokens may subscribe to any tenant_id,
    including the special "platform" feed.
    """
    claims = decode_access_token(token) if token else None
    if not claims:
        await websocket.close(code=1008)
        return
    role = claims.get("role", "clinic")
    if role != "superadmin" and claims.get("sub") != tenant_id:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    logger.info(f"WebSocket connected for tenant_id: {tenant_id}")

    if tenant_id not in connections:
        connections[tenant_id] = []
    connections[tenant_id].append(websocket)

    global _platform_broadcaster_task
    if tenant_id == "platform" and (_platform_broadcaster_task is None or _platform_broadcaster_task.done()):
        _platform_broadcaster_task = asyncio.create_task(_broadcast_platform_stats())

    try:
        active = await _count_active_calls(tenant_id)
        await websocket.send_json({"type": "connected", "tenant_id": tenant_id, "active_calls": active})

        while True:
            # Keep alive — wait for any message from client (ping/pong)
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send keepalive
                await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for tenant_id: {tenant_id}")
    finally:
        if tenant_id in connections:
            connections[tenant_id] = [c for c in connections[tenant_id] if c != websocket]


async def broadcast_event(tenant_id: str, event: dict):
    """Broadcast an event to all connected WebSocket clients for a tenant."""
    if tenant_id not in connections:
        return
    dead = []
    for ws in connections[tenant_id]:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connections[tenant_id].remove(ws)


@router.websocket("/ws/streaming-stt/{tenant_id}/{agent_id}")
async def streaming_stt_ws(websocket: WebSocket, tenant_id: str, agent_id: str, token: str | None = None):
    """
    WebSocket endpoint for real-time speech-to-text transcription.

    Requires a valid session token as ?token=<jwt> — same rule as /ws/calls.

    Client sends:
    {
        "type": "audio",
        "audio": "<base64-encoded-audio>",
        "language_code": "en-IN",
        "mode": "transcribe"  # or "translate", "verbatim", "translit", "codemix"
    }

    Server responds with:
    {
        "type": "transcript",
        "text": "...",
        "confidence": 0.95
    }
    """
    claims = decode_access_token(token) if token else None
    if not claims:
        await websocket.close(code=1008)
        return
    role = claims.get("role", "clinic")
    if role != "superadmin" and claims.get("sub") != tenant_id:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    logger.info(f"Streaming STT WS connected — tenant_id={tenant_id}, agent_id={agent_id}")

    stt_client = None
    language_code = "en-IN"
    mode = "transcribe"
    sample_rate = 16000

    try:
        # Wait for initial config message
        data = await websocket.receive_json()
        if data.get("type") == "config":
            language_code = data.get("language_code", "en-IN")
            mode = data.get("mode", "transcribe")
            sample_rate = data.get("sample_rate", 16000)
            logger.info(f"STT config: lang={language_code}, mode={mode}, sr={sample_rate}")

        # Create and connect to Sarvam streaming API
        stt_client = await create_streaming_stt(
            language_code=language_code,
            mode=mode,
            sample_rate=sample_rate,
        )

        if not stt_client:
            await websocket.send_json({
                "type": "error",
                "message": "Failed to connect to Sarvam streaming API",
                "code": "SARVAM_CONNECTION_FAILED",
            })
            return

        await websocket.send_json({
            "type": "ready",
            "language_code": language_code,
            "mode": mode,
        })

        # Start receiving STT results in background
        async def receive_stt_results():
            try:
                async for result in stt_client.receive_results():
                    await websocket.send_json(result)
            except Exception as e:
                logger.error(f"Error receiving STT results: {e}")

        result_task = asyncio.create_task(receive_stt_results())

        # Receive audio chunks from client
        try:
            while True:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=60.0)

                if data.get("type") == "audio":
                    # Decode base64 audio
                    try:
                        audio_b64 = data.get("audio", "")
                        audio_bytes = base64.b64decode(audio_b64)
                        await stt_client.send_audio(
                            audio_bytes,
                            encoding=data.get("encoding", "audio/wav"),
                        )
                    except Exception as e:
                        logger.error(f"Failed to process audio: {e}")
                        await websocket.send_json({
                            "type": "error",
                            "message": f"Audio processing error: {str(e)}",
                        })

                elif data.get("type") == "flush":
                    # Force immediate processing
                    await stt_client.flush()

                elif data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})

        except asyncio.TimeoutError:
            logger.warning(f"Streaming STT timeout for {agent_id}")
        except WebSocketDisconnect:
            logger.info(f"Streaming STT WS disconnected — agent_id={agent_id}")
            result_task.cancel()

    except Exception as e:
        logger.error(f"Streaming STT error: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(e),
            })
        except:
            pass

    finally:
        if stt_client:
            await stt_client.close()
