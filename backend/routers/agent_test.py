"""
backend/routers/agent_test.py
In-browser agent testing: Chat (REST) + Voice (WebSocket audio streaming).
No phone required — pure browser-based.
"""
import asyncio
import json
import logging
import time
# sync_and_log_appointment() takes a parameter literally named `time`, which
# shadows the module inside that function — this alias is how its timing log
# reaches the real clock.
import time as time_mod
import uuid
import base64
import os
import re
from collections import defaultdict
from typing import AsyncGenerator, Optional
from backend.services.sheets import log_booking_to_sheets

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import CurrentUser
from backend.db import async_session as AsyncSessionLocal, get_db
from backend.models.agent_config import AgentConfig
from backend.models.api_key_config import ApiKeyConfig
from backend.config import settings
from backend.security import decode_access_token
from backend.services.impersonation import claims_still_active
from backend import redis_client
from backend.agent.booking_rules import (
    BOOKING_RULES_BLOCK,
    BOOKING_RESULT_TRUE,
    BOOKING_RESULT_FALSE,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Language Detection Tracker ─────────────────────────────────────────────────
# Per-session rolling window of detected languages for ratio-based switching
_language_tracker: dict[str, list[str]] = {}
LANGUAGE_WINDOW_SIZE = 10  # Track last N utterances
LANGUAGE_SWITCH_THRESHOLD = 0.6  # 60% of recent utterances must be in new language

# Cache synthesized greeting clips to avoid repeating cold-start latency
_greeting_audio_cache: dict[str, bytes] = {}

# ── Per-session agent-speaking timestamp for echo suppression ──────────────────
# When agent sends TTS audio, this records the wall-clock time at which the
# agent's speech (plus a buffer) is expected to finish.  Any user mic audio
# arriving before that timestamp is silently discarded to prevent the STT from
# transcribing the agent's own voice.
_agent_speaking_until: dict[str, float] = {}

# ── Per-session turn counter for duplicate-greeting prevention ────────────────
_session_turn_count: dict[str, int] = {}

# ── Per-session explicit language override (set by language-switch keywords) ──
_session_language_override: dict[str, str] = {}

# ── Simple greetings that should NOT trigger an LLM call on turn 0 ────────────
SIMPLE_GREETINGS = {
    "hello", "hi", "hey", "halo", "helo",
    "namaste", "namaskar", "vanakkam", "salam",
    "namasthe", "namaskara", "salaam",
}

# ── Explicit language-switch keywords ─────────────────────────────────────────
LANGUAGE_SWITCH_KEYWORDS: dict[str, list[str]] = {
    "en-IN": ["english", "in english", "speak english", "talk english", "talk in english", "speak in english", "english please"],
    "hi-IN": ["hindi", "in hindi", "speak hindi", "talk hindi", "talk in hindi", "speak in hindi", "hindi please", "हिंदी", "हिन्दी"],
    "ml-IN": ["malayalam", "in malayalam", "speak malayalam", "talk in malayalam", "malayalam please", "മലയാളം"],
    "ta-IN": ["tamil", "in tamil", "speak tamil", "talk in tamil", "tamil please", "தமிழ்"],
    "te-IN": ["telugu", "in telugu", "speak telugu", "talk in telugu", "telugu please", "తెలుగు"],
    "kn-IN": ["kannada", "in kannada", "speak kannada", "talk in kannada", "kannada please", "ಕನ್ನಡ"],
    "bn-IN": ["bengali", "in bengali", "speak bengali", "talk in bengali", "bengali please", "বাংলা"],
    "gu-IN": ["gujarati", "in gujarati", "speak gujarati", "talk in gujarati", "gujarati please", "ગુજરાતી"],
    "ar-SA": ["arabic", "in arabic", "speak arabic", "talk in arabic", "arabic please", "عربي", "العربية"],
}

# ── Per-language system prompt enforcement ────────────────────────────────────
LANGUAGE_INSTRUCTIONS: dict[str, str] = {
    "hi-IN": "ALWAYS respond in Hindi. Never switch languages unless user explicitly requests.",
    "ml-IN": "ALWAYS respond in Malayalam. Never switch languages unless user explicitly requests.",
    "ta-IN": "ALWAYS respond in Tamil. Never switch languages unless user explicitly requests.",
    "en-IN": "ALWAYS respond in English. Never switch languages unless user explicitly requests.",
    "te-IN": "ALWAYS respond in Telugu. Never switch languages unless user explicitly requests.",
    "kn-IN": "ALWAYS respond in Kannada. Never switch languages unless user explicitly requests.",
    "bn-IN": "ALWAYS respond in Bengali. Never switch languages unless user explicitly requests.",
    "gu-IN": "ALWAYS respond in Gujarati. Never switch languages unless user explicitly requests.",
    "ar-SA": "ALWAYS respond in Arabic. Never switch languages unless user explicitly requests.",
}


def detect_language_switch(transcript: str) -> Optional[str]:
    """Check if user explicitly asked to switch language via keywords.
    Returns the target language code, or None if no switch detected."""
    t = transcript.lower().strip()
    for lang_code, keywords in LANGUAGE_SWITCH_KEYWORDS.items():
        if any(kw in t for kw in keywords):
            return lang_code
    return None

def get_dominant_language(session_id: str, default: str = "en-IN") -> str:
    """Get the dominant language based on ratio of recent detections."""
    history = _language_tracker.get(session_id, [])
    if not history:
        return default
    
    # Count occurrences in the rolling window
    counts: dict[str, int] = {}
    for lang in history[-LANGUAGE_WINDOW_SIZE:]:
        counts[lang] = counts.get(lang, 0) + 1
    
    total = sum(counts.values())
    # Find the language with highest ratio
    dominant = max(counts, key=lambda k: counts[k])
    ratio = counts[dominant] / total
    
    if ratio >= LANGUAGE_SWITCH_THRESHOLD:
        return dominant
    return default

def track_language(session_id: str, detected_lang: str):
    """Add a detected language to the session's tracking window."""
    if session_id not in _language_tracker:
        _language_tracker[session_id] = []
    _language_tracker[session_id].append(detected_lang)
    # Keep only last N entries
    if len(_language_tracker[session_id]) > LANGUAGE_WINDOW_SIZE * 2:
        _language_tracker[session_id] = _language_tracker[session_id][-LANGUAGE_WINDOW_SIZE:]


def _greeting_cache_key(agent: AgentConfig, text: str) -> str:
    return "|".join([
        str(agent.id),
        str(agent.tts_provider or "sarvam"),
        str(agent.tts_model or "bulbul:v3"),
        str(agent.tts_voice or ""),
        str(agent.language or "en-IN"),
        str((text or "").strip()),
    ])


async def _send_greeting_audio_fast(websocket: WebSocket, agent: AgentConfig, first_msg: str):
    """Send greeting audio without blocking call setup.
    Uses cache and a timeout to keep connect experience snappy."""
    cache_key = _greeting_cache_key(agent, first_msg)
    cached = _greeting_audio_cache.get(cache_key)
    if cached:
        try:
            await websocket.send_bytes(cached)
        except RuntimeError:
            return
        return

    try:
        # Avoid long startup stalls when provider has cold starts.
        greeting_audio = await asyncio.wait_for(synthesize_speech(agent, first_msg), timeout=25.0)
        if greeting_audio:
            _greeting_audio_cache[cache_key] = greeting_audio
            try:
                await websocket.send_bytes(greeting_audio)
            except RuntimeError:
                return
    except asyncio.TimeoutError:
        logger.warning("Greeting TTS timed out (>25s), skipping greeting audio for this call")
    except Exception as e:
        logger.warning(f"Greeting TTS failed (non-fatal): {e}")

# ── Key Decoding Helper ───────────────────────────────────────────────────────
def decrypt_key(encrypted: str) -> str:
    """Decode base64-obfuscated API key (dev mode). Use KMS in production."""
    try:
        return base64.b64decode(encrypted.encode()).decode()
    except Exception:
        return encrypted
        
# ── HTTPS CHAT (REST) ─────────────────────────────────────────────────────────

@router.get("/agent-chat/{agent_id}/greeting")
async def get_agent_greeting(agent_id: str, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AgentConfig).where(AgentConfig.id == agent_id)
    )
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(
            status_code=404,
            detail=f"Agent {agent_id} not found"
        )
    user.require_owns(str(agent.tenant_id))

    return {
        "agent_id": agent_id,
        "agent_name": agent.agent_name,
        "message": agent.first_message or f"Hello! I'm {agent.agent_name}. How can I help you today?",
        "session_id": str(uuid.uuid4()),
        "tts_provider": agent.tts_provider,
        "tts_voice": agent.tts_voice
    }


@router.post("/agent-chat/{agent_id}")
async def chat_with_agent(
    agent_id: str,
    body: dict,
    user: CurrentUser = None,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(AgentConfig).where(AgentConfig.id == agent_id)
    )
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(
            status_code=404,
            detail=f"Agent {agent_id} not found"
        )
    user.require_owns(str(agent.tenant_id))

    user_message = body.get("message", "")
    session_id = body.get("session_id", agent_id)
    
    if not user_message:
        raise HTTPException(status_code=400, detail="message is required")
    
    response_text = await generate_llm_response(
        agent, user_message, db, session_id
    )
    
    return {
        "response": response_text,
        "session_id": session_id,
        "agent_name": agent.agent_name
    }


@router.delete("/agent-chat/{agent_id}/session/{session_id}")
async def clear_agent_session(agent_id: str, session_id: str, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AgentConfig).where(AgentConfig.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent:
        user.require_owns(str(agent.tenant_id))
    session_key = session_id or agent_id
    if session_key in _conversation_history:
        del _conversation_history[session_key]
    await redis_client.delete_chat_history(str(session_key))
    return {"status": "cleared"}


# ── Per-session barge-in / interrupt state ────────────────────────────────────
# Maps ws_session_id → asyncio.Event. When set, in-flight TTS send aborts.
_agent_speaking: dict[str, asyncio.Event] = {}

# ── Per-session cancellation — set when the user closes the widget ────────────
# Checked at key points in handle_audio_turn to abort in-flight work instantly.
_session_cancelled: dict[str, asyncio.Event] = {}

# ── Per-session active audio turn tasks ───────────────────────────────────────
# Maps ws_session_id → asyncio.Task. The current active handle_audio_turn task.
_active_audio_tasks: dict[str, asyncio.Task] = {}


# ── WS /ws/agent-call/{agent_id} ──────────────────────────────────────────────

@router.websocket("/ws/agent-call/{agent_id}")
async def voice_websocket(websocket: WebSocket, agent_id: str, token: str | None = None):
    """
    Stable voice WebSocket handler. Dashboard "test this agent" feature only —
    the public embed widget does not use this endpoint (it uses /embed/*).
    Requires a valid session token as ?token=<jwt>, and the caller must own
    the agent's tenant (or be superadmin).

    Key design decisions:
    - DB session opened for agent load only, then closed immediately.
      Each turn (audio/text) opens its own fresh session → no pool exhaustion.
    - asyncio.wait() with 20-second timeout drives the loop so it never
      blocks forever on stale connections.
    - Server sends JSON {"type":"pong"} every 20 s to keep TCP alive through
      proxies/firewalls.
    - 120-second idle timeout closes the connection gracefully.
    - Every exception path is caught; greeting task is always cancelled.
    - Interrupt message: client sends {"type":"interrupt"} to stop agent speech.
    """
    claims = decode_access_token(token) if token else None
    if not claims:
        await websocket.close(code=1008)
        return
    # An exited/expired superadmin impersonation token is dead here too, not just
    # on the HTTP API (services/impersonation.py::claims_still_active). No-op for
    # ordinary clinic/superadmin tokens.
    if not await claims_still_active(claims):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    logger.info(f"Voice WebSocket connected for agent {agent_id}")

    ws_session_id = str(uuid.uuid4())
    greeting_task: asyncio.Task | None = None
    call_active = True  # BUG 3: graceful disconnect flag
    # Create a per-session interrupt event for barge-in
    interrupt_event = asyncio.Event()
    _agent_speaking[ws_session_id] = interrupt_event
    # Create cancellation event — set when user closes widget
    cancel_event = asyncio.Event()
    _session_cancelled[ws_session_id] = cancel_event
    # BUG 1: echo suppression — initialise speaking-until timestamp
    _agent_speaking_until[ws_session_id] = 0.0
    # BUG 4: duplicate greeting prevention
    _session_turn_count[ws_session_id] = 0

    # ── Load agent in a short-lived DB session ────────────────────────────────
    agent: AgentConfig | None = None
    try:
        async with AsyncSessionLocal() as _db:
            result = await _db.execute(
                select(AgentConfig).where(AgentConfig.id == agent_id)
            )
            agent = result.scalar_one_or_none()
    except Exception as db_err:
        logger.error(f"DB error loading agent {agent_id}: {db_err}")
        try:
            await websocket.send_json({"type": "error", "message": "Database error", "code": "DB_ERROR"})
            await websocket.close(code=1011)
        except Exception:
            pass
        _language_tracker.pop(ws_session_id, None)
        return

    if agent is None:
        logger.warning(f"Agent {agent_id} not found")
        try:
            await websocket.send_json({"error": "Agent not found", "agent_id": agent_id})
            await websocket.close(code=1008)
        except Exception:
            pass
        _language_tracker.pop(ws_session_id, None)
        return

    if claims.get("role") != "superadmin" and claims.get("sub") != str(agent.tenant_id):
        try:
            await websocket.send_json({"error": "Not found", "agent_id": agent_id})
            await websocket.close(code=1008)
        except Exception:
            pass
        _language_tracker.pop(ws_session_id, None)
        return

    logger.info(f"Agent loaded: {agent.agent_name}")
    # NOTE: _session_language_override is intentionally NOT pre-set here.
    # It is ONLY populated when the user explicitly asks to switch language
    # (e.g. "speak in Malayalam"). The default language detection flows through
    # get_dominant_language() which uses actual STT detections.

    # ── Send ready signal ─────────────────────────────────────────────────────
    first_msg = agent.first_message or "Hello, how can I help?"
    first_msg_mode = getattr(agent, 'first_message_mode', 'assistant-speaks-first') or 'assistant-speaks-first'
    try:
        await websocket.send_json({
            "type": "ready",
            "agent_name": agent.agent_name,
            "first_message": first_msg,
            "first_message_mode": first_msg_mode,
            "tts_provider": agent.tts_provider,
            "stt_provider": agent.stt_provider,
        })
        await websocket.send_json({"type": "status", "status": "connected"})
    except Exception as e:
        logger.warning(f"Cannot send ready for {agent_id}: {e}")
        _language_tracker.pop(ws_session_id, None)
        return

    # ── Kick off greeting audio in background (only if agent speaks first) ────
    if first_msg_mode == 'assistant-speaks-first':
        greeting_task = asyncio.create_task(
            _send_greeting_audio_fast(websocket, agent, first_msg)
        )

    # ── Main stable message loop ──────────────────────────────────────────────
    PING_INTERVAL = 20.0   # send keepalive every 20 s
    IDLE_TIMEOUT  = 120.0  # close if no data for 120 s
    loop          = asyncio.get_event_loop()
    last_activity = loop.time()
    next_ping_at  = last_activity + PING_INTERVAL

    try:
        while call_active:
            now = loop.time()

            # Send keepalive ping if PING_INTERVAL has elapsed
            if now >= next_ping_at:
                try:
                    await websocket.send_json({"type": "ping"})
                    next_ping_at = now + PING_INTERVAL
                except Exception:
                    logger.info(f"Keepalive ping failed — client gone ({agent_id})")
                    break

            # Enforce idle timeout
            if now - last_activity > IDLE_TIMEOUT:
                logger.info(f"WS idle timeout ({IDLE_TIMEOUT:.0f}s) for {agent_id}")
                try:
                    await websocket.send_json({"type": "status", "status": "ended", "reason": "idle_timeout"})
                    await websocket.close(code=1000)
                except Exception:
                    pass
                break

            # Wait up to 5 s for next frame (short so ping fires on time)
            wait_secs = min(5.0, max(0.5, next_ping_at - loop.time()))
            recv_fut = asyncio.ensure_future(websocket.receive())
            done, _ = await asyncio.wait({recv_fut}, timeout=wait_secs)

            if not done:
                recv_fut.cancel()
                try:
                    await recv_fut
                except (asyncio.CancelledError, Exception):
                    pass
                continue

            # Retrieve received frame
            try:
                data = recv_fut.result()
            except WebSocketDisconnect:
                logger.info(f"Client disconnected for {agent_id}")
                break
            except Exception as e:
                e_s = str(e).lower()
                if any(k in e_s for k in ("disconnect", "closed", "1000", "1001", "1005", "going away")):
                    logger.info(f"Client closed WS for {agent_id}")
                else:
                    logger.warning(f"WS receive error for {agent_id}: {e}")
                break

            last_activity = loop.time()

            if data.get("type") == "websocket.disconnect":
                call_active = False
                logger.info(f"Client disconnected — stopping pipeline for {agent_id}")
                break

            if data.get("type") != "websocket.receive":
                continue

            raw_bytes = data.get("bytes")
            raw_text  = data.get("text")

            # ── Audio frame → STT → LLM → TTS ──
            if raw_bytes:
                # Skip if session is already cancelled (user closed widget)
                if cancel_event.is_set() or not call_active:
                    break

                # BUG 1: Echo suppression — discard mic audio while agent is speaking
                speaking_until = _agent_speaking_until.get(ws_session_id, 0.0)
                if time.time() < speaking_until:
                    logger.info(
                        "Discarding audio — agent speaking for %.1fs more",
                        speaking_until - time.time(),
                    )
                    continue  # Skip STT for this chunk

                # Cancel existing audio task if any is still running to achieve true barge-in / interruption
                active_task = _active_audio_tasks.get(ws_session_id)
                if active_task and not active_task.done():
                    logger.info(f"Interrupting/Cancelling active audio turn task for session {ws_session_id} due to new incoming audio")
                    active_task.cancel()

                async def run_turn(bytes_to_process: bytes):
                    try:
                        async with AsyncSessionLocal() as db:
                            await handle_audio_turn(websocket, agent, bytes_to_process, db, ws_session_id)
                    except asyncio.CancelledError:
                        logger.info(f"Audio turn task cancelled for session {ws_session_id}")
                    except Exception as e:
                        logger.error(f"Background audio turn error for {agent_id}: {e}", exc_info=True)
                        try:
                            await websocket.send_json({"type": "error", "message": f"Processing error: {e}"})
                            await websocket.send_json({"type": "status", "status": "idle"})
                        except Exception:
                            pass

                _active_audio_tasks[ws_session_id] = asyncio.create_task(run_turn(raw_bytes))
                continue

            # ── Text frame ──
            if raw_text:
                try:
                    msg = json.loads(raw_text)
                except json.JSONDecodeError:
                    logger.warning(f"Bad JSON from {agent_id}: {raw_text[:60]}")
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "end":
                    try:
                        await websocket.send_json({"type": "status", "status": "ended"})
                    except Exception:
                        pass
                    break

                elif msg_type == "interrupt":
                    # ── Barge-in: user spoke while agent was playing audio ──
                    logger.info(f"Interrupt received from client for {agent_id}")
                    
                    # Cancel any active audio turn task immediately
                    active_task = _active_audio_tasks.get(ws_session_id)
                    if active_task and not active_task.done():
                        logger.info(f"Cancelling active audio turn task for session {ws_session_id} due to interrupt")
                        active_task.cancel()

                    # Signal any in-flight TTS/greeting to abort
                    interrupt_event.set()
                    # Cancel greeting task if still running
                    if greeting_task and not greeting_task.done():
                        greeting_task.cancel()
                        try:
                            await greeting_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        greeting_task = None
                    # Clear speaking-until timestamp so user's next utterance isn't ignored
                    _agent_speaking_until[ws_session_id] = 0.0
                    try:
                        await websocket.send_json({"type": "status", "status": "idle"})
                    except Exception:
                        break
                    # Reset event so next TTS turn works normally
                    interrupt_event.clear()

                elif msg_type in ("ping", "pong"):
                    try:
                        await websocket.send_json({"type": "pong"})
                    except Exception:
                        break

                else:
                    try:
                        async with AsyncSessionLocal() as db:
                            await handle_text_command(websocket, agent, msg, db, ws_session_id)
                    except Exception as e:
                        logger.error(f"Text command error for {agent_id}: {e}", exc_info=True)
                        try:
                            await websocket.send_json({"type": "error", "message": str(e)})
                        except Exception:
                            break

    except WebSocketDisconnect:
        call_active = False
        logger.info(f"WS disconnected (outer) for {agent_id}")
    except Exception as e:
        call_active = False
        logger.error(f"Fatal WS loop error for {agent_id}: {e}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": "Internal server error", "code": "INTERNAL_ERROR"})
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        # Signal cancellation so any in-flight handle_audio_turn aborts
        call_active = False
        cancel_event.set()
        
        # Cancel any active audio turn task
        active_task = _active_audio_tasks.pop(ws_session_id, None)
        if active_task and not active_task.done():
            logger.info(f"Cancelling active audio turn task for session {ws_session_id} due to call end")
            active_task.cancel()

        if greeting_task and not greeting_task.done():
            greeting_task.cancel()
            try:
                await greeting_task
            except (asyncio.CancelledError, Exception):
                pass
        _language_tracker.pop(ws_session_id, None)
        _agent_speaking.pop(ws_session_id, None)
        _agent_speaking_until.pop(ws_session_id, None)
        _session_cancelled.pop(ws_session_id, None)
        _session_turn_count.pop(ws_session_id, None)
        _session_language_override.pop(ws_session_id, None)
        _active_audio_tasks.pop(ws_session_id, None)
        # Clean up conversation history for this session
        _conversation_history.pop(ws_session_id, None)
        logger.info(f"Voice call ended cleanly for {agent_id}")


async def _send_greeting_audio_fast(websocket: WebSocket, agent: AgentConfig, text: str):
    """Synthesize the first_message greeting and send it as audio over the WebSocket.
    Runs as a background task immediately after connection — gives the agent the
    'speaks first' behaviour."""
    try:
        tts_provider = agent.tts_provider or "sarvam"
        if tts_provider == "sarvam":
            audio_bytes = await sarvam_synthesize_with_retry(
                agent, text, language_override=agent.language or "en-IN"
            )
        else:
            audio_bytes = await synthesize_speech(
                agent, text, language_override=agent.language or "en-IN"
            )

        if audio_bytes and len(audio_bytes) >= 512:
            # Send as raw binary bytes — both widget.js (browser embed) and
            # TestAgentModal.tsx (in-app tester) handle binary blobs uniformly
            # via their WS onmessage Blob path. Sending as JSON `greeting_audio`
            # was silently ignored by TestAgentModal.tsx (audio never played).
            try:
                await websocket.send_bytes(audio_bytes)
            except RuntimeError:
                return
            # NOTE: Do NOT send a separate 'transcript' here.
            # The 'ready' event already delivers first_message text to the UI.
            logger.info("Greeting audio sent (%d bytes) for agent %s", len(audio_bytes), agent.id)
        else:
            logger.warning(
                "Greeting TTS returned empty/small response (len=%s) for agent %s — skipping audio",
                (len(audio_bytes) if audio_bytes else 0), agent.id,
            )
    except (WebSocketDisconnect, RuntimeError):
        logger.info("Client disconnected before greeting audio could be sent")
    except Exception as e:
        logger.exception("Greeting audio synthesis failed for agent %s: %s", agent.id, e)


# ── WS /ws/agent/{agent_id}/tts-stream ────────────────────────────────────────
# Streaming TTS using Sarvam's WebSocket API for low-latency audio generation

@router.websocket("/ws/agent/{agent_id}/tts-stream")
async def tts_streaming_websocket(websocket: WebSocket, agent_id: str, token: str | None = None):
    """
    WebSocket endpoint for streaming text-to-speech. Debug/test feature only.
    Requires a valid session token as ?token=<jwt> and ownership of the agent.

    Client flow:
    1. Connect to this endpoint
    2. Send config message with voice parameters
    3. Send text chunks to synthesize
    4. Receive audio chunks progressively
    5. Send flush to finish processing
    """
    claims = decode_access_token(token) if token else None
    if not claims:
        await websocket.close(code=1008)
        return
    # An exited/expired superadmin impersonation token is dead here too, not just
    # on the HTTP API (services/impersonation.py::claims_still_active). No-op for
    # ordinary clinic/superadmin tokens.
    if not await claims_still_active(claims):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    logger.info(f"TTS Streaming WebSocket connected for agent {agent_id}")

    # Load agent configuration
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                select(AgentConfig).where(AgentConfig.id == agent_id)
            )
            agent = result.scalar_one_or_none()

            if agent is None:
                logger.warning(f"Agent {agent_id} not found for TTS streaming")
                await websocket.send_json({
                    "error": "Agent not found",
                    "agent_id": agent_id
                })
                await websocket.close(code=1008)
                return

            if claims.get("role") != "superadmin" and claims.get("sub") != str(agent.tenant_id):
                await websocket.send_json({"error": "Not found", "agent_id": agent_id})
                await websocket.close(code=1008)
                return

            # Check if TTS provider is Sarvam and has API key
            if agent.tts_provider != "sarvam":
                await websocket.send_json({
                    "error": f"TTS streaming only available for Sarvam provider, got {agent.tts_provider}",
                    "status": "unsupported"
                })
                await websocket.close(code=1008)
                return
            
            api_key = settings.sarvam_api_key or os.getenv("SARVAM_API_KEY")
            if not api_key:
                await websocket.send_json({
                    "error": "No Sarvam API key configured",
                    "status": "unauthorized"
                })
                await websocket.close(code=1008)
                return
            
            await websocket.send_json({
                "type": "ready",
                "agent_id": agent_id,
                "status": "connected",
                "message": "Ready for streaming TTS. Send config message first."
            })
            
            # Manage Sarvam streaming connection
            await manage_sarvam_streaming_tts(
                websocket, agent, api_key
            )
            
        except Exception as e:
            logger.error(f"TTS Streaming error: {e}", exc_info=True)
            try:
                await websocket.send_json({
                    "type": "error",
                    "message": str(e),
                    "code": "TTS_STREAMING_ERROR"
                })
                await websocket.close(code=1011)
            except Exception:
                pass


async def manage_sarvam_streaming_tts(
    client_ws: WebSocket,
    agent: AgentConfig,
    api_key: str
):
    """
    Manage bidirectional streaming with Sarvam's TTS API.
    
    Client sends:
    - config: {speaker, target_language_code, pace, min_buffer_size, max_chunk_length, output_audio_codec, output_audio_bitrate}
    - text: {text: "..."}
    - flush: {} (force process buffer)
    - ping: {} (keep-alive)
    
    We forward to Sarvam and relay audio back.
    """
    import websockets
    
    sarvam_ws = None
    config_sent = False
    buffered_config = None
    relay_task = None
    
    try:
        async for client_msg in client_ws.iter_text():
            try:
                msg = json.loads(client_msg)
                msg_type = msg.get("type", "").lower()
                
                # Lazy-connect on first config
                if msg_type == "config" and not sarvam_ws:
                    config_sent = True
                    buffered_config = msg.get("data", {})
                    try:
                        sarvam_ws = await websockets.connect(
                            "wss://api.sarvam.ai/text-to-speech-streaming/streaming",
                            subprotocols=["tts-streaming"],
                            ping_interval=20
                        )
                        logger.info(f"Connected to Sarvam streaming TTS API for agent {agent.id}")
                        
                        # Start relay task to send Sarvam responses back to client
                        relay_task = asyncio.create_task(
                            relay_sarvam_audio(sarvam_ws, client_ws)
                        )
                        
                    except Exception as e:
                        logger.error(f"Failed to connect to Sarvam: {e}")
                        await client_ws.send_json({
                            "error": f"Failed to connect to Sarvam TTS: {str(e)}",
                            "status": "connection_failed"
                        })
                        return
                
                # Forward config to Sarvam (add API key to auth)
                if msg_type == "config" and sarvam_ws:
                    sarvam_config = {
                        "type": "config",
                        "data": {
                            "api_subscription_key": api_key,
                            "model": agent.tts_model or "bulbul:v3",
                            "speaker": buffered_config.get(
                                "speaker",
                                (agent.tts_voice or "shubh").lower()
                            ),
                            "target_language_code": buffered_config.get(
                                "target_language_code",
                                agent.language or "en-IN"
                            ),
                            "pace": buffered_config.get("pace", agent.tts_pace or 1.0),
                            # 30/60 — same tuning as pipeline.py's live-call path
                            # (Sarvam's floor is 30; below it the server rejects
                            # the whole config). 50/200 are pipecat's stock,
                            # narration-tuned defaults and reproduce the
                            # wait-for-the-whole-reply latency this agent can't
                            # afford at a 2-sentence response length.
                            "min_buffer_size": buffered_config.get("min_buffer_size", 30),
                            "max_chunk_length": buffered_config.get("max_chunk_length", 60),
                            "output_audio_codec": buffered_config.get("output_audio_codec", "mp3"),
                            "output_audio_bitrate": buffered_config.get("output_audio_bitrate", "128k"),
                            "pitch": agent.tts_pitch or 0.0,
                            "loudness": agent.tts_loudness or 1.0,
                            "send_completion_event": True,
                        }
                    }
                    await sarvam_ws.send(json.dumps(sarvam_config))
                    logger.info(f"Sent TTS config to Sarvam for agent {agent.id}")
                    await client_ws.send_json({
                        "type": "status",
                        "status": "configured",
                        "message": "Configuration sent to Sarvam"
                    })
                
                # Forward text, flush, and ping to Sarvam
                elif msg_type in ["text", "flush", "ping"] and sarvam_ws:
                    if msg_type == "text":
                        # Validate text length
                        text_content = msg.get("data", {}).get("text", "")
                        if not text_content:
                            await client_ws.send_json({
                                "error": "Text content is empty",
                                "status": "invalid_input"
                            })
                            continue
                        if len(text_content) > 2500:
                            await client_ws.send_json({
                                "error": f"Text exceeds 2500 character limit ({len(text_content)} sent)",
                                "status": "text_too_long"
                            })
                            continue
                    
                    # Forward to Sarvam as-is
                    await sarvam_ws.send(client_msg)
                    logger.debug(f"Forwarded {msg_type} message to Sarvam")
                
                elif msg_type == "end":
                    # Client wants to close
                    await client_ws.send_json({
                        "type": "status",
                        "status": "ended"
                    })
                    break
                
                else:
                    if not config_sent:
                        await client_ws.send_json({
                            "error": "Must send config message first",
                            "status": "config_required"
                        })
                    else:
                        logger.warning(f"Unknown message type: {msg_type}")
                        await client_ws.send_json({
                            "error": f"Unknown message type: {msg_type}",
                            "status": "unknown_type"
                        })
                        
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON from client: {e}")
                await client_ws.send_json({
                    "error": "Invalid JSON format",
                    "status": "json_error"
                })
            except Exception as e:
                logger.error(f"Error processing client message: {e}", exc_info=True)
                await client_ws.send_json({
                    "error": f"Processing error: {str(e)}",
                    "status": "processing_error"
                })
    
    except WebSocketDisconnect:
        logger.info(f"Client disconnected from TTS streaming")
    except asyncio.CancelledError:
        logger.info("TTS streaming task cancelled")
    except Exception as e:
        logger.error(f"Error in TTS streaming: {e}", exc_info=True)
        try:
            await client_ws.send_json({
                "error": str(e),
                "status": "internal_error"
            })
        except Exception:
            pass
    
    finally:
        # Cancel relay task if running
        if relay_task and not relay_task.done():
            relay_task.cancel()
            try:
                await relay_task
            except asyncio.CancelledError:
                logger.debug("Relay task cancelled")
        
        # Clean up Sarvam connection
        if sarvam_ws:
            try:
                await sarvam_ws.close()
                logger.info("Sarvam streaming TTS connection closed")
            except Exception as e:
                logger.warning(f"Error closing Sarvam connection: {e}")



# ── Background task to relay Sarvam audio chunks ───────────────────────────────
async def relay_sarvam_audio(sarvam_ws, client_ws: WebSocket):
    """
    Continuously listen for audio chunks from Sarvam and relay to client.
    Runs as a background task when Sarvam connection is established.
    
    Sarvam sends:
    - audio: {audio: "base64-encoded-chunks"}
    - event: {event_type: "intermediate"|"final", ...}
    """
    try:
        async for message in sarvam_ws:
            try:
                # Parse message (Sarvam sends JSON for config/events, binary for audio)
                if isinstance(message, bytes):
                    # Binary audio chunk
                    try:
                        await client_ws.send_bytes(message)
                        logger.debug(f"Relayed {len(message)} bytes of audio from Sarvam")
                    except RuntimeError:
                        logger.info("Client disconnected, stopping audio relay")
                        break
                else:
                    # JSON message (config, event, error, etc)
                    data = json.loads(message)
                    msg_type = data.get("type", "").lower()
                    
                    if msg_type == "audio":
                        # Audio chunk in base64
                        audio_b64 = data.get("data", {}).get("audio", "")
                        if audio_b64:
                            try:
                                audio_bytes = base64.b64decode(audio_b64)
                                await client_ws.send_bytes(audio_bytes)
                                logger.debug(f"Relayed {len(audio_bytes)} bytes of audio")
                            except RuntimeError:
                                logger.info("Client disconnected, stopping audio relay")
                                break
                            except Exception as e:
                                logger.error(f"Error decoding audio: {e}")
                    
                    elif msg_type == "event":
                        # Completion or other events
                        event_type = data.get("data", {}).get("event_type", "")
                        await client_ws.send_json({
                            "type": "event",
                            "event_type": event_type,
                            "data": data.get("data", {})
                        })
                        logger.info(f"Relay event to client: {event_type}")
                        
                        if event_type == "final":
                            logger.info("TTS generation complete (final event received)")
                            break
                    
                    elif msg_type == "error":
                        # Error from Sarvam
                        error_msg = data.get("data", {}).get("error", "Unknown error")
                        await client_ws.send_json({
                            "type": "error",
                            "error": error_msg,
                            "status": "sarvam_error"
                        })
                        logger.error(f"Sarvam error: {error_msg}")
                        break
                    
                    else:
                        # Forward other message types as-is
                        await client_ws.send_json({
                            "type": "message",
                            "data": data
                        })
                        logger.debug(f"Relayed Sarvam message type: {msg_type}")
                        
            except json.JSONDecodeError:
                # Binary message or non-JSON data
                logger.debug(f"Received binary data from Sarvam")
                try:
                    await client_ws.send_bytes(message if isinstance(message, bytes) else message.encode())
                except Exception as e:
                    logger.error(f"Error relaying binary message: {e}")
    
    except asyncio.CancelledError:
        logger.debug("Audio relay task cancelled")
    except RuntimeError as e:
        # WebSocket closed
        logger.info(f"WebSocket closed during relay: {e}")
    except Exception as e:
        logger.error(f"Error relaying Sarvam audio: {e}", exc_info=True)
        try:
            await client_ws.send_json({
                "error": f"Relay error: {str(e)}",
                "status": "relay_error"
            })
        except Exception:
            pass



# ── REST Endpoint to Generate HTML Test Client for Streaming TTS ────────────

@router.get("/agent/{agent_id}/tts-stream-test")
async def get_tts_streaming_test_client(agent_id: str, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    """
    Returns an HTML page with a test client for the streaming TTS WebSocket endpoint.

    Usage:
    1. Visit: GET /agent/{agent_id}/tts-stream-test
    2. In browser: send config, then send text chunks, listen for audio

    Supported providers: Sarvam AI
    """
    result = await db.execute(
        select(AgentConfig).where(AgentConfig.id == agent_id)
    )
    agent = result.scalar_one_or_none()

    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    user.require_owns(str(agent.tenant_id))

    if agent.tts_provider != "sarvam":
        raise HTTPException(
            status_code=400,
            detail=f"Streaming TTS only available for Sarvam (agent uses {agent.tts_provider})"
        )
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Streaming TTS Test - {agent.agent_name}</title>
        <style>
            body {{ font-family: Arial, sans-serif; max-width: 900px; margin: 50px auto; padding: 20px; }}
            h1 {{ color: #333; }}
            .section {{ background: #f5f5f5; padding: 15px; margin: 15px 0; border-radius: 5px; }}
            input, textarea, button {{ padding: 10px; margin: 5px; border: 1px solid #ccc; border-radius: 3px; }}
            button {{ background: #007bff; color: white; cursor: pointer; }}
            button:hover {{ background: #0056b3; }}
            button:disabled {{ background: #ccc; cursor: not-allowed; }}
            #messages {{ background: white; height: 300px; overflow-y: auto; padding: 10px; border: 1px solid #ddd; margin: 10px 0; }}
            .log-entry {{ margin: 5px 0; padding: 5px; border-left: 3px solid #ccc; }}
            .log-entry.info {{ border-left-color: #0066cc; }}
            .log-entry.success {{ border-left-color: #00aa00; }}
            .log-entry.error {{ border-left-color: #cc0000; color: red; }}
            #audioPlayer {{ width: 100%; margin-top: 10px; }}
        </style>
    </head>
    <body>
        <h1>Streaming Text-to-Speech Test</h1>
        <p><strong>Agent:</strong> {agent.agent_name} (ID: {agent_id})</p>
        <p><strong>Provider:</strong> {agent.tts_provider} | <strong>Voice:</strong> {agent.tts_voice or 'default'} | <strong>Language:</strong> {agent.language or 'en-IN'}</p>
        
        <div class="section">
            <h2>1. Configuration</h2>
            <label>Speaker (voice):</label>
            <input type="text" id="speaker" value="{(agent.tts_voice or 'shubh').lower()}" placeholder="e.g., shubh, shreya">
            
            <label>Language Code:</label>
            <input type="text" id="language" value="{agent.language or 'en-IN'}" placeholder="e.g., hi-IN, en-IN">
            
            <label>Pace (speed):</label>
            <input type="number" id="pace" value="{agent.tts_pace or 1.0}" step="0.1" min="0.5" max="2.0">
            
            <label>Audio Codec:</label>
            <select id="codec">
                <option value="mp3">MP3</option>
                <option value="wav">WAV</option>
                <option value="aac">AAC</option>
                <option value="opus">OPUS</option>
            </select>
            
            <button onclick="sendConfig()">Send Configuration</button>
            <button onclick="connectWebSocket()">Connect</button>
            <button onclick="disconnectWebSocket()">Disconnect</button>
        </div>
        
        <div class="section">
            <h2>2. Send Text</h2>
            <textarea id="textInput" placeholder="Enter text to synthesize (max 2500 chars)" rows="4"></textarea>
            <p>Characters: <span id="charCount">0</span>/2500</p>
            <button onclick="sendText()" id="sendBtn" disabled>Send Text</button>
            <button onclick="flushBuffer()" id="flushBtn" disabled>Flush Buffer</button>
        </div>
        
        <div class="section">
            <h2>3. Audio Output</h2>
            <audio id="audioPlayer" controls></audio>
            <p><small>Audio will play progressively as chunks arrive.</small></p>
        </div>
        
        <div class="section">
            <h2>4. Status & Logs</h2>
            <p><strong>Connection:</strong> <span id="status">Disconnected</span></p>
            <p><strong>Chunks Received:</strong> <span id="chunkCount">0</span></p>
            <div id="messages"></div>
        </div>
        
        <script>
            let ws = null;
            let audioChunks = [];
            let isConnected = false;
            
            function log(msg, type = 'info') {{
                const messagesDiv = document.getElementById('messages');
                const entry = document.createElement('div');
                entry.className = `log-entry ${{type}}`;
                entry.textContent = `[${{new Date().toLocaleTimeString()}}] ${{msg}}`;
                messagesDiv.appendChild(entry);
                messagesDiv.scrollTop = messagesDiv.scrollHeight;
            }}
            
            function updateStatus(status) {{
                document.getElementById('status').textContent = status;
                const isConn = status === 'Connected';
                isConnected = isConn;
                document.getElementById('sendBtn').disabled = !isConn;
                document.getElementById('flushBtn').disabled = !isConn;
            }}
            
            function connectWebSocket() {{
                if (ws) return;
                
                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                const url = `${{protocol}}//${{window.location.host}}/ws/agent/{agent_id}/tts-stream`;
                
                ws = new WebSocket(url);
                
                ws.onopen = () => {{
                    log('WebSocket connected', 'success');
                    updateStatus('Connected');
                }};
                
                ws.onmessage = (event) => {{
                    if (event.data instanceof Blob) {{
                        // Audio chunk
                        audioChunks.push(event.data);
                        const chunkCount = parseInt(document.getElementById('chunkCount').textContent) + 1;
                        document.getElementById('chunkCount').textContent = chunkCount;
                        
                        // Update audio player
                        const audioBlob = new Blob(audioChunks, {{ type: 'audio/mpeg' }});
                        const audioUrl = URL.createObjectURL(audioBlob);
                        document.getElementById('audioPlayer').src = audioUrl;
                        
                        log(`Audio chunk #${{chunkCount}} received (${{(event.data.size / 1024).toFixed(2)}} KB)`, 'success');
                    }} else {{
                        // JSON message
                        const msg = JSON.parse(event.data);
                        const status = msg.status || msg.type;
                        log(`Message: ${{JSON.stringify(msg)}}`, msg.error ? 'error' : 'info');
                        
                        if (msg.event_type === 'final') {{
                            log('TTS generation complete!', 'success');
                        }}
                    }}
                }};
                
                ws.onerror = (error) => {{
                    log(`WebSocket error: ${{error}}`, 'error');
                    updateStatus('Error');
                }};
                
                ws.onclose = () => {{
                    log('WebSocket disconnected', 'info');
                    updateStatus('Disconnected');
                    ws = null;
                }};
            }}
            
            function disconnectWebSocket() {{
                if (ws) {{
                    ws.close();
                    ws = null;
                }}
            }}
            
            function sendConfig() {{
                if (!isConnected) {{
                    log('WebSocket not connected. Click "Connect" first.', 'error');
                    return;
                }}
                
                const config = {{
                    type: 'config',
                    data: {{
                        speaker: document.getElementById('speaker').value,
                        target_language_code: document.getElementById('language').value,
                        pace: parseFloat(document.getElementById('pace').value),
                        output_audio_codec: document.getElementById('codec').value
                    }}
                }};
                
                ws.send(JSON.stringify(config));
                log('Configuration sent', 'success');
                audioChunks = [];
                document.getElementById('chunkCount').textContent = '0';
            }}
            
            function sendText() {{
                if (!isConnected) {{
                    log('WebSocket not connected', 'error');
                    return;
                }}
                
                const text = document.getElementById('textInput').value.trim();
                if (!text) {{
                    log('Text input is empty', 'error');
                    return;
                }}
                
                const message = {{
                    type: 'text',
                    data: {{ text: text }}
                }};
                
                ws.send(JSON.stringify(message));
                log(`Text sent (${{text.length}} chars): "${{text.substring(0, 50)}}..."`, 'success');
            }}
            
            function flushBuffer() {{
                if (!isConnected) {{
                    log('WebSocket not connected', 'error');
                    return;
                }}
                
                ws.send(JSON.stringify({{ type: 'flush' }}));
                log('Buffer flushed - TTS processing started', 'success');
            }}
            
            document.getElementById('textInput').addEventListener('input', (e) => {{
                document.getElementById('charCount').textContent = e.target.value.length;
            }});
            
            // Auto-connect on load
            window.addEventListener('load', () => {{
                log('Page loaded. Click "Connect" to start.', 'info');
            }});
        </script>
    </body>
    </html>
    """
    
    return HTMLResponse(content=html)


# ── AUDIO / TEXT TURN HANDLERS ────────────────────────────────────────────────

async def handle_text_command(
    websocket: WebSocket,
    agent: AgentConfig,
    msg: dict,
    db: AsyncSession,
    session_id: str = ""
):
    """Fallback text chat via websocket"""
    if msg.get("type") == "transcript" and msg.get("text"):
        user_text = msg["text"].strip()
        await websocket.send_json({"type": "status", "status": "processing"})
        
        # Detect language from text and track it
        detected_lang = detect_text_language(user_text)
        if detected_lang and session_id:
            track_language(session_id, detected_lang)
        
        # ── Explicit language switch detection ──
        switched = detect_language_switch(user_text)
        if switched:
            _session_language_override[session_id] = switched
            logger.info("Language explicitly switched to: %s in text command", switched)

        # Use explicit override if set, else fall back to ratio-based dominant
        current_dominant = _session_language_override.get(
            session_id,
            get_dominant_language(session_id, agent.language or "en-IN"),
        )
        
        response_text = await generate_llm_response(agent, user_text, db, session_id=session_id, user_language=current_dominant, websocket=websocket)
        
        await websocket.send_json({
            "type": "agent_text", 
            "text": response_text,
            "detected_language": current_dominant
        })
        
        # Step 3: TTS - synthesize response for voice mode
        # Use the response text's actual script to pick TTS language
        # so TTS never speaks English text in Hindi voice or vice-versa
        response_lang = detect_text_language(response_text) or current_dominant
        tts_lang = normalize_sarvam_language(response_lang) if (agent.tts_provider or "sarvam") == "sarvam" else response_lang
        try:
            await websocket.send_json({"type": "status", "status": "speaking"})
        except RuntimeError:
            return  # connection closed
            
        try:
            audio_response = await synthesize_speech(agent, response_text, language_override=tts_lang)
            if audio_response:
                try:
                    await websocket.send_bytes(audio_response)
                except RuntimeError:
                    return
            else:
                try:
                    await websocket.send_json({
                        "type": "error",
                        "message": "TTS synthesis failed or API key missing."
                    })
                except RuntimeError:
                    return
        except Exception as e:
            logger.error(f"TTS synthesis failed for websocket: {e}")
            try:
                await websocket.send_json({
                    "type": "error",
                    "message": f"TTS synthesis error: {str(e)}"
                })
            except RuntimeError:
                pass
            
        try:
            await websocket.send_json({"type": "status", "status": "idle"})
        except RuntimeError:
            pass


# ── TTS pipelining ────────────────────────────────────────────────────────────
# Splits an already-final, already-safety-checked reply into sentence-ish
# chunks so handle_audio_turn can synthesize them CONCURRENTLY and send each
# one to the client as soon as it's ready, instead of paying one large TTS
# call's full latency before the caller hears anything. This must only ever
# run on text that has already been through _handle_booking_action — an
# [ACTION: ...] tag can appear anywhere in the model's raw output and triggers
# a full replacement of the reply with a DB-verified outcome (never confirm a
# booking before the row exists), so speaking any partial text before that
# check has run would risk speaking words that get thrown away.
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?।])\s+')  # । = Hindi/Devanagari danda


def _split_into_speakable_chunks(text: str, min_chunk_len: int = 12) -> list[str]:
    """Split final reply text into TTS-sized chunks for pipelined synthesis.

    Only affects chunk BOUNDARIES for pipelining — never correctness. A bad
    split (e.g. on "Dr.") costs an awkward pause, not wrong spoken content, so
    this is a pragmatic splitter, not a linguistic sentence tokenizer. Any
    fragment shorter than min_chunk_len is merged into the previous chunk
    (avoids a lone "Dr." or stray punctuation becoming its own TTS call).
    """
    text = (text or "").strip()
    if not text:
        return []
    raw_parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text) if p.strip()]
    if len(raw_parts) <= 1:
        return raw_parts

    # Accumulate fragments into a running buffer until it reaches min_chunk_len,
    # then emit it as one chunk and start a new buffer. This (rather than
    # merging a short fragment into whatever chunk came before it) is what
    # correctly folds a leading short fragment like "Dr." into the sentence
    # that follows it, since there's no "previous chunk" to merge into yet.
    chunks: list[str] = []
    buf = ""
    for part in raw_parts:
        buf = f"{buf} {part}".strip() if buf else part
        if len(buf) >= min_chunk_len:
            chunks.append(buf)
            buf = ""
    if buf:
        if chunks:
            chunks[-1] = f"{chunks[-1]} {buf}"
        else:
            chunks.append(buf)
    return chunks


async def handle_audio_turn(
    websocket: WebSocket,
    agent: AgentConfig,
    audio_bytes: bytes,
    db: AsyncSession,
    session_id: str = ""
):
    """Process one turn of audio: STT -> LLM -> TTS -> send back.
    Issues 3+5: adds per-stage timing, tts_failed event, and graceful fallback."""

    # Send "processing" status
    try:
        await websocket.send_json({"type": "status", "status": "processing"})
    except RuntimeError:
        return

    turn_start = time.monotonic()

    try:
        # ── Check for session cancellation (user closed widget) ────────────────
        _cancel = _session_cancelled.get(session_id)
        if _cancel and _cancel.is_set():
            logger.info("Session %s cancelled, aborting audio turn", session_id)
            return

        # Determine which language to use for STT based on ratio tracking
        dominant_lang = get_dominant_language(session_id, agent.language or "en-IN")

        # ── Step 1: STT ──────────────────────────────────────────────────────────
        stt_start = time.monotonic()
        transcript, detected_lang = await transcribe_audio(agent, audio_bytes, language_hint=dominant_lang)
        stt_ms = int((time.monotonic() - stt_start) * 1000)
        logger.info(f"[TIMING] STT: {stt_ms}ms")

        if not transcript or transcript.strip() == "":
            try:
                await websocket.send_json({
                    "type": "error",
                    "message": "No speech detected. Try speaking closer to the mic, or check that microphone permission is allowed.",
                    "code": "STT_EMPTY_TRANSCRIPT",
                })
                await websocket.send_json({"type": "status", "status": "idle"})
            except RuntimeError:
                pass
            return

        # ── Post-STT text verification + smart retry ─────────────────────────
        # Always use detect_text_language() as ground truth — Sarvam's
        # language_code return can disagree with the actual script.
        text_lang = detect_text_language(transcript)
        if text_lang:
            detected_lang = text_lang  # trust script detection over STT header

        # SMART RETRY: If the text script contradicts the session's established
        # language history, Sarvam likely misdetected. Retry with the dominant
        # language explicitly (force_language=True bypasses "unknown").
        # This only fires when we have enough history (≥2 utterances) to be
        # confident about the user's language, so latency impact is minimal.
        session_langs = _language_tracker.get(session_id, [])
        if (
            text_lang
            and len(session_langs) >= 2
            and text_lang != dominant_lang
        ):
            logger.info(
                "STT misdetection suspected: transcript in %s but session dominant is %s — retrying with explicit %s",
                text_lang, dominant_lang, dominant_lang,
            )
            try:
                retry_transcript, retry_detected = await transcribe_audio(
                    agent, audio_bytes,
                    language_hint=dominant_lang,
                    force_language=True,  # bypass "unknown", send dominant_lang explicitly
                )
                if retry_transcript and retry_transcript.strip():
                    retry_text_lang = detect_text_language(retry_transcript)
                    # Use the retry result if it matches the dominant language
                    if retry_text_lang == dominant_lang:
                        logger.info(
                            "STT retry succeeded: '%s' (%s) → using retry result",
                            retry_transcript[:60], retry_text_lang,
                        )
                        transcript = retry_transcript
                        detected_lang = retry_text_lang
                        text_lang = retry_text_lang
                    else:
                        # Retry also produced different script — it's a genuine
                        # language switch, not a misdetection. Keep original.
                        logger.info(
                            "STT retry also produced %s — accepting language switch",
                            retry_text_lang,
                        )
            except Exception as retry_err:
                logger.warning("STT retry failed (non-fatal): %s", retry_err)

        # Track detected language for ratio-based switching
        if detected_lang and session_id:
            track_language(session_id, detected_lang)

        # ── BUG 2: Explicit language switch detection ──────────────────────────
        switched = detect_language_switch(transcript)
        if switched:
            _session_language_override[session_id] = switched
            logger.info("Language explicitly switched to: %s", switched)

        # Use explicit override if set, else fall back to ratio-based dominant
        current_dominant = _session_language_override.get(
            session_id,
            get_dominant_language(session_id, agent.language or "en-IN"),
        )

        # Send user transcript back
        try:
            await websocket.send_json({
                "type": "transcript",
                "text": transcript,
                "role": "user",
                "detected_language": current_dominant,
            })
        except RuntimeError:
            return

        logger.info(f"Transcribed: '{transcript[:80]}' (lang: {detected_lang}, dominant: {current_dominant})")

        # ── End-call phrase detection ──────────────────────────────────────────
        # Check if the user said a goodbye/end phrase configured on the agent.
        end_phrases = agent.end_call_phrases or ["bye", "goodbye", "thank you", "dhanyavaad", "shukriya", "alvida"]
        transcript_lower = transcript.lower().strip()
        # Match: transcript IS an end phrase, or ends with one, or contains one
        # as a standalone segment. Use word-level matching to avoid false positives
        # (e.g. "goodbye" should match but "good" alone shouldn't).
        is_end_phrase = any(
            phrase.lower() in transcript_lower
            for phrase in end_phrases
            if phrase and len(phrase.strip()) >= 2
        )
        if is_end_phrase:
            logger.info("End-call phrase detected in transcript: '%s'", transcript[:60])
            end_msg = agent.end_call_message or "Thank you for calling. Have a great day!"
            # Send the farewell transcript
            try:
                await websocket.send_json({
                    "type": "transcript",
                    "text": end_msg,
                    "role": "assistant",
                    "detected_language": current_dominant,
                })
            except RuntimeError:
                return
            # Synthesize farewell audio
            try:
                farewell_lang = detect_text_language(end_msg) or current_dominant
                farewell_tts_lang = normalize_sarvam_language(farewell_lang) if (agent.tts_provider or "sarvam") == "sarvam" else farewell_lang
                farewell_audio = await synthesize_speech(agent, end_msg, language_override=farewell_tts_lang)
                if farewell_audio:
                    await websocket.send_bytes(farewell_audio)
            except Exception as e:
                logger.warning("Farewell TTS failed (non-fatal): %s", e)
            # Signal the call has ended
            try:
                await websocket.send_json({"type": "status", "status": "ended", "reason": "end_phrase_detected"})
            except RuntimeError:
                pass
            return  # exit the turn — the main loop will see the 'ended' status

        # ── Check cancellation before LLM (most expensive step) ────────────────
        if _cancel and _cancel.is_set():
            logger.info("Session %s cancelled before LLM, aborting", session_id)
            return

        # ── Step 2: LLM ──────────────────────────────────────────────────────────
        try:
            await websocket.send_json({"type": "status", "status": "thinking"})
        except RuntimeError:
            return

        # ── BUG 4: Duplicate greeting prevention ─────────────────────────────
        turn_count = _session_turn_count.get(session_id, 0)
        if turn_count == 0 and transcript.lower().strip() in SIMPLE_GREETINGS:
            response_text = "How can I help you today?"
            llm_ms = 0
            logger.info("[TIMING] LLM: skipped (simple greeting on turn 0)")
        else:
            llm_start = time.monotonic()
            try:
                response_text = await asyncio.wait_for(
                    generate_llm_response(agent, transcript, db, session_id=session_id, user_language=current_dominant, websocket=websocket),
                    timeout=15.0,
                )
            except asyncio.TimeoutError:
                logger.warning("LLM timeout — client may have disconnected")
                if _cancel and _cancel.is_set():
                    return
                response_text = "I'm sorry, I took too long to respond. Could you please repeat?"
            llm_ms = int((time.monotonic() - llm_start) * 1000)
            logger.info(f"[TIMING] LLM: {llm_ms}ms — '{response_text[:80]}'")
        _session_turn_count[session_id] = turn_count + 1

        # ── Detect response language from actual script for TTS ──────────────────
        # This ensures TTS never speaks English text in Hindi voice or vice-versa.
        response_lang = detect_text_language(response_text) or current_dominant
        tts_lang_for_turn = normalize_sarvam_language(response_lang) if (agent.tts_provider or "sarvam") == "sarvam" else response_lang

        # Send agent transcript back
        try:
            await websocket.send_json({
                "type": "transcript",
                "text": response_text,
                "role": "assistant",
                "detected_language": response_lang,
            })
        except RuntimeError:
            return

        # ── Step 3: TTS ──────────────────────────────────────────────────────────
        try:
            await websocket.send_json({"type": "status", "status": "speaking"})
        except RuntimeError:
            return

        tts_ms = 0
        ttfa_ms = 0  # time-to-first-audio — the metric the pipelining below improves
        tts_ok = False
        # Get the interrupt event for this session (barge-in support)
        _interrupt = _agent_speaking.get(session_id)
        if _interrupt:
            _interrupt.clear()  # reset before starting new TTS

        def _make_tts_coro(text_chunk: str):
            # Use retry-capable TTS wrapper for sarvam, else generic
            return sarvam_synthesize_with_retry(
                agent, text_chunk, language_override=tts_lang_for_turn
            ) if (agent.tts_provider or "sarvam") == "sarvam" else synthesize_speech(
                agent, text_chunk, language_override=tts_lang_for_turn
            )

        # response_text is already final and already past _handle_booking_action
        # (see the note on _split_into_speakable_chunks) — safe to chunk here.
        speakable_chunks = _split_into_speakable_chunks(response_text)

        try:
            tts_start = time.monotonic()

            if len(speakable_chunks) <= 1:
                # ── Single-sentence replies: unchanged from before pipelining ──
                # BUG 3: Wrap TTS with timeout + cancellation check
                try:
                    audio_response = await asyncio.wait_for(
                        _make_tts_coro(response_text), timeout=10.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("TTS timeout — client may have disconnected")
                    if _cancel and _cancel.is_set():
                        return
                    audio_response = b""
                tts_ms = int((time.monotonic() - tts_start) * 1000)
                logger.info(f"[TIMING] TTS: {tts_ms}ms ({len(audio_response) if audio_response else 0} bytes)")

                if audio_response and len(audio_response) >= 512:  # sanity-check non-empty
                    # Check if user interrupted BEFORE we send
                    if _interrupt and _interrupt.is_set():
                        logger.info("TTS send aborted — interrupt received for session %s", session_id)
                        tts_ok = False
                    elif _cancel and _cancel.is_set():
                        logger.info("Session cancelled before TTS send for %s", session_id)
                        return
                    else:
                        tts_ok = True
                        try:
                            await websocket.send_bytes(audio_response)
                        except Exception:
                            return
                        ttfa_ms = tts_ms
                        # BUG 1: Set agent_speaking_until so incoming mic audio is discarded
                        # Calculate audio duration: 16-bit PCM @ 24kHz (Sarvam default) ≈ 48000 bytes/sec
                        audio_duration_s = len(audio_response) / 48000.0
                        _agent_speaking_until[session_id] = time.time() + audio_duration_s + 0.8
                        logger.info(
                            "Agent speaking for %.1fs (audio=%d bytes, buffer=0.8s)",
                            audio_duration_s + 0.8, len(audio_response),
                        )
                else:
                    logger.warning(f"TTS returned empty/tiny response ({len(audio_response) if audio_response else 0}B) — sending tts_failed")

            else:
                # ── Multi-sentence replies: pipelined synthesis ────────────────
                # Kick off every chunk's synthesis CONCURRENTLY so chunk 2+'s
                # TTS runs while chunk 1 is being awaited/sent — by the time
                # chunk 1 is on the wire, chunk 2 is often already done or
                # close to it. Sent to the client strictly IN ORDER; the
                # frontend already queues and plays multiple sequential audio
                # messages per turn (TestAgentModal.tsx's audioQueueRef), so
                # this needs no protocol or frontend change.
                synth_tasks = [asyncio.create_task(_make_tts_coro(c)) for c in speakable_chunks]
                speaking_until = time.time()
                any_sent = False
                for i, task in enumerate(synth_tasks):
                    if _cancel and _cancel.is_set():
                        for t in synth_tasks[i:]:
                            t.cancel()
                        return
                    if _interrupt and _interrupt.is_set():
                        logger.info(
                            "TTS send aborted mid-stream — interrupt received for session %s "
                            "(chunk %d/%d)", session_id, i + 1, len(synth_tasks),
                        )
                        for t in synth_tasks[i:]:
                            t.cancel()
                        break

                    try:
                        chunk_audio = await asyncio.wait_for(task, timeout=10.0)
                    except asyncio.TimeoutError:
                        logger.warning(
                            "TTS chunk %d/%d timed out — client may have disconnected",
                            i + 1, len(synth_tasks),
                        )
                        if _cancel and _cancel.is_set():
                            return
                        chunk_audio = b""
                    except Exception as chunk_err:
                        logger.error(
                            "TTS chunk %d/%d failed: %s", i + 1, len(synth_tasks), chunk_err
                        )
                        chunk_audio = b""

                    if not (chunk_audio and len(chunk_audio) >= 512):
                        logger.warning(
                            "TTS chunk %d/%d empty/tiny (%dB) — stopping remaining chunks",
                            i + 1, len(synth_tasks), len(chunk_audio) if chunk_audio else 0,
                        )
                        for t in synth_tasks[i + 1:]:
                            t.cancel()
                        break

                    try:
                        await websocket.send_bytes(chunk_audio)
                    except Exception:
                        for t in synth_tasks[i + 1:]:
                            t.cancel()
                        return
                    any_sent = True
                    if ttfa_ms == 0:
                        ttfa_ms = int((time.monotonic() - tts_start) * 1000)
                    # Echo suppression must cover the WHOLE multi-chunk
                    # utterance — extend forward cumulatively, don't overwrite.
                    chunk_duration_s = len(chunk_audio) / 48000.0
                    speaking_until = max(speaking_until, time.time()) + chunk_duration_s

                tts_ms = int((time.monotonic() - tts_start) * 1000)
                tts_ok = any_sent
                if any_sent:
                    _agent_speaking_until[session_id] = speaking_until + 0.8
                    logger.info(
                        "[TIMING] TTS (pipelined, %d chunks): %dms total, first audio at %dms",
                        len(speakable_chunks), tts_ms, ttfa_ms,
                    )
                else:
                    logger.warning("TTS pipelined path produced no audio at all")

        except WebSocketDisconnect:
            # Client hung up mid-TTS — normal on mobile when user closes tab,
            # navigates away, or backgrounds Safari. Bubble up to outer except
            # which handles it as an info-level event (no traceback spam).
            raise
        except Exception as tts_err:
            tts_ms = int((time.monotonic() - tts_start) * 1000)  # type: ignore[possibly-unbound]
            err_low = str(tts_err).lower()
            if any(k in err_low for k in ("disconnect", "clientdisconnected", "closed", "1005", "1006", "1000")):
                # Treat send-side disconnects as a normal hangup, not an error.
                logger.info("Client disconnected during TTS send for agent %s after %dms", agent.id, tts_ms)
                raise WebSocketDisconnect(code=1006)
            logger.error(f"[TIMING] TTS FAILED in {tts_ms}ms: {tts_err}", exc_info=True)

        # ISSUE 5: If TTS failed, tell frontend so it shows the badge + keeps transcript visible
        if not tts_ok:
            try:
                await websocket.send_json({
                    "type": "tts_failed",
                    "message": response_text,
                    "reason": "TTS synthesis failed — check API key and provider settings.",
                })
            except RuntimeError:
                pass

        # ISSUE 3: Send timing breakdown to frontend
        total_ms = int((time.monotonic() - turn_start) * 1000)
        try:
            await websocket.send_json({
                "type": "timing",
                "stt_ms": stt_ms,
                "llm_ms": llm_ms,
                "tts_ms": tts_ms,
                "ttfa_ms": ttfa_ms,
                "total_ms": total_ms,
            })
        except RuntimeError:
            pass

        try:
            await websocket.send_json({"type": "status", "status": "idle"})
        except RuntimeError:
            pass

    except (WebSocketDisconnect, RuntimeError):
        # Client disconnected mid-turn — this is normal (user closed tab/navigated away)
        logger.info("Client disconnected during audio turn for agent %s", agent.id)
    except Exception as e:
        # Only log as error if it's a genuine processing failure, not a disconnect
        err_str = str(e).lower()
        if any(k in err_str for k in ("disconnect", "closed", "1005", "1006", "1000")):
            logger.info("Client disconnected during audio turn for agent %s", agent.id)
        else:
            logger.error(f"Error in audio turn: {e}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": f"Processing error: {str(e)}"})
        except (RuntimeError, WebSocketDisconnect, Exception):
            pass


# ── STT Logic ─────────────────────────────────────────────────────────────────

async def transcribe_audio(agent: AgentConfig, audio_bytes: bytes, language_hint: str = "", force_language: bool = False) -> tuple[str, str]:
    """Transcribe audio bytes to text using configured STT provider.
    Returns (transcript, detected_language_code).
    
    When force_language=True, the language_hint is sent as-is to STT
    (used for retry calls when auto-detect misidentified the language).
    """
    
    stt_provider = agent.stt_provider or "sarvam"
    
    # Get API key from environment (avoid opening a new DB session to prevent pool exhaustion)
    api_key = None
    if stt_provider == "sarvam":
        api_key = settings.sarvam_api_key or os.getenv("SARVAM_API_KEY")
    elif stt_provider == "deepgram":
        api_key = settings.deepgram_api_key or os.getenv("DEEPGRAM_API_KEY")
    elif stt_provider == "openai_whisper":
        api_key = os.getenv("OPENAI_API_KEY")
    
    if not api_key:
        logger.warning(f"No API key for STT provider: {stt_provider}")
        return "", ""
    
    from backend.services import stt_catalog

    # agent.stt_language is stored CANONICALLY — "auto" for auto-detect, otherwise
    # BCP-47. It is translated into each provider's own spelling at the call sites
    # below, never sent verbatim: Sarvam answers 400 for anything outside its 24
    # enumerated codes (so "auto" is a 400), and Deepgram wants "kn"/"multi", not
    # "kn-IN". Keep `lang` canonical up here so both branches translate from the
    # same value.
    lang = language_hint or agent.language or "en-IN"

    if stt_provider == "sarvam":
        stt_model = agent.stt_model or "saaras:v3"
        if (
            getattr(agent, "auto_detect_language", True)
            and stt_model.startswith("saaras")
            and not force_language
        ):
            # Use "unknown" for Sarvam auto-detect. This lets the STT model
            # identify the spoken language from the audio itself, instead of
            # forcing the agent's configured language (which would transcribe
            # English speech as Malayalam/Hindi/Tamil if the agent is set up
            # for those languages).
            # 
            # Misdetections are caught by the post-STT smart retry in
            # handle_audio_turn() which re-calls with force_language=True.
            lang = "unknown"
        else:
            # When force_language=True, 'lang' keeps the explicit language_hint
            # value — used for retry calls after misdetection. Translated so a
            # saarika model never receives a saaras-only code, and so "auto"
            # becomes Sarvam's own "unknown" instead of a 400.
            lang = stt_catalog.to_provider_code("sarvam", stt_model, lang) or "unknown"
        return await sarvam_transcribe(api_key, audio_bytes, stt_model, lang)

    elif stt_provider == "deepgram":
        # Empty language ⇒ Deepgram auto-detect (nova-2) / multi (nova-3), same
        # policy as the Sarvam branch above. A retry pins the language.
        dg_lang = ""
        if force_language or not getattr(agent, "auto_detect_language", True):
            dg_lang = stt_catalog.to_provider_code(
                "deepgram", agent.stt_model or "nova-3", lang
            ) or ""
        return await deepgram_transcribe(
            api_key, audio_bytes, agent.stt_model or "nova-3", language=dg_lang
        )
    
    elif stt_provider == "openai_whisper":
        return await openai_transcribe(api_key, audio_bytes)
    
    else:
        logger.warning(f"Unknown STT provider: {stt_provider}")
        return "", ""


def _detect_audio_upload_format(audio_bytes: bytes) -> tuple[str, str]:
    """Best-effort detection of container/codec for multipart upload metadata.
    Returns (filename, mime_type)."""
    if not audio_bytes or len(audio_bytes) < 12:
        return "audio.wav", "audio/wav"

    # WAV (RIFF....WAVE)
    if audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
        return "audio.wav", "audio/wav"

    # OGG/Opus
    if audio_bytes[:4] == b"OggS":
        return "audio.ogg", "audio/ogg"

    # WebM/Matroska (EBML header)
    if audio_bytes[:4] == b"\x1a\x45\xdf\xa3":
        return "audio.webm", "audio/webm"

    # MP3 (ID3 or frame sync)
    if audio_bytes[:3] == b"ID3" or (audio_bytes[0] == 0xFF and (audio_bytes[1] & 0xE0) == 0xE0):
        return "audio.mp3", "audio/mpeg"

    # MP4/M4A (ftyp box)
    if audio_bytes[4:8] == b"ftyp":
        return "audio.m4a", "audio/mp4"

    # Default fallback
    return "audio.wav", "audio/wav"


def detect_text_language(text: str) -> str:
    """Simple heuristic language detection based on character scripts.
    Returns language code like 'hi-IN', 'en-IN', 'ta-IN', etc."""
    if not text:
        return ""
    
    # Count characters by Unicode script
    devanagari = 0  # Hindi, Marathi, Sanskrit
    tamil = 0
    telugu = 0
    kannada = 0
    malayalam = 0
    bengali = 0
    gujarati = 0
    latin = 0
    total = 0
    
    for ch in text:
        cp = ord(ch)
        if ch.isalpha():
            total += 1
            if 0x0900 <= cp <= 0x097F:
                devanagari += 1
            elif 0x0B80 <= cp <= 0x0BFF:
                tamil += 1
            elif 0x0C00 <= cp <= 0x0C7F:
                telugu += 1
            elif 0x0C80 <= cp <= 0x0CFF:
                kannada += 1
            elif 0x0D00 <= cp <= 0x0D7F:
                malayalam += 1
            elif 0x0980 <= cp <= 0x09FF:
                bengali += 1
            elif 0x0A80 <= cp <= 0x0AFF:
                gujarati += 1
            elif 0x0041 <= cp <= 0x007A:
                latin += 1
    
    if total == 0:
        return ""
    
    # Map script to language code
    scripts = {
        "hi-IN": devanagari,
        "ta-IN": tamil,
        "te-IN": telugu,
        "kn-IN": kannada,
        "ml-IN": malayalam,
        "bn-IN": bengali,
        "gu-IN": gujarati,
        "en-IN": latin,
    }
    
    dominant = max(scripts, key=lambda k: scripts[k])
    if scripts[dominant] / total >= 0.3:
        return dominant
    return "en-IN"


async def sarvam_transcribe(api_key: str, audio_bytes: bytes, 
                             model: str, language: str) -> tuple[str, str]:
    """Call Sarvam STT API. Returns (transcript, detected_language).
    
    Handles browser WebM/Opus audio by converting to WAV when needed.
    Includes retry logic for transient API failures.
    """
    import httpx
    import io

    if not audio_bytes or len(audio_bytes) < 100:
        logger.warning("STT: audio too small (%d bytes), skipping", len(audio_bytes) if audio_bytes else 0)
        return "", ""

    # Sarvam docs recommend multipart upload with file + model + mode
    normalized_model = model if model and (model.startswith("saarika") or model.startswith("saaras")) else "saaras:v3"

    # ── Auto-remap deprecated Sarvam STT models ──────────────────────────────
    # Sarvam returns HTTP 400 "Model 'saarika:v2' has been deprecated. Please
    # use 'saarika:v2.5' instead." for older model IDs. Silently upgrade so
    # legacy agent configs keep working without manual intervention.
    DEPRECATED_SARVAM_STT_MODELS = {
        "saarika:v1": "saarika:v2.5",
        "saarika:v2": "saarika:v2.5",
        "saaras:v1": "saaras:v3",
        "saaras:v2": "saaras:v3",
    }
    if normalized_model in DEPRECATED_SARVAM_STT_MODELS:
        new_stt = DEPRECATED_SARVAM_STT_MODELS[normalized_model]
        logger.info("Sarvam STT: remapping deprecated model '%s' → '%s'", normalized_model, new_stt)
        normalized_model = new_stt
    upload_name, upload_mime = _detect_audio_upload_format(audio_bytes)
    
    # ── Convert WebM/Opus to WAV for Sarvam compatibility ──────────────────
    # Browser MediaRecorder sends WebM/Opus which Sarvam can reject.
    # Convert to 16kHz mono WAV (Sarvam's preferred format).
    processed_bytes = audio_bytes
    if upload_mime in ("audio/webm", "audio/ogg"):
        original_mime = upload_mime
        original_name = upload_name
        try:
            # ffmpeg transcode is blocking (subprocess + file I/O). Run it in a
            # worker thread so it does NOT stall the event loop — otherwise every
            # WebM/Opus turn serializes ALL concurrent calls (audit P2).
            loop = asyncio.get_running_loop()
            processed_bytes = await loop.run_in_executor(None, _convert_to_wav_pcm, audio_bytes)
            upload_name = "audio.wav"
            upload_mime = "audio/wav"
            logger.info("STT: Converted %s → WAV (%d → %d bytes)", 
                       original_mime, len(audio_bytes), len(processed_bytes))
        except Exception as conv_err:
            logger.info("STT: ffmpeg conversion unavailable (%s), sending raw %s", conv_err, original_mime)
            # CRITICAL: Keep original mime type — do NOT relabel as WAV
            processed_bytes = audio_bytes
            upload_name = original_name
            upload_mime = original_mime

    max_retries = 2
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            files = {
                "file": (upload_name, io.BytesIO(processed_bytes), upload_mime)
            }
            form_data = {
                "model": normalized_model,
                "mode": "transcribe",
                "language_code": language,
            }

            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    "https://api.sarvam.ai/speech-to-text",
                    headers={"api-subscription-key": api_key},
                    data=form_data,
                    files=files,
                )
                
                if response.status_code == 200:
                    data = response.json()
                    transcript = data.get("transcript", "")
                    # Sarvam returns language_code in response
                    detected = data.get("language_code", language)
                    # Also try to detect from text content if Sarvam doesn't return it
                    if not detected or detected == language:
                        text_lang = detect_text_language(transcript)
                        if text_lang:
                            detected = text_lang
                    if attempt > 1:
                        logger.info("STT succeeded on attempt %d", attempt)
                    return transcript, detected
                else:
                    last_err = f"HTTP {response.status_code}: {response.text[:200]}"
                    logger.warning(
                        "Sarvam STT attempt %d/%d error: %s (upload=%s, mime=%s, size=%d)",
                        attempt, max_retries, last_err, upload_name, upload_mime, len(processed_bytes),
                    )
        except Exception as exc:
            last_err = str(exc)
            logger.warning("Sarvam STT attempt %d/%d exception: %s", attempt, max_retries, exc)
        
        if attempt < max_retries:
            await asyncio.sleep(0.3 * attempt)

    logger.error("Sarvam STT failed after %d attempts. Last error: %s", max_retries, last_err)
    return "", ""


def _convert_to_wav_pcm(audio_bytes: bytes) -> bytes:
    """Convert browser audio (WebM/Opus/OGG) to 16kHz mono 16-bit PCM WAV.
    
    Uses ffmpeg subprocess (available on Render/Linux).
    Falls back to returning original bytes if ffmpeg is unavailable.
    """
    import subprocess
    import tempfile
    import os

    # If already WAV, return as-is
    if audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
        return audio_bytes

    # Use ffmpeg for real transcoding
    tmp_in = None
    tmp_out = None
    try:
        # Write input to temp file
        tmp_in = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
        tmp_in.write(audio_bytes)
        tmp_in.flush()
        tmp_in.close()

        tmp_out_path = tmp_in.name.replace(".webm", ".wav")

        # ffmpeg: convert to 16kHz mono 16-bit PCM WAV (Sarvam's preferred format)
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", tmp_in.name,
                "-ar", "16000",      # 16kHz sample rate
                "-ac", "1",          # Mono
                "-sample_fmt", "s16", # 16-bit signed PCM
                "-f", "wav",
                tmp_out_path,
            ],
            capture_output=True,
            timeout=10,
        )

        if result.returncode == 0 and os.path.exists(tmp_out_path):
            with open(tmp_out_path, "rb") as f:
                wav_bytes = f.read()
            if len(wav_bytes) > 44:  # Valid WAV must be > 44 bytes (header)
                return wav_bytes

        # ffmpeg failed — log and raise so caller falls back
        stderr = result.stderr.decode("utf-8", errors="replace")[:200] if result.stderr else "unknown"
        raise RuntimeError(f"ffmpeg returned {result.returncode}: {stderr}")

    finally:
        # Cleanup temp files
        for path in [tmp_in.name if tmp_in else None, tmp_out_path if 'tmp_out_path' in dir() else None]:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


DEEPGRAM_LISTEN_URL = "https://api.deepgram.com/v1/listen"

# BCP-47 (our internal codes) → Deepgram language codes for the pre-recorded API.
# Anything not listed falls back to auto-detect rather than risking a 400.
_DEEPGRAM_LANGS = {
    "en-IN": "en-IN", "en-US": "en-US", "en-GB": "en-GB", "en": "en",
    "hi-IN": "hi", "hi": "hi",
    "ta-IN": "ta", "ta": "ta",
    "te-IN": "te", "te": "te",
    "ml-IN": "ml", "ml": "ml",
    "mr-IN": "mr", "mr": "mr",
    "bn-IN": "bn", "bn": "bn",
    "gu-IN": "gu", "gu": "gu",
    "pa-IN": "pa", "pa": "pa",
    "kn-IN": "kn", "kn": "kn",
}


def _deepgram_language_param(model: str, language: str) -> dict:
    """Query params controlling Deepgram language selection.

    Mirrors the live pipeline's rules — see the verified support matrix in
    backend/agent/pipeline.py (_DG_NOVA3_MULTI_LANGS / _DG_UNSUPPORTED_LANGS).
    nova-3 covers en/hi via "multi" and ta/te/kn/mr/bn/gu via their own codes;
    nova-2 rejects every Indic language with HTTP 400. An empty ``language``
    means auto-detect.

    This previously sent EVERY non-English language to nova-3 as "multi", which
    nova-3 does not support for ta/te/kn/mr/bn/gu — the same 400 the live pipeline
    was hitting from the opposite direction.
    """
    # From provider_registry (pipecat-free), NOT backend.agent.pipeline — this
    # router runs in the API process, which has no pipecat installed, so importing
    # the pipeline here raised `No module named 'pipecat'` mid-request.
    from backend.services.provider_registry import (
        DEEPGRAM_NOVA3_MULTI_LANGS as _DG_NOVA3_MULTI_LANGS,
        DEEPGRAM_UNSUPPORTED_LANGS as _DG_UNSUPPORTED_LANGS,
    )

    base = (language or "").split("-")[0]

    if model.startswith("nova-3"):
        if not base:
            return {"language": "multi"}
        if base in _DG_UNSUPPORTED_LANGS:
            # Deepgram cannot do these on any tier; let it auto-detect rather than
            # sending a language it will reject outright.
            return {"detect_language": "true"}
        return {"language": "multi" if base in _DG_NOVA3_MULTI_LANGS else base}

    # nova-2 and earlier: only English variants and Hindi are accepted. Every other
    # Indic code returns HTTP 400, so auto-detect instead of guaranteeing a failure.
    dg = _DEEPGRAM_LANGS.get(language) or _DEEPGRAM_LANGS.get(base, "")
    if dg and (dg.startswith("en") or dg == "hi"):
        return {"language": dg}
    return {"detect_language": "true"}


async def deepgram_transcribe(
    api_key: str,
    audio_bytes: bytes | None,
    model: str = "nova-3",
    language: str = "",
    audio_url: str | None = None,
) -> tuple[str, str]:
    """Deepgram pre-recorded STT via ``POST /v1/listen``.

    This is the REST call from Deepgram's console example::

        curl -X POST \
          -H "Authorization: Token $DEEPGRAM_API_KEY" \
          -H 'content-type: application/json' \
          -d '{"url": "https://static.deepgram.com/examples/Bueller-Life-moves-pretty-fast.wav"}' \
          "https://api.deepgram.com/v1/listen?model=nova-2&smart_format=true"

    Same endpoint and auth header, two interchangeable bodies:

    * ``audio_url``   → ``content-type: application/json``, body ``{"url": ...}``
      (the curl above, for audio already hosted somewhere reachable)
    * ``audio_bytes`` → ``content-type: <sniffed audio mime>``, body = raw audio
      (what the test-agent WebSocket path has: a recorded browser chunk)

    Returns ``(transcript, detected_language)`` — ``("", "")`` on any failure so
    callers degrade the same way the other STT providers do.

    Note this is the *batch* API. The live LiveKit pipeline uses Deepgram's
    streaming WebSocket via pipecat (see ``backend/agent/pipeline.py``); this
    function is only for whole-utterance audio.
    """
    import httpx

    if not api_key:
        logger.warning("Deepgram STT: no API key")
        return "", ""
    if not audio_url and not audio_bytes:
        logger.warning("Deepgram STT: no audio supplied")
        return "", ""

    params = {"model": model or "nova-3", "smart_format": "true", "punctuate": "true"}
    params.update(_deepgram_language_param(params["model"], language))

    if audio_url:
        headers = {"Authorization": f"Token {api_key}", "content-type": "application/json"}
        body: dict = {"json": {"url": audio_url}}
    else:
        _, mime = _detect_audio_upload_format(audio_bytes)
        headers = {"Authorization": f"Token {api_key}", "content-type": mime}
        body = {"content": audio_bytes}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(DEEPGRAM_LISTEN_URL, params=params, headers=headers, **body)

            # An unsupported language for this model is a 400 — retry on
            # auto-detect instead of losing the whole utterance.
            if resp.status_code == 400 and "language" in params:
                logger.warning(
                    "Deepgram STT rejected language=%s for %s (%s) — retrying with detect_language",
                    params["language"], params["model"], resp.text[:200],
                )
                params.pop("language")
                params["detect_language"] = "true"
                resp = await client.post(DEEPGRAM_LISTEN_URL, params=params, headers=headers, **body)

            if resp.status_code != 200:
                logger.error("Deepgram STT error %s: %s", resp.status_code, resp.text[:300])
                return "", ""

            data = resp.json()
    except Exception as exc:
        logger.error("Deepgram STT exception: %s", exc)
        return "", ""

    try:
        channel = data["results"]["channels"][0]
        transcript = (channel["alternatives"][0].get("transcript") or "").strip()
    except (KeyError, IndexError, TypeError):
        logger.error("Deepgram STT: unexpected response shape: %s", str(data)[:300])
        return "", ""

    # Deepgram reports ISO 639-1 when detect_language was used; normalise to the
    # BCP-47 codes the rest of the agent speaks, else fall back to script sniffing.
    dg_lang = channel.get("detected_language") or ""
    if dg_lang:
        detected = dg_lang if "-" in dg_lang else f"{dg_lang}-IN"
    else:
        detected = detect_text_language(transcript)

    return transcript, detected

async def openai_transcribe(api_key: str, audio_bytes: bytes) -> tuple[str, str]:
    """OpenAI Whisper transcription via OpenAI API — returns (transcript, detected_language)."""
    import httpx
    import io
    try:
        filename, mime = _detect_audio_upload_format(audio_bytes)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (filename, io.BytesIO(audio_bytes), mime)},
                data={"model": "whisper-1", "response_format": "verbose_json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                transcript = data.get("text", "")
                lang = data.get("language", "")  # returns ISO 639-1, e.g. "hi"
                # Map ISO 639-1 → BCP-47 region codes for Indian langs
                lang_map = {
                    "hi": "hi-IN", "en": "en-IN", "ta": "ta-IN", "te": "te-IN",
                    "kn": "kn-IN", "ml": "ml-IN", "mr": "mr-IN", "bn": "bn-IN",
                    "gu": "gu-IN", "pa": "pa-IN", "or": "or-IN",
                }
                detected = lang_map.get(lang, lang + "-IN" if lang else "")
                return transcript, detected
            else:
                logger.error("OpenAI Whisper STT error %s: %s", resp.status_code, resp.text[:200])
                return "", ""
    except Exception as exc:
        logger.error("OpenAI Whisper STT exception: %s", exc)
        return "", ""


# Retained refs for the fire-and-forget Sheets writes below — a bare
# create_task can be collected before it runs (audit R3, same as
# web_calls.py::_dispatch_watchers).
_sheets_tasks: set = set()


async def sync_and_log_appointment(action: str, name: str, phone: str, date: str, time: str, doctor: str, notes: str, tenant_id: str, webhook_url: str | None, websocket: WebSocket = None, call_id: str | None = None) -> dict:
    """AWAIT a Book/Reschedule/Cancel, then log only REAL successes to Sheets.

    Returns the execute_booking_action() result dict::

        {success, reason, appointment_id, doctor_name, available_doctors, slot}

    The caller MUST gate any user-facing confirmation on ``result["success"]``.
    This is the chat-path half of audit FIX 4 — this function used to be fired
    with asyncio.create_task() and never awaited, so the chat confirmed the
    appointment regardless of whether the write actually happened.
    """
    from backend.services.his import execute_booking_action
    from backend.services.sheets import log_booking_to_sheets

    if websocket:
        try:
            await websocket.send_json({
                "type": "tool_call",
                "tool": "Database & Google Sheets Sync",
                "status": "started",
                "payload": {"action": action, "name": name, "phone": phone,
                            "date": date, "time": time, "doctor": doctor, "notes": notes},
            })
        except Exception:
            pass

    _t0 = time_mod.perf_counter()
    try:
        res = await execute_booking_action(
            action=action, tenant_id=tenant_id, name=name, phone=phone,
            date_str=date, time_str=time, doctor_name=doctor, notes=notes,
            call_id=call_id,
        )
    except Exception as e:
        logger.error(f"Error in sync_and_log_appointment DB step: {e}", exc_info=True)
        res = {"success": False, "reason": "db_error", "appointment_id": None,
               "doctor_name": None, "available_doctors": [], "slot": ""}
    # The patient is sitting in a "please hold" state for exactly this window,
    # so it is worth being able to see it in the logs rather than inferring it
    # from total request time (which also includes two LLM calls).
    logger.info(
        "Booking commit %s finished in %.2fs (success=%s reason=%s)",
        action, time_mod.perf_counter() - _t0, res.get("success"), res.get("reason") or "-",
    )

    appointment_id = res.get("appointment_id") or "N/A"
    status = ("cancelled" if (action or "").upper() == "CANCEL" else "confirmed") if res["success"] else "failed"

    # Log to Sheets ONLY when the appointment really changed. Logging a failed or
    # fabricated booking would recreate the exact inconsistency this fix removes.
    # "already_at_that_time" succeeds without changing anything, so it must not
    # add a row either.
    sheets_success = False
    if res["success"] and res.get("reason") != "already_at_that_time":
        # Fire-and-forget. This is an external HTTP call with a 10s timeout and
        # it used to be AWAITED inside the patient's request — up to 10 extra
        # seconds of "please hold on" before they were told their booking had
        # succeeded, for a mirror-write that is not the source of truth (the
        # appointment row already exists at this point). The reference is
        # retained because a bare create_task can be garbage-collected
        # mid-flight (same audit R3 hazard documented in web_calls.py).
        try:
            _task = asyncio.create_task(log_booking_to_sheets(
                action=action, name=name, phone=phone, date=date, time=time,
                doctor=res.get("doctor_name") or doctor, appointment_id=appointment_id,
                status=status, notes=notes, webhook_url=webhook_url,
            ))
            _sheets_tasks.add(_task)
            _task.add_done_callback(_sheets_tasks.discard)
            sheets_success = True  # queued, not yet confirmed — see payload below
        except Exception as e:
            logger.error(f"Sheets log could not be queued: {e}", exc_info=True)

    if websocket:
        try:
            await websocket.send_json({
                "type": "tool_call",
                "tool": "Database & Google Sheets Sync",
                "status": "success" if res["success"] else "failed",
                "payload": {"action": action, "name": name, "phone": phone,
                            "date": date, "time": time, "doctor": doctor, "notes": notes},
                "result": {"appointment_id": appointment_id, "status": status,
                           "reason": res.get("reason", ""),
                           "db_sync": "success" if res["success"] else "failed",
                           # "queued": the Sheets mirror-write is no longer
                           # awaited (it blocked the patient's confirmation),
                           # so its real outcome isn't known at this point.
                           "sheets_sync": "queued" if sheets_success else "skipped"},
            })
        except Exception:
            pass

    return res


# ── LLM Logic ─────────────────────────────────────────────────────────────────

# Store conversation history per session (in-memory for now)
_conversation_history: dict[str, list] = {}

# Matches the [ACTION: BOOK|Name|Phone|Date|Time|Doctor|Notes] tag the LLM emits.
#
# Whitespace is tolerated everywhere the model actually puts it. Production
# 2026-08-11 (Indiana Hospital): the model emitted
#   "[ ACTION: RESCHEDULE|Ainan|9090909090|11/08/2026|03:00 PM|Rajesh|N/A ]"
# — a space after the opening bracket. The old pattern required "[ACTION"
# adjacently, so it did not match, NOTHING was executed, and (because the
# loose scrubber below had the same gap) the raw tag was shown to the patient
# verbatim. Both are now whitespace- and case-tolerant; the fields themselves
# are .strip()ed by the caller.
_ACTION_RE = re.compile(
    r'\[\s*ACTION\s*:\s*(BOOK|RESCHEDULE|CANCEL)\s*\|(.*?)\|(.*?)\|(.*?)\|(.*?)\|(.*?)(?:\|(.*?))?\]',
    re.IGNORECASE,
)

# ANY [ACTION: ...]-shaped fragment, including malformed ones _ACTION_RE cannot
# parse. Observed in production 2026-08-10: the model emitted a bare
# "[ACTION: None]", which the strict regex ignored and which therefore leaked
# into the patient's chat verbatim. Every user-facing reply is scrubbed with
# this, not just the ones where a valid tag was found.
_LOOSE_ACTION_RE = re.compile(r'\[\s*ACTION\b[^\]]*\]', re.IGNORECASE)

# A machine tag whose closing bracket never arrived — the model ran into the
# max_tokens cap mid-tag, or stopped early. There is no valid patient-facing
# text that starts "[ACTION"/"[BOOKING_RESULT" and never closes, so the whole
# tail is dropped rather than spoken.
_TRUNCATED_TAG_RE = re.compile(
    r'\[\s*(?:ACTION|BOOKING_RESULT|AVAILABILITY_NOTE)\b[^\]]*$',
    re.IGNORECASE,
)

# Phrases that promise a LATER message ("hold on", "I'll confirm shortly").
#
# This path is strictly request/response: one patient message in, one reply
# out. There is no queue, no callback, no polling — nothing that can ever
# deliver a promised follow-up. So a reply ending on one of these strands the
# patient in a permanent "waiting to be confirmed" state.
#
# That is the exact 2026-08-10 production bug: the agent said "please hold on
# for a moment while I complete your booking" / "I've sent the request, please
# wait for a moment to confirm" and then went silent forever — in one case
# even though the appointment row HAD been written successfully.
_FOLLOWUP_PROMISE_PATTERNS = (
    "hold on", "please hold", "one moment", "a moment", "a minute",
    "bear with me", "please wait", "kindly wait", "shortly", "momentarily",
    "i'll confirm", "i will confirm", "let me confirm", "let me check",
    "i'm checking", "i am checking", "checking availability", "checking the availability",
    "sent the request", "processing your", "working on it", "get back to you",
    # "The action is underway" — the same stranding without the word "wait".
    # Measured live 2026-08-11 on a reschedule: asked "is it done?", the model
    # replied "Yes, proceeding with the change." and emitted no tag, so nothing
    # happened and the patient was told it was in progress forever. Note these
    # must NOT match "please reply yes to proceed", which is a legitimate
    # question — hence "proceeding", not "proceed".
    "proceeding", "going ahead", "i'll go ahead", "i will go ahead",
    "doing that now", "i'll do that now", "i will do that now",
    "making that change", "making the change", "updating your appointment",
    "changing your appointment", "in progress", "being processed",
    "इंतज़ार", "इंतजार", "प्रतीक्षा", "थोड़ा रुक", "एक मिनट",
)


def _promises_followup(text: str) -> bool:
    """True if the reply tells the patient to wait for something that will
    never arrive. False positives are safe here: the caller's response is to
    substitute a deterministic, outcome-accurate reply, which is correct
    either way."""
    low = (text or "").lower()
    return any(p in low for p in _FOLLOWUP_PROMISE_PATTERNS)


# Values a model writes when it does not actually have the detail. The tag
# instructions themselves say to use 'N/A' for fields that don't apply, so the
# model readily fills these in for BOOK fields it hasn't collected yet.
_PLACEHOLDER_VALUES = frozenset({
    "", "n/a", "na", "n.a.", "none", "null", "-", "--", "unknown", "not provided",
    "not given", "patient", "customer", "tbd", "xxx",
})


def _is_placeholder(value: str) -> bool:
    return (value or "").strip().lower() in _PLACEHOLDER_VALUES


# Words that make a reply an actual ASSERTION that the action completed. A
# reply may mention "confirm" and still not confirm anything ("To confirm, you
# are Ramesh Kumar… is that right?"), so a trailing question mark disqualifies
# it regardless — see _asserts_completion.
_COMPLETION_MARKERS = {
    "BOOK": ("confirmed", "booked", "scheduled", "reserved", "is set", "all set",
             "कन्फर्म", "बुक", "निश्चित"),
    "RESCHEDULE": ("rescheduled", "moved", "changed", "updated", "new time",
                   "रीशेड्यूल", "बदल"),
    "CANCEL": ("cancelled", "canceled", "called off", "रद्द"),
}


def _asserts_completion(text: str, action: str) -> bool:
    """True if this reply actually TELLS the patient the action is done.

    Guards the success path: the write really happened, so a reply that merely
    asks another question ("…is that correct?") leaves the patient believing
    nothing has been booked — the same stranding as a "please wait", just
    phrased differently. Observed live 2026-08-10.
    """
    t = (text or "").strip()
    if not t or t.endswith("?"):
        return False
    low = t.lower()
    return any(k in low for k in _COMPLETION_MARKERS.get((action or "").upper(), ()))


def _claims_any_completion(text: str) -> bool:
    """True if the reply claims ANY appointment action is done.

    Used where no [ACTION:] tag was emitted — in which case nothing was
    written, so such a claim is a fabricated confirmation. Measured
    2026-08-10: llama-3.1-8b-instant produced exactly this ("Your appointment
    with Dr. Rajesh is booked", no tag) in 2 of 3 runs, and the stronger
    llama-3.3-70b does it occasionally too.
    """
    return any(_asserts_completion(text, action) for action in _COMPLETION_MARKERS)


def _scrub_reply(text: str) -> str:
    """Strip machine tags (valid, malformed, or truncated) out of anything shown
    to a patient. Every user-facing string on this path passes through here."""
    text = _LOOSE_ACTION_RE.sub("", text or "")
    text = re.sub(r'\[\s*BOOKING_RESULT[^\]]*\]', "", text, flags=re.IGNORECASE)
    text = re.sub(r'\[\s*AVAILABILITY_NOTE[^\]]*\]', "", text, flags=re.IGNORECASE)
    text = _TRUNCATED_TAG_RE.sub("", text)
    return re.sub(r'[ \t]{2,}', ' ', text).strip()


def _is_only_a_tag(text: str) -> bool:
    """True if the model's whole reply was machine tag(s) and nothing else.

    Scrubbing such a reply yields "" — and an empty chat bubble tells the
    patient nothing at all, which is the same dead end as leaking the tag. The
    2026-08-11 production reply was exactly this: a bare
    "[ ACTION: RESCHEDULE|…]" with no prose around it.
    """
    return bool((text or "").strip()) and not _scrub_reply(text)


# Used when the model promised a follow-up, could not be repaired into either a
# real action tag or a real question, and would otherwise leave the patient
# waiting on silence. Asks for what is needed instead of promising anything.
_NEEDS_DETAILS_REPLY = {
    "en-IN": ("Sorry — could you confirm the doctor, and the date and time you'd like? "
              "I'll book it as soon as you tell me."),
    "hi-IN": ("क्षमा करें — कृपया बताइए किस डॉक्टर के साथ, और कौन सी तारीख़ और समय चाहिए? "
              "बताते ही मैं बुक कर दूँगा।"),
}


# The clinic's real roster + real open slots, and the day/date parsing it needs,
# both live in backend/services/availability_prompt.py — ONE implementation,
# shared with the voice pipeline. They used to live here, private to the
# chat/embed router, which is precisely how voice came to answer "which doctors
# do you have?" with "no doctors, no information given to me" for a clinic whose
# chat channel named a real cardiologist off the same tables.
from backend.services.availability_prompt import (  # noqa: E402
    _cache as _avail_cache,
    real_availability_block as _real_availability_block,
)


async def _dispatch_llm(provider: str, api_key: str, system_prompt: str,
                        history: list, model: str, max_tokens: int) -> str:
    """Single entry point to the provider adapters, used for BOTH the first pass
    and the honest regeneration pass so they stay identical."""
    if provider == "gemini":
        return await call_gemini(api_key, system_prompt, history, model, max_tokens=max_tokens)
    if provider == "openai":
        return await call_openai(api_key, system_prompt, history, model, max_tokens=max_tokens)
    if provider == "anthropic":
        return await call_anthropic(api_key, system_prompt, history, model, max_tokens=max_tokens)
    if provider == "groq":
        return await call_groq(api_key, system_prompt, history, model, max_tokens=max_tokens)
    if provider == "deepseek":
        return await call_openai(api_key, system_prompt, history, model,
                                 max_tokens=max_tokens, base_url="https://api.deepseek.com/v1")
    raise Exception(f"Unknown LLM provider: {provider}")


async def _resolve_tenant_webhook(agent: AgentConfig, db: AsyncSession) -> str | None:
    webhook = getattr(agent, "webhook_url", None)
    if webhook:
        return webhook
    try:
        from backend.models.tenant import Tenant
        t = (await db.execute(select(Tenant).where(Tenant.id == agent.tenant_id))).scalar_one_or_none()
        if t and t.google_sheets_webhook_url:
            return t.google_sheets_webhook_url
    except Exception as e:
        logger.error("Failed to fetch tenant webhook url: %s", e)
    return None


def _booking_result_message(action: str, res: dict) -> str:
    """Build the authoritative [BOOKING_RESULT ...] system line injected before
    the honest regeneration pass — the chat analogue of what BookingProcessor
    injects into the voice pipeline's LLM context."""
    action = (action or "").upper()
    verb = {"BOOK": "booking", "RESCHEDULE": "reschedule",
            "CANCEL": "cancellation"}.get(action, "request")
    # Checked before the generic success branch: the appointment IS at the
    # requested time (so the patient's goal holds and success is true), but
    # saying "rescheduled" would imply a change that did not happen.
    if res.get("reason") == "already_at_that_time":
        return (f"{BOOKING_RESULT_TRUE} The patient's appointment was ALREADY at exactly that time "
                f"(appointment id {res.get('appointment_id')}), so nothing needed to change and nothing "
                "was lost. Tell them in ONE short sentence that their appointment is already at that time "
                "and is still confirmed.")
    if res.get("success"):
        detail = f" (appointment id {res.get('appointment_id')}"
        if res.get("doctor_name"):
            detail += f", doctor {res['doctor_name']}"
        if res.get("slot"):
            detail += f", {res['slot']}"
        detail += ")"
        return (f"{BOOKING_RESULT_TRUE} The {verb} is saved in the system{detail}. "
                "Confirm this to the patient in ONE short sentence, in their language.")

    reason = res.get("reason", "")
    if reason == "disabled":
        return (f"{BOOKING_RESULT_FALSE} This clinic has the {verb} feature turned off, so nothing "
                "was changed. Do NOT say it was done. Politely tell the patient you can't do that here "
                "and offer to connect them to the clinic's staff.")
    if reason in ("doctor_not_found", "doctor_required"):
        docs = ", ".join(res.get("available_doctors") or [])
        avail = (f" The doctors available at this clinic are: {docs}."
                 if docs else " There are no doctors listed at this clinic yet.")
        req = ("The doctor the patient asked for is not available at this clinic"
               if reason == "doctor_not_found" else "No specific available doctor was chosen")
        return (f"{BOOKING_RESULT_FALSE} {req}, so NOTHING was booked. Do NOT say it is booked.{avail} "
                "Ask the patient which of the available doctors they'd like, or offer to connect them to staff.")
    if reason == "not_found":
        return (f"{BOOKING_RESULT_FALSE} No matching appointment was found for that name and phone number, "
                "so nothing was changed. Do NOT say it was done. Ask the patient to re-check their name and "
                "phone number.")
    if reason == "invalid_time":
        return (f"{BOOKING_RESULT_FALSE} No valid appointment TIME was given, so nothing was booked or "
                "changed — never assume or invent a time. Do NOT say it was done. Ask the patient to state "
                "the time again, e.g. '3 PM' or '11:30 AM'.")
    if reason == "missing_details":
        need = " and ".join(res.get("missing") or ["the patient's details"])
        if action in ("RESCHEDULE", "CANCEL"):
            return (f"{BOOKING_RESULT_FALSE} NOTHING was changed and the existing appointment is untouched: "
                    f"an appointment can only be found by the patient's name AND phone number, and you have "
                    f"not collected their {need} yet. Do NOT say it was done, and do NOT tell them no "
                    f"appointment exists. Ask the patient for their {need} in one short question.")
        return (f"{BOOKING_RESULT_FALSE} NOTHING was booked: you have not collected the patient's {need} "
                f"yet, and an appointment without it could never be found again to cancel or reschedule. "
                f"Do NOT say it was done. Ask the patient for their {need} in one short question.")
    # ── Pre-write availability gate (his._availability_gate) ──────────────────
    # BOOK and RESCHEDULE are both checked against the doctor's real schedule
    # BEFORE anything is written, so the patient can be told the specific truth
    # rather than a generic "system error" — or, worse, a confirmation.
    unchanged = ("nothing was booked and the patient still has no appointment"
                 if action == "BOOK" else
                 "NOTHING changed and the existing appointment still stands at its original time")
    if reason in ("slot_taken", "outside_hours", "slot_in_past", "slot_unavailable"):
        alts = ", ".join(res.get("alternatives") or [])
        who = res.get("doctor_name") or "That doctor"
        offer = (f" {who} IS free at: {alts}. Offer those exact times and nothing else."
                 if alts else " Ask the patient for a different day or time.")
        if reason == "slot_taken":
            what = "That exact time is ALREADY BOOKED for that doctor"
        elif reason == "outside_hours":
            what = f"{who} does not consult at that time — it is outside their hours that day"
        elif reason == "slot_in_past":
            what = "That time has already passed"
        else:
            what = "That time is not open on that doctor's real schedule"
        return (f"{BOOKING_RESULT_FALSE} {what}, so {unchanged}. Do NOT say it was done, and do NOT offer "
                f"to retry the SAME time — it will fail again.{offer}")
    if reason == "no_schedule":
        who = res.get("doctor_name") or "that doctor"
        return (f"{BOOKING_RESULT_FALSE} {who} has no consulting hours set for that day, so NOTHING was "
                f"{verb}d and the appointment is UNCHANGED. Do NOT say it was done. Tell the patient that "
                "doctor does not have hours that day, ask them for a different day, or offer to connect "
                "them to the clinic's staff.")
    if reason == "doctor_unavailable":
        who = res.get("doctor_name") or "That doctor"
        return (f"{BOOKING_RESULT_FALSE} {who} is currently ON LEAVE, so NOTHING was {verb}d and the "
                f"appointment is UNCHANGED. Do NOT say it was done. Tell the patient that doctor is not "
                "available and offer another doctor at this clinic.")
    return (f"{BOOKING_RESULT_FALSE} The {verb} could NOT be saved due to a system error. Do NOT say it was "
            "done. Apologize briefly and offer to try again.")


# Honest, non-confirming fallbacks used ONLY if the regeneration LLM call itself
# errors out. Success strings are safe to localise (the row really exists);
# failures fall back to a plain message that clearly does not confirm anything.
_SUCCESS_FALLBACKS = {
    "BOOK": {"en-IN": "Your appointment is confirmed.",
             "hi-IN": "आपका अपॉइंटमेंट कन्फर्म हो गया है।",
             "ml-IN": "നിങ്ങളുടെ അപ്പോയിന്റ്മെന്റ് സ്ഥിരീകരിച്ചു."},
    "RESCHEDULE": {"en-IN": "Your appointment has been rescheduled.",
                   "hi-IN": "आपका अपॉइंटमेंट रीशेड्यूल कर दिया गया है।"},
    "CANCEL": {"en-IN": "Your appointment has been cancelled.",
               "hi-IN": "आपका अपॉइंटमेंट रद्द कर दिया गया है।"},
}


def _deterministic_booking_reply(action: str, res: dict, user_language: str) -> str:
    lang = (user_language or "en-IN").strip() or "en-IN"
    if res.get("reason") == "already_at_that_time":
        return "Your appointment is already at that time, and it's still confirmed."
    if res.get("success"):
        table = _SUCCESS_FALLBACKS.get((action or "").upper(), _SUCCESS_FALLBACKS["BOOK"])
        return table.get(lang, table["en-IN"])
    if res.get("reason") in ("doctor_not_found", "doctor_required"):
        docs = ", ".join(res.get("available_doctors") or [])
        if docs:
            return f"I'm sorry, that doctor isn't available at this clinic. We have: {docs}. Who would you like to see?"
        return "I'm sorry, I couldn't find that doctor at this clinic, so I haven't booked anything. Could you clarify?"
    if res.get("reason") == "not_found":
        return ("I couldn't find a matching appointment for that name and phone number, so nothing was "
                "changed. Could you re-check those details?")
    if res.get("reason") == "invalid_time":
        return "I'm sorry, I didn't catch a clear time for that. Could you say it again, like '3 PM' or '11:30 AM'?"
    if res.get("reason") == "missing_details":
        need = " and ".join(res.get("missing") or ["your details"])
        return f"Before I book that, could you give me your {need}?"
    if res.get("reason") in ("slot_taken", "outside_hours", "slot_in_past", "slot_unavailable"):
        who = res.get("doctor_name") or "that doctor"
        alts = ", ".join(res.get("alternatives") or [])
        unchanged = "" if (action or "").upper() == "BOOK" else " Your existing appointment is unchanged."
        what = {
            "slot_taken": f"that time is already booked with {who}",
            "outside_hours": f"{who} doesn't consult at that time",
            "slot_in_past": "that time has already passed",
        }.get(res.get("reason"), f"{who} isn't free at that time")
        if alts:
            return (f"Sorry, {what}.{unchanged} They are open at: {alts}. Which of those works for you?")
        return (f"Sorry, {what}, and there's nothing else open that day."
                f"{unchanged} Could you pick a different day?")
    if res.get("reason") == "no_schedule":
        who = res.get("doctor_name") or "that doctor"
        unchanged = "" if (action or "").upper() == "BOOK" else " Your existing appointment is unchanged."
        return (f"Sorry, {who} doesn't have consulting hours on that day, so I haven't changed anything."
                f"{unchanged} Could you pick a different day?")
    if res.get("reason") == "doctor_unavailable":
        who = res.get("doctor_name") or "That doctor"
        return f"Sorry, {who} is on leave at the moment, so I haven't made any changes. Would another doctor work?"
    if res.get("reason") == "disabled":
        return "I'm sorry, I'm not able to do that here. Let me connect you with the clinic's staff."
    return "I'm sorry, I couldn't complete that just now, so nothing was changed. Would you like me to try again?"


async def _handle_booking_action(
    response: str, *, agent: AgentConfig, db: AsyncSession, history: list,
    provider: str, api_key: str, model: str, max_tokens: int,
    base_system_prompt: str, user_language: str, websocket: WebSocket = None,
) -> str:
    """If the model emitted an [ACTION:] tag, AWAIT the real DB write and return
    an honest reply generated with the true outcome injected. No tag → return
    the response unchanged.

    This is the chat/embed analogue of BookingProcessor: the premature phase-1
    text is discarded, so the patient only ever sees a reply produced AFTER the
    booking outcome is known.

    Whatever happens, this returns a reply that RESOLVES the turn — success,
    failure, or a question. It never returns a promise of a later message,
    because this path is request/response and no later message can exist.
    """
    m = _ACTION_RE.search(response or "")

    if not m:
        # No parseable tag means NOTHING was written. Two replies are unsafe to
        # pass through in that state:
        #   * a promise to go do it ("hold on…") — the patient waits on silence
        #     forever, since this path cannot send a follow-up;
        #   * a claim that it is already done — a fabricated confirmation for a
        #     booking that does not exist.
        # Either way, give the model one strict chance to emit the tag or ask
        # for what is missing.
        #   * a reply that was ENTIRELY a machine tag this regex could not
        #     parse — scrubbing leaves nothing to say, and the patient gets an
        #     empty bubble (2026-08-11 production: a bare, space-padded
        #     "[ ACTION: RESCHEDULE|…]" tag that no longer parses as a tag).
        fabricated = _claims_any_completion(response)
        tag_only = _is_only_a_tag(response)
        if not (_promises_followup(response) or fabricated or tag_only):
            return _scrub_reply(response)

        logger.error(
            "Booking reply %s with NO parseable action tag — nothing was written. Repairing: %r",
            "WAS AN UNPARSEABLE TAG AND NOTHING ELSE" if tag_only
            else ("CLAIMED THE ACTION WAS DONE" if fabricated else "promised a follow-up"),
            (response or "")[:160],
        )
        repaired = await _repair_missing_action_tag(
            provider=provider, api_key=api_key, model=model, max_tokens=max_tokens,
            base_system_prompt=base_system_prompt, history=history,
        )
        m = _ACTION_RE.search(repaired or "")
        if not m:
            cleaned = _scrub_reply(repaired or "")
            # Accept the repair only if it stopped BOTH promising and claiming.
            if cleaned and not _promises_followup(cleaned) and not _claims_any_completion(cleaned):
                return cleaned
            lang = (user_language or "en-IN").strip() or "en-IN"
            return _NEEDS_DETAILS_REPLY.get(lang, _NEEDS_DETAILS_REPLY["en-IN"])

    g = m.groups()
    b_action = (g[0] or "").strip().upper()
    b_name = (g[1] or "").strip()
    b_phone = (g[2] or "").strip()
    b_date = (g[3] or "").strip()
    b_time = (g[4] or "").strip()
    b_doctor = (g[5] or "").strip()
    b_notes = (g[6].strip() if (len(g) > 6 and g[6] is not None) else "N/A")

    # Capability gate — the chat path used to book even when the clinic admin had
    # the tool switched off. BOOK/RESCHEDULE need can_book_appointments; CANCEL
    # needs can_cancel_appointments.
    can_book = bool(getattr(agent, "can_book_appointments", True))
    can_cancel = bool(getattr(agent, "can_cancel_appointments", True))
    allowed = can_book if b_action in ("BOOK", "RESCHEDULE") else (
        can_cancel if b_action == "CANCEL" else False)

    # Time gate — BOOK/RESCHEDULE need a REAL caller-given time. The model's
    # [ACTION:] tag can carry an empty/"N/A" Time field (observed live
    # 2026-08-10: the tag had a correct Date but a blank Time), and
    # parse_slot_datetime's fallback-on-unparseable behavior would silently
    # book midnight instead of refusing — exactly the kind of fabricated slot
    # audit FIX 4 already bans for the voice path. CANCEL needs no time at
    # all (matched by name+phone), so it is not gated here.
    from backend.services.his import is_time_str_parseable
    needs_real_time = b_action in ("BOOK", "RESCHEDULE")

    # Identity gate — a BOOK with a placeholder Name/Phone creates a row nobody
    # can be identified from, and which the patient can NEVER cancel or
    # reschedule, because that lookup matches on name AND phone
    # (his.py::sync_appointment_to_db). Observed live 2026-08-10: the model
    # emitted a BOOK tag with "N/A" for both before it had asked for them, and
    # a real appointment was written for "N/A".
    #
    # CANCEL/RESCHEDULE need the same two fields for the opposite reason: the
    # existing appointment is FOUND by name + phone, so a placeholder there can
    # only ever produce "no appointment found" — which reads to the patient as
    # "your appointment doesn't exist" when the truth is "you were never asked
    # who you are". Ask instead.
    missing = [label for label, val in (("name", b_name), ("phone number", b_phone))
               if _is_placeholder(val)] if b_action in ("BOOK", "RESCHEDULE", "CANCEL") else []

    if needs_real_time and not is_time_str_parseable(b_time):
        res = {"success": False, "reason": "invalid_time", "appointment_id": None,
               "doctor_name": None, "available_doctors": [], "slot": ""}
    elif missing:
        res = {"success": False, "reason": "missing_details", "appointment_id": None,
               "doctor_name": None, "available_doctors": [], "slot": "",
               "missing": missing}
    elif not allowed:
        res = {"success": False, "reason": "disabled", "appointment_id": None,
               "doctor_name": None, "available_doctors": [], "slot": ""}
    else:
        webhook = await _resolve_tenant_webhook(agent, db)
        res = await sync_and_log_appointment(
            action=b_action, name=b_name, phone=b_phone, date=b_date, time=b_time,
            doctor=b_doctor, notes=b_notes, tenant_id=str(agent.tenant_id),
            webhook_url=webhook, websocket=websocket,
        )

    result_line = _booking_result_message(b_action, res)

    # Regenerate the user-facing reply with the REAL outcome injected, under the
    # shared honesty rules. history still ends at the user's message — the
    # phase-1 assistant text was intentionally NOT appended.
    regen_system = (
        base_system_prompt
        + "\n\n--- SYSTEM UPDATE (AUTHORITATIVE — obey exactly) ---\n"
        + result_line + "\n"
        + BOOKING_RULES_BLOCK
    )
    try:
        final = await _dispatch_llm(provider, api_key, regen_system, history, model, max_tokens)
        final = _scrub_reply(final)
        # The reply must RESOLVE the outcome, not defer it. A model that
        # ignores the injected [BOOKING_RESULT ...] and hedges ("I've sent the
        # request, please wait to confirm") strands the patient exactly as if
        # nothing had happened — and on 2026-08-10 it did that on a booking
        # whose row had ALREADY been written successfully. The deterministic
        # reply below is always outcome-accurate, so substituting it is strictly
        # safer than trusting the model here.
        # On SUCCESS the reply must assert completion; on failure it must at
        # least not promise a follow-up. Anything else gets replaced with the
        # deterministic, outcome-accurate reply.
        if final:
            # A no-op reschedule ("already at that time") is a success that must
            # NOT assert a change, so it is held to the weaker bar: just don't
            # strand the patient on a promise.
            assert_completion = res.get("success") and res.get("reason") != "already_at_that_time"
            ok = (_asserts_completion(final, b_action) if assert_completion
                  else not _promises_followup(final))
            if ok:
                return final
            logger.warning(
                "Regenerated booking reply did not resolve the outcome (success=%s) — "
                "substituting the deterministic reply: %r",
                res.get("success"), final[:160],
            )
    except Exception as e:
        logger.error("Honest booking regeneration failed (%s) — using deterministic reply: %s", provider, e)

    return _deterministic_booking_reply(b_action, res, user_language)


async def _repair_missing_action_tag(
    *, provider: str, api_key: str, model: str, max_tokens: int,
    base_system_prompt: str, history: list,
) -> str:
    """One strict re-prompt after the model promised to complete a booking but
    emitted no [ACTION:] tag. Returns the model's second attempt (or "" on
    error) — the caller decides whether it produced a usable tag.

    This is a repair, not a retry loop: it runs at most once per turn, and only
    on the path that would otherwise end in permanent silence.
    """
    repair_system = (
        base_system_prompt
        + "\n\n--- SYSTEM UPDATE (AUTHORITATIVE — obey exactly) ---\n"
        "Your previous reply either told the patient to wait while you completed their request, or told "
        "them it was already done — but you did NOT emit an [ACTION: ...] tag, so NOTHING happened. No "
        "appointment was created, changed or cancelled. This system can only reply to the patient's own "
        "messages: there is no way to send them a follow-up later, and you must never state that an "
        "appointment is booked/cancelled/rescheduled unless the system has confirmed it back to you.\n"
        "Reply once more, choosing EXACTLY ONE of:\n"
        "  (a) If you already have Name, Phone, Date (DD/MM/YYYY), Time and Doctor — output the correct "
        "[ACTION: ...] tag NOW, at the end of one short neutral sentence.\n"
        "  (b) If any of those details is missing — ask for the missing detail in ONE short question.\n"
        "NEVER say 'hold on', 'please wait', 'one moment', 'I'll confirm shortly' or anything else implying "
        "a later message.\n"
        + BOOKING_RULES_BLOCK
    )
    try:
        return await _dispatch_llm(provider, api_key, repair_system, history, model, max_tokens) or ""
    except Exception as e:
        logger.error("ACTION-tag repair pass failed (%s): %s", provider, e)
        return ""


async def generate_llm_response(
    agent: AgentConfig, 
    user_message: str,
    db: AsyncSession,
    session_id: str = None,
    user_language: str = "",
    websocket: WebSocket = None,
) -> str:
    """Generate LLM response using configured provider, system prompt, and knowledge base.
    
    `user_language` is the BCP-47 code detected from the user's latest utterance
    (e.g. 'en-IN', 'hi-IN'). When provided, the LLM is instructed to mirror it.
    """
    
    llm_provider = agent.llm_provider or "gemini"
    session_key = session_id or agent.id

    # Initialize conversation history for this session.
    # `_conversation_history` is an L1 in-process cache. The WS voice/text path
    # (`websocket` is not None) is pinned to one worker for the connection's
    # whole life, so the cache alone is correct there — skip Redis to leave
    # that hot low-latency loop untouched. REST /agent-chat and public
    # /embed chat (`websocket is None`) reuse the same session_key across
    # independent requests that can land on any worker, so read-through from
    # the shared store the first time *this* worker sees the session_key.
    if session_key not in _conversation_history:
        seeded = None
        if websocket is None:
            seeded = await redis_client.get_chat_history(str(session_key))
        _conversation_history[session_key] = seeded or []

    history = _conversation_history[session_key]
    
    # Get API key — check DB first, then fall back to .env
    result = await db.execute(
        select(ApiKeyConfig).where(
            ApiKeyConfig.provider == llm_provider,
            ApiKeyConfig.is_active == True
        ).limit(1)
    )
    key_config = result.scalars().first()
    
    env_key = None
    if llm_provider == "gemini":
        env_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY")
    elif llm_provider == "openai":
        env_key = settings.openai_api_key or os.getenv("OPENAI_API_KEY")
    elif llm_provider == "anthropic":
        env_key = settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
    elif llm_provider == "groq":
        env_key = settings.groq_api_key or os.getenv("GROQ_API_KEY")
    elif llm_provider == "deepseek":
        env_key = settings.deepseek_api_key or os.getenv("DEEPSEEK_API_KEY")
    
    api_key = None
    if key_config and key_config.api_key_enc:
        api_key = key_config.get_key_raw()
    if not api_key and env_key:
        api_key = env_key.strip()
    
    if not api_key:
        return generate_demo_response(agent, user_message, history)
    
    # ── Build System Prompt with Knowledge Base ──────────────────────────────
    # IST, not the server's local clock. This is the reference date the model
    # converts "tomorrow" against, while his.parse_slot_datetime resolves the
    # resulting slot in IST — so a UTC-hosted backend (Railway) told the model
    # it was still yesterday between 18:30 and 24:00 UTC, and every relative
    # date the patient gave in that window was booked one day early. Seen in
    # the 2026-08-11 transcript: "tomorrow" became 11/08 when IST was already
    # 11/08.
    from backend.services.timeutil import ist_now

    today = ist_now()
    current_date_str = today.strftime("%d/%m/%Y")
    current_day_str = today.strftime("%A")

    base_prompt = agent.system_prompt or (
        f"You are a helpful AI receptionist for {agent.agent_name}. "
        f"Help patients book appointments, answer questions about "
        f"clinic services, and provide general assistance. "
        f"Keep responses concise and conversational — under 50 words. "
        f"You are speaking on a phone call. Today's date is {current_day_str}, {current_date_str}."
    )
    
    # Fetch knowledge base entries for this agent's tenant
    kb_context = ""
    try:
        from backend.models.knowledge_base import KnowledgeBase
        kb_result = await db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.tenant_id == agent.tenant_id,
                KnowledgeBase.is_active == True
            )
        )
        kb_entries = kb_result.scalars().all()
        
        if kb_entries:
            kb_lines = []
            for entry in kb_entries:
                kb_lines.append(f"[{entry.category.upper()}] {entry.title}: {entry.content}")
            kb_context = "\n\n--- CLINIC KNOWLEDGE BASE ---\n" + "\n".join(kb_lines) + "\n--- END KNOWLEDGE BASE ---\n"
    except Exception as e:
        logger.warning(f"Could not load knowledge base: {e}")
    
    # ── LANGUAGE MIRRORING + GROUNDING GUARDRAIL ─────────────────────────────
    # Map BCP-47 codes to human-readable language names for the LLM
    _LANG_NAMES = {
        "en-IN": "English", "hi-IN": "Hindi", "ta-IN": "Tamil",
        "te-IN": "Telugu", "kn-IN": "Kannada", "ml-IN": "Malayalam",
        "bn-IN": "Bengali", "gu-IN": "Gujarati", "mr-IN": "Marathi",
        "pa-IN": "Punjabi", "ur-IN": "Urdu", "od-IN": "Odia",
        "as-IN": "Assamese", "ne-IN": "Nepali", "ar-SA": "Arabic",
    }
    detected_lang_name = _LANG_NAMES.get(user_language, "")
    if not detected_lang_name and user_language:
        detected_lang_name = user_language.split("-")[0].capitalize()
    
    guardrail = "\n\n--- MANDATORY INSTRUCTIONS (OVERRIDE ALL ABOVE IF CONFLICTING) ---\n"
    guardrail += "1. LANGUAGE RULE: "
    if detected_lang_name:
        # BUG 2: Use LANGUAGE_INSTRUCTIONS for stronger enforcement
        lang_instr = LANGUAGE_INSTRUCTIONS.get(
            user_language,
            f"ALWAYS respond in {detected_lang_name}. Never switch languages unless user explicitly requests.",
        )
        guardrail += (
            f"The user is speaking in {detected_lang_name}. "
            f"{lang_instr} "
            f"Do NOT switch to any other language unless the user explicitly switches first. "
            f"NEVER mix languages or reply in a different language than the user spoke in.\n"
        )
    else:
        guardrail += (
            "Mirror the user's language exactly. If they speak English, reply in English. "
            "If they speak Hindi, reply in Hindi. Match their language precisely.\n"
        )
    guardrail += (
        "2. GROUNDING RULE: You MUST answer ONLY based on the system prompt above and the CLINIC KNOWLEDGE BASE. "
        "Do NOT invent information, make up doctor names, services, prices, or clinic details that are not explicitly mentioned above. "
        "If you don't know the answer, say so honestly and offer to transfer to a human staff member.\n"
        "3. STAY ON TOPIC: You are a clinic receptionist. Do NOT discuss topics unrelated to the clinic, "
        "appointments, doctors, or healthcare services. Politely redirect off-topic conversations.\n"
        "4. CONCISENESS: Keep every response under 2 sentences for voice conversations.\n"
        "5. GREETING RULE: You have ALREADY greeted the user with the opening message. "
        "Do NOT repeat the welcome greeting. If the user says Hello or Hi, respond naturally "
        "as a conversation continuation, NOT a new greeting.\n"
        "6. APPOINTMENT ACTIONS (BOOK / RESCHEDULE / CANCEL): Collect the required details, then SIGNAL the action with a tag. You must NEVER tell the patient the appointment is booked, rescheduled, or cancelled in the same turn — the system actually performs the action and replies to you with a [BOOKING_RESULT ...] message, and only THEN may you confirm (see the BOOKING RULES below).\n"
        "   - BOOK: collect Name, Phone number, preferred Date, preferred Time, and the Doctor's name.\n"
        f"     *Convert relative dates ('today', 'tomorrow', 'day after tomorrow', 'next Monday', weekdays) into an actual DD/MM/YYYY date using the reference date: Today is {current_day_str}, {current_date_str}. Never output relative date words in the tag — only DD/MM/YYYY.*\n"
        "   - RESCHEDULE or CANCEL: you MUST ask for and confirm BOTH the patient's Name and Phone number before signalling the action.\n"
        "   - NOTES: capture symptoms/reasons/special requests, or 'N/A' if none.\n"
        "   When (and only when) you have the required details, append exactly ONE of these tags at the very END of your reply:\n"
        "   - [ACTION: BOOK|Name|Phone|Date|Time|Doctor|Notes]\n"
        "   - [ACTION: RESCHEDULE|Name|Phone|Date|Time|Doctor|Notes]\n"
        "   - [ACTION: CANCEL|Name|Phone|Date|Time|Doctor|Notes]\n"
        "   (Use 'N/A' for fields that are not being changed.) The text before the tag must NOT claim the action is already done.\n"
        "   NEVER emit a tag when the patient is only ASKING something — 'what times are free?', 'is the "
        "doctor in tomorrow?', 'how much does it cost?'. A question about availability is answered "
        "straight from the REAL DOCTOR AVAILABILITY section above, with NO tag. Only emit a tag when the "
        "patient has actually asked you to book, move or cancel an appointment AND you have every field.\n"
        "   Date and Time in the tag must be the real values the patient gave (converted to DD/MM/YYYY and "
        "a clock time). Never write 'N/A' in the Time field of a BOOK or RESCHEDULE — if you do not have a "
        "time yet, ask for it instead of emitting a tag.\n"
        "   CRITICAL — the tag must be in the SAME message as that text. You cannot send the patient a "
        "message later: this system only ever replies to a message they send. If you say 'hold on', "
        "'please wait', 'one moment' or 'I'll confirm shortly' WITHOUT the tag, the patient is left waiting "
        "on a reply that will never come. So: either emit the tag now, or ask for the detail you are still "
        "missing — never promise to follow up.\n"
        "--- END MANDATORY INSTRUCTIONS ---\n"
    )

    # Real roster + real open slots, from the same availability engine that
    # gates the write. Built BEFORE the user message is appended to history so
    # the day/doctor detection sees this turn's words exactly once.
    availability_context = await _real_availability_block(
        str(agent.tenant_id),
        " ".join(
            [user_message or ""]
            + [str(m.get("content") or "") for m in (history or [])[-3:]]
        ),
    )

    system_prompt = (
        base_prompt + kb_context + availability_context + guardrail + BOOKING_RULES_BLOCK
    )

    # Add user message to history
    history.append({"role": "user", "content": user_message})
    
    # Provider-specific default models (safety net if wrong model is stored)
    PROVIDER_DEFAULTS = {
        "gemini": "gemini-2.5-flash",
        "openai": "gpt-4o-mini",
        "anthropic": "claude-haiku-4-5",
        "groq": "llama-3.3-70b-versatile",
        "deepseek": "deepseek-chat",
        "mistral": "mistral-small-latest",
    }
    PROVIDER_MODEL_PREFIXES = {
        "gemini": ["gemini"],
        "openai": ["gpt-", "o1-", "o3-"],
        "anthropic": ["claude"],
        # "openai/gpt-oss" is Groq-hosted despite the vendor prefix — without it
        # here, setting a clinic to that model would be silently "auto-corrected"
        # back to llama-3.3-70b by the check below.
        "groq": ["llama", "mixtral", "gemma", "whisper", "compound", "deepseek-r1",
                 "moonshard", "openai/gpt-oss", "qwen"],
        "deepseek": ["deepseek"],
        "mistral": ["mistral"],
    }
    
    agent_model = agent.llm_model or PROVIDER_DEFAULTS.get(llm_provider, "gemini-2.5-flash")
    # Check if stored model actually belongs to this provider
    valid_prefixes = PROVIDER_MODEL_PREFIXES.get(llm_provider, [])
    model_matches_provider = any(agent_model.lower().startswith(p) for p in valid_prefixes)
    if not model_matches_provider:
        old_model = agent_model
        agent_model = PROVIDER_DEFAULTS.get(llm_provider, agent_model)
        logger.info(
            "LLM model '%s' doesn't match provider '%s' — auto-corrected to '%s'",
            old_model, llm_provider, agent_model,
        )

    # Sanitize decommissioned Groq models → current equivalent at dispatch time.
    # This handles stale DB rows that still store the old name even after the user
    # updated the dropdown (the dropdown shows the new name but llm_model in DB
    # may not have been saved yet, or the agent was created before the deprecation).
    _DECOMMISSIONED_GROQ = {
        "mixtral-8x7b-32768",
        "llama3-8b-8192",
        "llama3-70b-8192",
        "llama-3.1-70b-versatile",   # retired from free tier
        "gemma-7b-it",               # retired
    }
    if llm_provider == "groq" and agent_model in _DECOMMISSIONED_GROQ:
        new_model = "llama-3.3-70b-versatile"
        logger.warning(
            "Groq model '%s' is decommissioned — auto-upgrading to '%s'",
            agent_model, new_model,
        )
        agent_model = new_model

    # Admin-configured response length cap (AgentDetail.tsx "Max Tokens" field).
    # Was previously ignored here — every provider call hardcoded 150 regardless
    # of this setting, so the UI field had no effect on the in-browser tester.
    #
    # Scaled by language for the same reason the voice path is: the configured
    # number is an English-equivalent budget, and Malayalam/Kannada cost ~7.6x/9x
    # more tokens for the same sentence, so a flat cap truncated them mid-word here
    # too. See backend/services/token_budget.py for the measurements.
    from backend.services.token_budget import response_token_budget

    max_tokens = response_token_budget(
        agent.max_response_tokens,
        getattr(agent, "language", None) or getattr(agent, "tts_language", None),
    )

    try:
        if llm_provider in ("gemini", "openai", "anthropic", "groq", "deepseek"):
            response = await _dispatch_llm(llm_provider, api_key, system_prompt,
                                           history, agent_model, max_tokens)
        else:
            response = generate_demo_response(agent, user_message, history)

        # Honest booking (audit FIX 4): if the model emitted an [ACTION:] tag,
        # AWAIT the real DB write and regenerate the reply with the true outcome
        # injected. A confirmation is never spoken before the row exists — the
        # same guarantee the voice pipeline's BookingProcessor provides.
        response = await _handle_booking_action(
            response, agent=agent, db=db, history=history,
            provider=llm_provider, api_key=api_key, model=agent_model,
            max_tokens=max_tokens, base_system_prompt=system_prompt,
            user_language=user_language, websocket=websocket,
        )

        # Add response to history
        history.append({"role": "assistant", "content": response})

        # Keep history to last 10 turns to avoid token overflow
        if len(history) > 20:
            history = history[-20:]
            _conversation_history[session_key] = history

        # Write-through so a follow-up turn on the same session_id, landing on
        # a different uvicorn worker, still sees this turn (see the
        # read-through comment above — WS voice turns are excluded).
        # Not atomic — see redis_client.save_chat_history's race note.
        if websocket is None:
            await redis_client.save_chat_history(str(session_key), history)

        return response
        
    except Exception as e:
        logger.error(f"LLM call failed (Provider: {llm_provider}, Model: {agent_model}): {type(e).__name__}: {e}", exc_info=True)
        error_msg = str(e) or type(e).__name__
        err_low = error_msg.lower()

        # ── Auto-fallback to another provider on geo/region/auth/quota errors ──
        # e.g. Gemini returns 400 "User location is not supported for the API use"
        # when the backend is hosted in an unsupported region (Render free-tier
        # often runs in such regions). Transparently retry with the next
        # available provider so the user never sees a broken reply.
        should_fallback = (
            "user location is not supported" in err_low
            or "location is not supported" in err_low
            or "unsupported_country" in err_low
            or "permission_denied" in err_low
            or "failed_precondition" in err_low
            or "model_decommissioned" in err_low      # Groq/OpenAI retired model
            or "decommissioned" in err_low            # generic form
            or "no longer supported" in err_low       # Groq error wording
            or "deprecated" in err_low
            or " 400" in err_msg_pad(error_msg)  # see helper
            or " 401" in err_msg_pad(error_msg)
            or " 403" in err_msg_pad(error_msg)
            or " 404" in err_msg_pad(error_msg)
            # A rate limit / exhausted quota is the case where another provider
            # helps MOST — the current one is refusing to serve at all. This
            # used to be excluded, so a Groq 429 dead-ended the conversation
            # (and the booking in progress) even when another key was
            # configured. Observed in production 2026-08-10.
            or is_rate_limit_error(error_msg)
        )

        if should_fallback:
            # (provider, model_override) candidates, tried in order.
            #
            # Groq's free tier meters tokens-per-day PER MODEL, so when the
            # primary model's daily budget is gone another Groq model still has
            # its own untouched budget — that is the cheapest possible recovery,
            # and it needs no extra API key. gpt-oss-120b is used because it
            # matched llama-3.3-70b on ACTION-tag emission when measured
            # (3/3 each) at comparable latency; the smaller llama-3.1-8b-instant
            # did NOT (1/3, and it fabricated a booking confirmation), so it is
            # deliberately not in this list.
            #
            # NOTE: groq/compound* is also NOT a valid escape hatch — it routes
            # to llama-3.3-70b internally and returns that same exhausted-budget
            # 429.
            candidates: list[tuple[str, str | None]] = []
            if is_rate_limit_error(error_msg):
                for alt_model in SAME_PROVIDER_RATE_LIMIT_MODELS.get(llm_provider, ()):
                    if alt_model != agent_model:
                        candidates.append((llm_provider, alt_model))
            candidates += [(p, None) for p in ("groq", "openai", "anthropic", "deepseek", "gemini")
                           if p != llm_provider]

            for fb_provider, fb_model_override in candidates:
                fb_env = {
                    "groq": settings.groq_api_key or os.getenv("GROQ_API_KEY"),
                    "openai": settings.openai_api_key or os.getenv("OPENAI_API_KEY"),
                    "anthropic": settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"),
                    "deepseek": settings.deepseek_api_key or os.getenv("DEEPSEEK_API_KEY"),
                    "gemini": settings.gemini_api_key or os.getenv("GEMINI_API_KEY"),
                }.get(fb_provider)

                # Prefer a DB key over the env one — but ONLY if it actually
                # decrypts. get_key_raw() returns "" for a row stored under a
                # different SECRET_KEY (decrypt_secret swallows InvalidToken),
                # and assigning that unconditionally WIPES a perfectly good env
                # key, so `if not fb_env: continue` below then skips the
                # provider entirely.
                #
                # That is not hypothetical: production's api_key_configs row for
                # groq decrypts to "" (verified 2026-08-10), which silently
                # disabled Groq as a fallback target for every provider — and
                # was why the gpt-oss same-model retry never ran. The primary
                # key lookup above already has this precedence right; this is
                # the same rule.
                try:
                    res_fb = await db.execute(
                        select(ApiKeyConfig).where(
                            ApiKeyConfig.provider == fb_provider,
                            ApiKeyConfig.is_active == True,
                        ).limit(1)
                    )
                    fb_cfg = res_fb.scalars().first()
                    if fb_cfg and fb_cfg.api_key_enc:
                        db_key = fb_cfg.get_key_raw()
                        if db_key:
                            fb_env = db_key
                        else:
                            logger.warning(
                                "api_key_configs row for %s does not decrypt — falling back to the "
                                "environment key for this provider.", fb_provider,
                            )
                except Exception:
                    pass

                if not fb_env:
                    continue
                fb_key = fb_env.strip()
                fb_model = fb_model_override or PROVIDER_DEFAULTS.get(fb_provider, "")
                try:
                    logger.warning(
                        "Falling back from %s/%s → %s/%s due to error: %s",
                        llm_provider, agent_model, fb_provider, fb_model, error_msg[:200],
                    )
                    if fb_provider == "gemini":
                        response = await call_gemini(fb_key, system_prompt, history, fb_model)
                    elif fb_provider == "openai":
                        response = await call_openai(fb_key, system_prompt, history, fb_model)
                    elif fb_provider == "anthropic":
                        response = await call_anthropic(fb_key, system_prompt, history, fb_model)
                    elif fb_provider == "groq":
                        response = await call_groq(fb_key, system_prompt, history, fb_model)
                    elif fb_provider == "deepseek":
                        response = await call_openai(
                            fb_key, system_prompt, history, fb_model,
                            base_url="https://api.deepseek.com/v1",
                        )
                    else:
                        continue

                    # Same honest booking handling as the primary path.
                    response = await _handle_booking_action(
                        response, agent=agent, db=db, history=history,
                        provider=fb_provider, api_key=fb_key, model=fb_model,
                        max_tokens=max_tokens, base_system_prompt=system_prompt,
                        user_language=user_language, websocket=websocket,
                    )

                    history.append({"role": "assistant", "content": response})
                    if len(history) > 20:
                        history = history[-20:]
                        _conversation_history[session_key] = history
                    if websocket is None:
                        await redis_client.save_chat_history(str(session_key), history)
                    return response
                except Exception as fb_e:
                    logger.error(
                        "Fallback provider %s also failed: %s",
                        fb_provider, fb_e,
                    )
                    continue

        # Every fallback provider is exhausted or unconfigured by this point, so
        # tell the patient what actually went wrong (see describe_llm_failure).
        described = describe_llm_failure(error_msg)
        if described:
            return described

        return "I'm sorry, I'm having trouble processing that right now. Could you please repeat?"


def err_msg_pad(s: str) -> str:
    """Pad error string with spaces so HTTP status code substring matches reliably."""
    return f" {s} "


# \b so a token COUNT never reads as a status code. The old check was a bare
# `"429" in error_msg`, which also matched "Requested 4291 tokens" — i.e. an
# unrelated failure could be reported to the patient as a rate limit. (Padding
# alone does not fix this: " 429" is a prefix of " 4291".)
_HTTP_429_RE = re.compile(r"\b429\b")

# Groq spells the wait as "Please try again in 14m46.464s."
_RETRY_HINT_RE = re.compile(
    r"try again in\s+(?:(\d+)\s*h)?(?:(\d+)\s*m)?(?:([\d.]+)\s*s)?", re.IGNORECASE,
)

# Markers that this is a LONG-HORIZON budget (a daily/monthly allowance), not a
# short burst limit. "Please wait a moment" is a lie for these — the next
# message will fail exactly the same way.
_LONG_QUOTA_MARKERS = (
    "per day", "tokens per day", "tpd", "daily limit", "per month", "quota exceeded",
    "insufficient_quota", "billing", "credit balance",
)


# Same-provider models to retry when the configured one is rate limited.
#
# Groq meters tokens-per-day PER MODEL on the free tier, so a second Groq model
# is a whole extra daily budget reachable with the SAME api key — the cheapest
# recovery available, and the first thing tried before switching provider.
#
# Chosen empirically (2026-08-10) on this app's hardest task, emitting a
# well-formed [ACTION: ...] tag from a booking conversation:
#     llama-3.3-70b-versatile  3/3   (current primary)
#     openai/gpt-oss-120b      3/3   <- listed here
#     openai/gpt-oss-20b       3/3
#     llama-3.1-8b-instant     1/3   <- NOT listed: it also fabricated
#                                       "your appointment is booked" with no
#                                       tag, i.e. confirmed a booking that was
#                                       never written.
SAME_PROVIDER_RATE_LIMIT_MODELS: dict[str, tuple[str, ...]] = {
    "groq": ("openai/gpt-oss-120b", "openai/gpt-oss-20b"),
}


def is_rate_limit_error(error_msg: str) -> bool:
    low = (error_msg or "").lower()
    return bool(
        _HTTP_429_RE.search(error_msg or "")
        or "rate limit" in low
        or "rate_limit" in low
        or "too many requests" in low
        or "quota exceeded" in low
        or "insufficient_quota" in low
    )


def retry_after_seconds(error_msg: str) -> float | None:
    """Seconds the provider asked us to wait, if it said so."""
    m = _RETRY_HINT_RE.search(error_msg or "")
    if not m or not any(m.groups()):
        return None
    h, mins, secs = m.groups()
    return (int(h or 0) * 3600) + (int(mins or 0) * 60) + float(secs or 0)


def describe_llm_failure(error_msg: str) -> str | None:
    """The patient-facing sentence for a KNOWN failure mode, or None.

    Every branch here has to survive the same test the booking replies do: it
    must tell the patient something true and leave them with a next step. The
    old code answered every 429 with "Please wait a moment before speaking
    again", which on 2026-08-10 was shown for an exhausted tokens-per-DAY
    budget ("Used 99463 of 100000, try again in 14m46s") — waiting a moment
    could not possibly help, and the booking simply dead-ended.
    """
    if not error_msg:
        return None
    low = error_msg.lower()

    if is_rate_limit_error(error_msg):
        wait = retry_after_seconds(error_msg)
        long_budget = any(k in low for k in _LONG_QUOTA_MARKERS) or (wait or 0) > 120
        if long_budget:
            # Not recoverable inside this conversation — say so, and hand the
            # patient somewhere that can actually finish the booking.
            return ("I'm sorry — our AI assistant has reached its usage limit for today, so I can't "
                    "continue right now. Please call the clinic directly and our staff will finish "
                    "your booking.")
        if wait:
            return (f"I'm handling a lot of requests right now. Please try again in about "
                    f"{max(1, round(wait))} seconds.")
        return "I'm handling a lot of requests right now. Please try again in a few seconds."

    if "safety" in low:
        return "I'm sorry, I cannot respond to that prompt due to safety guidelines. How else can I help?"
    if "timeout" in low or "timed out" in low:
        return "The AI service took too long to respond. Please try again."
    return None


async def call_gemini(api_key: str, system_prompt: str,
                       history: list, model: str, max_tokens: int = 150) -> str:
    import httpx
    
    # Convert history to Gemini format, ensuring alternating roles
    contents = []
    last_role = None
    
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        
        if not contents:
            if role == "model":
                # Must start with user
                contents.append({"role": "user", "parts": [{"text": "Hello."}]})
                last_role = "user"
        
        if role == last_role:
            # Group identical roles
            contents[-1]["parts"][0]["text"] += f"\n\n{msg['content']}"
        else:
            contents.append({
                "role": role,
                "parts": [{"text": msg["content"]}]
            })
            last_role = role
    
    # ── Auto-remap deprecated models ───────────────────────────────────────────
    DEPRECATED_GEMINI_MODELS = {
        "gemini-2.0-flash": "gemini-2.5-flash",
        "gemini-1.0-pro": "gemini-1.5-pro",
    }
    if model in DEPRECATED_GEMINI_MODELS:
        new_model = DEPRECATED_GEMINI_MODELS[model]
        logger.info("Remapping deprecated model '%s' → '%s'", model, new_model)
        model = new_model

    # Ensure model starts with 'models/' if it's a standard model name
    gemini_model = model
    if not gemini_model.startswith("models/"):
        gemini_model = f"models/{gemini_model}"
    
    max_retries = 2
    async with httpx.AsyncClient(timeout=15.0) as client:
        for attempt in range(max_retries):
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/{gemini_model}:generateContent",
                headers={"Content-Type": "application/json"},
                params={"key": api_key},
                json={
                    "system_instruction": {
                        "parts": [{"text": system_prompt}]
                    },
                    "contents": contents,
                    "generationConfig": {
                        "maxOutputTokens": max_tokens,
                        "temperature": 0.7
                    }
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                try:
                    answer = data["candidates"][0]["content"]["parts"][0]["text"]
                    return answer
                except (KeyError, IndexError):
                    logger.error(f"Gemini response unexpected structure: {data}")
                    raise Exception("Gemini returned an empty or malformed candidate (check safety filters)")
            elif response.status_code == 429:
                wait = 2 ** attempt
                logger.warning(f"Gemini 429 rate-limited, retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(wait)
                continue
            else:
                logger.error(f"Gemini API Error: {response.status_code} - {response.text}")
                try:
                    error_msg = response.json().get("error", {}).get("message", "Unknown error")
                except Exception:
                    error_msg = response.text
                raise Exception(f"Gemini error: {response.status_code} - {error_msg}")
        
        raise Exception("Gemini error: 429 — rate limit exceeded after retries")


async def call_openai(api_key: str, system_prompt: str,
                       history: list, model: str,
                       base_url: str = "https://api.openai.com/v1",
                       max_tokens: int = 150) -> str:
    import httpx

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.7
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        else:
            raise Exception(f"OpenAI-compatible API error: {response.status_code}")


async def call_anthropic(api_key: str, system_prompt: str,
                          history: list, model: str, max_tokens: int = 150) -> str:
    import httpx

    messages = []
    for msg in history:
        messages.append({
            "role": msg["role"],
            "content": msg["content"]
        })

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "system": system_prompt,
                "messages": messages,
                "max_tokens": max_tokens
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            return data["content"][0]["text"]
        else:
            raise Exception(f"Anthropic error: {response.status_code}")


# Groq-hosted REASONING models: they spend tokens thinking before they emit a
# single visible character. Measured 2026-08-10: at the agents' normal
# max_tokens (150-250) openai/gpt-oss-* returns EMPTY content every time — the
# whole allowance goes to reasoning. Given room (and effort capped to "low")
# the same models match llama-3.3-70b on this app's hardest task, emitting a
# well-formed [ACTION: ...] tag 3/3. So these two knobs are not tuning, they
# are the difference between "works" and "replies with nothing".
_GROQ_REASONING_MODEL_MARKERS = ("gpt-oss", "qwen3", "deepseek-r1")
_GROQ_REASONING_MIN_TOKENS = 800


def is_groq_reasoning_model(model: str) -> bool:
    return any(marker in (model or "").lower() for marker in _GROQ_REASONING_MODEL_MARKERS)


async def call_groq(api_key: str, system_prompt: str,
                             history: list, model: str, max_tokens: int = 150) -> str:
    import httpx

    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        # groq requires role user or assistant
        messages.append({
            "role": "assistant" if msg["role"] == "model" else msg["role"],
            "content": msg["content"]
        })

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }
    if is_groq_reasoning_model(model):
        payload["reasoning_effort"] = "low"
        payload["max_tokens"] = max(max_tokens, _GROQ_REASONING_MIN_TOKENS)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json=payload,
        )

        if response.status_code == 200:
            data = response.json()
            content = (data["choices"][0]["message"].get("content") or "").strip()
            if not content:
                # Never hand an empty string back as if it were a reply — raise
                # so the caller's fallback chain gets a chance to answer.
                raise Exception(f"Groq returned an empty completion for model {model}")
            return content
        else:
            logger.error(f"Groq API error: {response.status_code} - {response.text}")
            raise Exception(f"Groq API error: {response.status_code} - {response.text}")


def generate_demo_response(agent: AgentConfig, 
                            user_message: str,
                            history: list) -> str:
    """Scripted demo responses when no LLM key is configured"""
    
    msg = user_message.lower()
    
    if any(word in msg for word in ["appointment", "book", "schedule", "visit"]):
        return ("I'd be happy to help you book an appointment. "
                "Could you tell me which doctor you'd like to see "
                "and your preferred date and time?")
    
    elif any(word in msg for word in ["doctor", "specialist", "physician"]):
        return ("We have several specialists available. "
                "Are you looking for a general physician, "
                "or a specific specialist?")
    
    elif any(word in msg for word in ["hours", "open", "timing", "time"]):
        return ("Our clinic is open Monday to Saturday, "
                "9 AM to 6 PM. We are closed on Sundays "
                "and public holidays.")
    
    elif any(word in msg for word in ["cancel", "reschedule", "change"]):
        return ("I can help you with that. "
                "Could you please provide your appointment ID "
                "or the phone number you booked with?")
    
    elif any(word in msg for word in ["emergency", "urgent", "help"]):
        return ("If this is a medical emergency, "
                "please call emergency services immediately. "
                "For urgent appointments, I can check "
                "our earliest available slot.")
    
    elif any(word in msg for word in ["hello", "hi", "hey", "good"]):
        return (f"Hello! Welcome to {agent.agent_name}. "
                "How can I assist you today?")
    
    elif any(word in msg for word in ["bye", "goodbye", "thank", "thanks"]):
        return ("Thank you for calling. "
                "Have a great day and stay healthy!")
    
    else:
        return ("I understand. Could you tell me more about "
                "how I can help you today? "
                "I can assist with appointments, clinic information, "
                "and general inquiries.")


# ── TTS Logic ─────────────────────────────────────────────────────────────────

async def synthesize_speech(agent: AgentConfig, text: str, language_override: str = "") -> bytes | None:
    """Convert text to speech using configured TTS provider"""
    
    tts_provider = agent.tts_provider or "sarvam"
    tts_language = language_override or agent.language or "en-IN"
    
    # Get API key from environment (avoid opening a new DB session to prevent pool exhaustion)
    api_key = None
    if tts_provider == "sarvam":
        api_key = settings.sarvam_api_key or os.getenv("SARVAM_API_KEY")
    elif tts_provider == "elevenlabs":
        api_key = os.getenv("ELEVENLABS_API_KEY")
    elif tts_provider == "openai_tts":
        api_key = os.getenv("OPENAI_API_KEY")
    
    if not api_key:
        logger.warning(f"No TTS API key for provider: {tts_provider}")
        return None
    
    try:
        if tts_provider == "sarvam":
            raw_voice = (agent.tts_voice or "priya").strip()
            sarvam_voice_map = {
                "meera": "shreya",
                "pavithra": "kavitha",
                "maitreyi": "priya",
                "arvind": "rahul",
                "amol": "aditya",
                "amartya": "rohan",
                "diya": "ritu",
                "neel": "amit",
                "misha": "simran",
                "vian": "shubh",
            }
            normalized_voice = sarvam_voice_map.get(raw_voice.lower(), raw_voice.lower())
            # Normalize language before sending to Sarvam
            normalized_language = normalize_sarvam_language(tts_language)
            return await sarvam_synthesize(
                api_key=api_key,
                text=text,
                voice=normalized_voice,
                model=agent.tts_model or "bulbul:v3",
                language=normalized_language,
                pitch=agent.tts_pitch or 0.0,
                pace=agent.tts_pace or 1.0,
                loudness=agent.tts_loudness or 1.0,
                enable_preprocessing=agent.tts_input_preprocessing if agent.tts_input_preprocessing is not None else True,
            )
        
        elif tts_provider == "elevenlabs":
            return await elevenlabs_synthesize(
                api_key=api_key,
                text=text,
                voice_id=agent.tts_voice or "21m00Tcm4TlvDq8ikWAM",
                model_id=agent.tts_model or "eleven_flash_v2_5",
                stability=agent.tts_stability,
                similarity_boost=agent.tts_clarity,
                style=agent.tts_style,
                use_speaker_boost=bool(agent.tts_use_speaker_boost) if agent.tts_use_speaker_boost is not None else None,
                speed=agent.tts_speed,
            )
        
        elif tts_provider == "openai_tts":
            openai_model = agent.tts_model or "gpt-4o-mini-tts"
            if not (openai_model.startswith("gpt-") or openai_model.startswith("tts-")):
                openai_model = "gpt-4o-mini-tts"
            return await openai_synthesize(
                api_key=api_key,
                text=text,
                voice=agent.tts_voice or "nova",
                model=openai_model,
            )
        
        else:
            logger.warning(f"Unknown TTS provider: {tts_provider}")
            return None
            
    except Exception as e:
        logger.error(f"TTS synthesis failed ({tts_provider}): {e}", exc_info=True)

        # ── AUTO-FALLBACK: If ElevenLabs/OpenAI TTS fails, try Sarvam ────────
        # This ensures the agent call NEVER goes silent during a live call.
        # Common failures: ElevenLabs 401 (quota exhausted), 429 (rate limit),
        # network timeouts, OpenAI TTS outages.
        if tts_provider != "sarvam":
            sarvam_key = settings.sarvam_api_key or os.getenv("SARVAM_API_KEY")
            if sarvam_key:
                try:
                    logger.warning(
                        "Auto-fallback: %s TTS failed, falling back to Sarvam for this utterance",
                        tts_provider,
                    )
                    fallback_lang = normalize_sarvam_language(tts_language)
                    return await sarvam_synthesize(
                        api_key=sarvam_key,
                        text=text,
                        voice="priya",
                        model="bulbul:v3",
                        language=fallback_lang,
                        pitch=0.0,
                        pace=1.0,
                        loudness=1.0,
                    )
                except Exception as fb_err:
                    logger.error("Sarvam fallback also failed: %s", fb_err)

        return None


# ISSUE 5: Sarvam-specific wrapper with retry + size validation
async def sarvam_synthesize_with_retry(
    agent: AgentConfig,
    text: str,
    language_override: str = "",
    max_retries: int = 2,
) -> bytes | None:
    """Wraps sarvam_synthesize with retry logic and result size validation.
    Falls back to synthesize_speech (which supports non-Sarvam providers) on
    all retries failing."""
    api_key = getattr(settings, 'sarvam_api_key', None) or os.getenv("SARVAM_API_KEY")
    if not api_key:
        logger.warning("sarvam_synthesize_with_retry: no API key")
        return None

    tts_language = normalize_sarvam_language(language_override or agent.language or "en-IN")

    # Apply the same legacy-voice → v3-speaker mapping used by synthesize_speech()
    # so that v2-only voices (meera, pavithra, etc.) are remapped before reaching
    # sarvam_synthesize(), eliminating the compatibility warning at the source.
    sarvam_voice_map = {
        "meera": "shreya",
        "pavithra": "kavitha",
        "maitreyi": "priya",
        "arvind": "rahul",
        "amol": "aditya",
        "amartya": "rohan",
        "diya": "ritu",
        "neel": "amit",
        "misha": "simran",
        "vian": "shubh",
    }
    raw_voice = (agent.tts_voice or "priya").strip().lower()
    normalized_voice = sarvam_voice_map.get(raw_voice, raw_voice)

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            audio = await sarvam_synthesize(
                api_key=api_key,
                text=text,
                voice=normalized_voice,
                model=agent.tts_model or "bulbul:v3",
                language=tts_language,
                pitch=agent.tts_pitch or 0.0,
                pace=agent.tts_pace or 1.1,
                loudness=agent.tts_loudness or 1.5,
                enable_preprocessing=agent.tts_input_preprocessing if agent.tts_input_preprocessing is not None else True,
            )
            if audio and len(audio) >= 500:
                if attempt > 1:
                    logger.info(f"Sarvam TTS succeeded on attempt {attempt}")
                return audio
            logger.warning(f"Sarvam TTS attempt {attempt}: response too small ({len(audio) if audio else 0}B), retrying")
        except Exception as exc:
            last_exc = exc
            logger.warning(f"Sarvam TTS attempt {attempt}/{max_retries} failed: {exc}")
            if attempt < max_retries:
                await asyncio.sleep(0.5 * attempt)

    logger.error(f"Sarvam TTS failed after {max_retries} attempts. Last error: {last_exc}")
    return None


# ── Speaker compatibility map ──────────────────────────────────────────────────────

# Complete, authoritative speaker list for each Sarvam TTS model.
# Source: Sarvam API error message for bulbul:v3 (April 2026).
SARVAM_MODEL_SPEAKERS: dict[str, list[str]] = {
    "bulbul:v3": [
        "aditya", "ritu", "ashutosh", "priya", "neha", "rahul",
        "pooja", "rohan", "simran", "kavya", "amit", "dev",
        "ishita", "shreya", "ratan", "varun", "manan", "sumit",
        "roopa", "kabir", "aayan", "shubh", "advait", "anand",
        "tanya", "tarun", "sunny", "mani", "gokul", "vijay",
        "shruti", "suhani", "mohit", "kavitha", "rehan", "soham",
        "rupali", "niharika",
    ],
    "bulbul:v2": [
        "meera", "pavithra", "maitreyi", "arvind", "amol",
        "amartya", "diya", "neel", "misha", "vian", "arjun",
        "maya", "anushka", "karun", "hitesh", "shubh",
    ],
    "bulbul:v1": [
        "meera", "pavithra", "maitreyi", "arvind", "amol", "amartya",
    ],
}

# Default speaker per model — female, clear voice, confirmed working
SARVAM_MODEL_DEFAULT_SPEAKER: dict[str, str] = {
    "bulbul:v3": "priya",
    "bulbul:v2": "meera",
    "bulbul:v1": "meera",
}


def get_compatible_speaker(model: str, requested_speaker: str) -> str:
    """
    Returns requested_speaker if it is valid for the given model.
    Falls back to the model's default speaker when not compatible.
    Logs a warning whenever a fallback is used so the issue is visible in logs.
    """
    valid_speakers = SARVAM_MODEL_SPEAKERS.get(model, [])

    if not valid_speakers:
        # Unknown model — pass the speaker through and let the API decide
        return requested_speaker

    if requested_speaker in valid_speakers:
        return requested_speaker

    fallback = SARVAM_MODEL_DEFAULT_SPEAKER.get(model, valid_speakers[0])
    logger.info(
        "Speaker '%s' auto-remapped to '%s' for model '%s'",
        requested_speaker, fallback, model,
    )
    return fallback


# ── Valid Sarvam TTS language codes ─────────────────────────────────────────────
# Authoritative list from Sarvam API validation (May 2026).
SARVAM_VALID_LANGUAGES: set[str] = {
    "as-IN", "bn-IN", "brx-IN", "doi-IN", "en-IN", "gu-IN",
    "hi-IN", "kn-IN", "kok-IN", "ks-IN", "mai-IN", "ml-IN",
    "mni-IN", "mr-IN", "ne-IN", "od-IN", "pa-IN", "sa-IN",
    "sat-IN", "sd-IN", "ta-IN", "te-IN", "ur-IN",
}

def normalize_sarvam_language(language: str) -> str:
    """Return a valid Sarvam target_language_code.
    If the code is not supported (e.g. ar-SA for Arabic), fall back to en-IN.
    """
    if not language:
        return "en-IN"
    lang = language.strip()
    if lang in SARVAM_VALID_LANGUAGES:
        return lang
    # Try common prefix match (e.g. 'hi' → 'hi-IN')
    prefix = lang.split("-")[0].lower()
    for valid in SARVAM_VALID_LANGUAGES:
        if valid.startswith(prefix + "-"):
            logger.info("Sarvam TTS: remapped language '%s' → '%s'", language, valid)
            return valid
    logger.info(
        "Sarvam TTS: language '%s' auto-remapped to 'en-IN'",
        language,
    )
    return "en-IN"


async def sarvam_synthesize(api_key: str, text: str, voice: str,
                               model: str, language: str,
                               pitch: float, pace: float,
                               loudness: float,
                               enable_preprocessing: bool = True) -> bytes:
    import httpx

    normalized_text = (text or "").strip()
    if not normalized_text:
        raise Exception("Sarvam TTS error: empty text payload")

    # Normalise model — must start with 'bulbul:'
    normalized_model = model if model and model.startswith("bulbul:") else "bulbul:v3"

    # FIX: Ensure the speaker is actually valid for this model version.
    # This is the authoritative compatibility check — prevents the
    # 'Speaker X is not compatible with model bulbul:v3' 400 error.
    normalized_voice = get_compatible_speaker(
        normalized_model,
        (voice or "priya").lower().strip(),
    )

    # FIX: Validate language code — Sarvam only supports Indian languages.
    # Unsupported codes like 'ar-SA' would cause a 400 validation error.
    normalized_language = normalize_sarvam_language(language)

    # Clamp to Sarvam's accepted ranges — same bounds as pipeline.py's live-call
    # path. Callers pass agent.tts_pace/pitch/loudness straight from the DB with
    # no validation at write time, so a stray out-of-range stored value would
    # otherwise reach Sarvam unclamped and 400.
    clamped_pace = min(max(float(pace), 0.5), 2.0)
    clamped_pitch = min(max(float(pitch), -0.75), 0.75)
    clamped_loudness = min(max(float(loudness), 0.3), 3.0)

    # Build payload based on model capabilities.
    payload = {
        # Sarvam REST v3 expects `text` for synchronous synthesis.
        "text": normalized_text,
        "target_language_code": normalized_language,
        "speaker": normalized_voice,
        "model": normalized_model,
        "pace": clamped_pace,
        "enable_preprocessing": enable_preprocessing,
        "speech_sample_rate": 24000,
    }

    # Only bulbul:v2 supports pitch/loudness — v3 and v3-beta both reject them.
    if normalized_model == "bulbul:v2":
        payload["pitch"] = clamped_pitch
        payload["loudness"] = clamped_loudness

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            "https://api.sarvam.ai/text-to-speech",
            headers={
                "api-subscription-key": api_key,
                "Content-Type": "application/json"
            },
            json=payload,
        )
        
        if response.status_code == 200:
            data = response.json()
            # Sarvam returns base64 encoded audio
            audios = data.get("audios", [])
            if audios and audios[0]:
                return base64.b64decode(audios[0])
            
            logger.error(f"Sarvam TTS empty audio list. Response: {data}")
            raise Exception("No audio content returned from Sarvam")
        else:
            body = response.text
            try:
                body = response.json()
            except Exception:
                pass
            logger.error(f"Sarvam TTS API Error: {response.status_code} - {body}")
            raise Exception(f"Sarvam TTS error: {response.status_code} - {body}")




async def elevenlabs_synthesize(api_key: str, text: str,
                                  voice_id: str,
                                  model_id: str = "eleven_flash_v2_5",
                                  stability: float | None = None,
                                  similarity_boost: float | None = None,
                                  style: float | None = None,
                                  use_speaker_boost: bool | None = None,
                                  speed: float | None = None) -> bytes:
    import httpx

    voice_settings = {
        "stability": stability if stability is not None else 0.5,
        "similarity_boost": similarity_boost if similarity_boost is not None else 0.75,
    }
    if style is not None:
        voice_settings["style"] = style
    if use_speaker_boost is not None:
        voice_settings["use_speaker_boost"] = use_speaker_boost
    if speed is not None:
        # ElevenLabs only accepts speed in [0.7, 1.2]; the agent's slider
        # goes 0.5-2.0, so clamp rather than let the API 400.
        voice_settings["speed"] = min(max(speed, 0.7), 1.2)

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg"
            },
            json={
                "text": text,
                "model_id": model_id,
                "voice_settings": voice_settings
            }
        )
        
        if response.status_code == 200:
            return response.content
        elif response.status_code == 401:
            raise Exception(
                f"ElevenLabs TTS: 401 — API key invalid or character quota exhausted. "
                f"Free tier resets monthly. Upgrade at elevenlabs.io or wait for reset."
            )
        else:
            raise Exception(f"ElevenLabs TTS: {response.status_code}")


async def openai_synthesize(api_key: str, text: str, voice: str, model: str = "tts-1") -> bytes:
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "input": text,
                "voice": voice,
                "response_format": "mp3"
            }
        )
        
        if response.status_code == 200:
            return response.content
        else:
            raise Exception(f"OpenAI TTS: {response.status_code}")

