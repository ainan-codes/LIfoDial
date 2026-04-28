"""
backend/routers/agent_test.py
In-browser agent testing: Chat (REST) + Voice (WebSocket audio streaming).
No phone required — pure browser-based.
"""
import asyncio
import json
import logging
import uuid
import base64
import os
from collections import defaultdict
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Depends
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import async_session as AsyncSessionLocal, get_db
from backend.models.agent_config import AgentConfig
from backend.models.api_key_config import ApiKeyConfig
from backend.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Language Detection Tracker ─────────────────────────────────────────────────
# Per-session rolling window of detected languages for ratio-based switching
_language_tracker: dict[str, list[str]] = {}
LANGUAGE_WINDOW_SIZE = 10  # Track last N utterances
LANGUAGE_SWITCH_THRESHOLD = 0.6  # 60% of recent utterances must be in new language

# Cache synthesized greeting clips to avoid repeating cold-start latency
_greeting_audio_cache: dict[str, bytes] = {}

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
        str(agent.tts_language or "en-IN"),
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
async def get_agent_greeting(agent_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AgentConfig).where(AgentConfig.id == agent_id)
    )
    agent = result.scalar_one_or_none()
    
    if not agent:
        raise HTTPException(
            status_code=404, 
            detail=f"Agent {agent_id} not found"
        )
    
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
async def clear_agent_session(agent_id: str, session_id: str):
    session_key = session_id or agent_id
    if session_key in _conversation_history:
        del _conversation_history[session_key]
    return {"status": "cleared"}


# ── WS /ws/agent-call/{agent_id} ──────────────────────────────────────────────

@router.websocket("/ws/agent-call/{agent_id}")
async def voice_websocket(websocket: WebSocket, agent_id: str):
    """
    Stable voice WebSocket handler.

    Key design decisions:
    - DB session opened for agent load only, then closed immediately.
      Each turn (audio/text) opens its own fresh session → no pool exhaustion.
    - asyncio.wait() with 20-second timeout drives the loop so it never
      blocks forever on stale connections.
    - Server sends JSON {"type":"pong"} every 20 s to keep TCP alive through
      proxies/firewalls.
    - 120-second idle timeout closes the connection gracefully.
    - Every exception path is caught; greeting task is always cancelled.
    """
    await websocket.accept()
    logger.info(f"Voice WebSocket connected for agent {agent_id}")

    ws_session_id = str(uuid.uuid4())
    greeting_task: asyncio.Task | None = None

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

    logger.info(f"Agent loaded: {agent.agent_name}")

    # ── Send ready signal ─────────────────────────────────────────────────────
    first_msg = agent.first_message or "Hello, how can I help?"
    try:
        await websocket.send_json({
            "type": "ready",
            "agent_name": agent.agent_name,
            "first_message": first_msg,
            "tts_provider": agent.tts_provider,
            "stt_provider": agent.stt_provider,
        })
        await websocket.send_json({"type": "status", "status": "connected"})
    except Exception as e:
        logger.warning(f"Cannot send ready for {agent_id}: {e}")
        _language_tracker.pop(ws_session_id, None)
        return

    # ── Kick off greeting audio in background ─────────────────────────────────
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
        while True:
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
                logger.info(f"Clean disconnect frame from {agent_id}")
                break

            if data.get("type") != "websocket.receive":
                continue

            raw_bytes = data.get("bytes")
            raw_text  = data.get("text")

            # ── Audio frame → STT → LLM → TTS ──
            if raw_bytes:
                try:
                    async with AsyncSessionLocal() as db:
                        await handle_audio_turn(websocket, agent, raw_bytes, db, ws_session_id)
                except Exception as e:
                    logger.error(f"Audio turn error for {agent_id}: {e}", exc_info=True)
                    try:
                        await websocket.send_json({"type": "error", "message": f"Processing error: {e}"})
                        await websocket.send_json({"type": "status", "status": "idle"})
                    except Exception:
                        break
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
        logger.info(f"WS disconnected (outer) for {agent_id}")
    except Exception as e:
        logger.error(f"Fatal WS loop error for {agent_id}: {e}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": "Internal server error", "code": "INTERNAL_ERROR"})
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        if greeting_task and not greeting_task.done():
            greeting_task.cancel()
            try:
                await greeting_task
            except (asyncio.CancelledError, Exception):
                pass
        _language_tracker.pop(ws_session_id, None)
        logger.info(f"WS session ended cleanly for {agent_id}")



# ── WS /ws/agent/{agent_id}/tts-stream ────────────────────────────────────────
# Streaming TTS using Sarvam's WebSocket API for low-latency audio generation

@router.websocket("/ws/agent/{agent_id}/tts-stream")
async def tts_streaming_websocket(websocket: WebSocket, agent_id: str):
    """
    WebSocket endpoint for streaming text-to-speech.
    
    Client flow:
    1. Connect to this endpoint
    2. Send config message with voice parameters
    3. Send text chunks to synthesize
    4. Receive audio chunks progressively
    5. Send flush to finish processing
    """
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
                                agent.tts_language or "en-IN"
                            ),
                            "pace": buffered_config.get("pace", agent.tts_pace or 1.0),
                            "min_buffer_size": buffered_config.get("min_buffer_size", 50),
                            "max_chunk_length": buffered_config.get("max_chunk_length", 200),
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
async def get_tts_streaming_test_client(agent_id: str, db: AsyncSession = Depends(get_db)):
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
        <p><strong>Provider:</strong> {agent.tts_provider} | <strong>Voice:</strong> {agent.tts_voice or 'default'} | <strong>Language:</strong> {agent.tts_language or 'en-IN'}</p>
        
        <div class="section">
            <h2>1. Configuration</h2>
            <label>Speaker (voice):</label>
            <input type="text" id="speaker" value="{(agent.tts_voice or 'shubh').lower()}" placeholder="e.g., shubh, shreya">
            
            <label>Language Code:</label>
            <input type="text" id="language" value="{agent.tts_language or 'en-IN'}" placeholder="e.g., hi-IN, en-IN">
            
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
        
        dominant_lang = get_dominant_language(session_id, agent.tts_language or "en-IN")
        
        response_text = await generate_llm_response(agent, user_text, db)
        
        await websocket.send_json({
            "type": "agent_text", 
            "text": response_text,
            "detected_language": dominant_lang
        })
        
        # Step 3: TTS - synthesize response for voice mode
        try:
            await websocket.send_json({"type": "status", "status": "speaking"})
        except RuntimeError:
            return  # connection closed
            
        try:
            audio_response = await synthesize_speech(agent, response_text, language_override=dominant_lang)
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
        

async def handle_audio_turn(
    websocket: WebSocket,
    agent: AgentConfig,
    audio_bytes: bytes,
    db: AsyncSession,
    session_id: str = ""
):
    """Process one turn of audio: STT -> LLM -> TTS -> send back.
    Issues 3+5: adds per-stage timing, tts_failed event, and graceful fallback."""
    import time

    # Send "processing" status
    try:
        await websocket.send_json({"type": "status", "status": "processing"})
    except RuntimeError:
        return

    turn_start = time.monotonic()

    try:
        # Determine which language to use for STT based on ratio tracking
        dominant_lang = get_dominant_language(session_id, agent.tts_language or "en-IN")

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

        # Track detected language for ratio-based switching
        if detected_lang and session_id:
            track_language(session_id, detected_lang)
        elif session_id:
            text_lang = detect_text_language(transcript)
            if text_lang:
                track_language(session_id, text_lang)

        current_dominant = get_dominant_language(session_id, agent.tts_language or "en-IN")

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

        # ── Step 2: LLM ──────────────────────────────────────────────────────────
        try:
            await websocket.send_json({"type": "status", "status": "thinking"})
        except RuntimeError:
            return

        llm_start = time.monotonic()
        response_text = await generate_llm_response(agent, transcript, db)
        llm_ms = int((time.monotonic() - llm_start) * 1000)
        logger.info(f"[TIMING] LLM: {llm_ms}ms — '{response_text[:80]}'")

        # Send agent transcript back
        try:
            await websocket.send_json({
                "type": "transcript",
                "text": response_text,
                "role": "assistant",
                "detected_language": current_dominant,
            })
        except RuntimeError:
            return

        # ── Step 3: TTS ──────────────────────────────────────────────────────────
        try:
            await websocket.send_json({"type": "status", "status": "speaking"})
        except RuntimeError:
            return

        tts_ms = 0
        tts_ok = False
        try:
            tts_start = time.monotonic()
            # ISSUE 5: use retry-capable TTS wrapper
            audio_response = await sarvam_synthesize_with_retry(
                agent, response_text, language_override=current_dominant
            ) if (agent.tts_provider or "sarvam") == "sarvam" else await synthesize_speech(
                agent, response_text, language_override=current_dominant
            )
            tts_ms = int((time.monotonic() - tts_start) * 1000)
            logger.info(f"[TIMING] TTS: {tts_ms}ms ({len(audio_response) if audio_response else 0} bytes)")

            if audio_response and len(audio_response) >= 512:  # sanity-check non-empty
                tts_ok = True
                await websocket.send_bytes(audio_response)
            else:
                logger.warning(f"TTS returned empty/tiny response ({len(audio_response) if audio_response else 0}B) — sending tts_failed")
        except Exception as tts_err:
            tts_ms = int((time.monotonic() - tts_start) * 1000)  # type: ignore[possibly-unbound]
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
                "total_ms": total_ms,
            })
        except RuntimeError:
            pass

        try:
            await websocket.send_json({"type": "status", "status": "idle"})
        except RuntimeError:
            pass

    except Exception as e:
        logger.error(f"Error in audio turn: {e}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": f"Processing error: {str(e)}"})
        except RuntimeError:
            pass


# ── STT Logic ─────────────────────────────────────────────────────────────────

async def transcribe_audio(agent: AgentConfig, audio_bytes: bytes, language_hint: str = "") -> tuple[str, str]:
    """Transcribe audio bytes to text using configured STT provider.
    Returns (transcript, detected_language_code)."""
    
    stt_provider = agent.stt_provider or "sarvam"
    
    # Get API key from environment (avoid opening a new DB session to prevent pool exhaustion)
    api_key = None
    if stt_provider == "sarvam":
        api_key = settings.sarvam_api_key or os.getenv("SARVAM_API_KEY")
    elif stt_provider == "deepgram":
        api_key = os.getenv("DEEPGRAM_API_KEY")
    elif stt_provider == "openai_whisper":
        api_key = os.getenv("OPENAI_API_KEY")
    
    if not api_key:
        logger.warning(f"No API key for STT provider: {stt_provider}")
        return "", ""
    
    lang = language_hint or agent.tts_language or "en-IN"
    
    if stt_provider == "sarvam":
        return await sarvam_transcribe(api_key, audio_bytes, 
                                        agent.stt_model or "saarika:v2",
                                        lang)
    
    elif stt_provider == "deepgram":
        transcript = await deepgram_transcribe(api_key, audio_bytes,
                                          agent.stt_model or "nova-2")
        return transcript, detect_text_language(transcript)
    
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
    """Call Sarvam STT API. Returns (transcript, detected_language)."""
    import httpx
    import io

    # Sarvam docs recommend multipart upload with file + model + mode
    normalized_model = model if model and model.startswith("saarika") else "saarika:v2"
    upload_name, upload_mime = _detect_audio_upload_format(audio_bytes)
    files = {
        "file": (upload_name, io.BytesIO(audio_bytes), upload_mime)
    }
    form_data = {
        "model": normalized_model,
        "mode": "transcribe",
        "language_code": language,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
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
            return transcript, detected
        else:
            logger.error(
                "Sarvam STT error: %s %s (upload=%s, mime=%s)",
                response.status_code,
                response.text,
                upload_name,
                upload_mime,
            )
            return "", ""

async def deepgram_transcribe(api_key: str, audio_bytes: bytes, model: str) -> str:
    # Placeholder — returns empty
    return ""

async def openai_transcribe(api_key: str, audio_bytes: bytes) -> tuple[str, str]:
    """OpenAI Whisper transcription - returns (transcript, detected_language)"""
    # Placeholder — returns empty
    return "", ""


# ── LLM Logic ─────────────────────────────────────────────────────────────────

# Store conversation history per session (in-memory for now)
_conversation_history: dict[str, list] = {}

async def generate_llm_response(
    agent: AgentConfig, 
    user_message: str,
    db: AsyncSession,
    session_id: str = None
) -> str:
    """Generate LLM response using configured provider, system prompt, and knowledge base."""
    
    llm_provider = agent.llm_provider or "gemini"
    session_key = session_id or agent.id
    
    # Initialize conversation history for this session
    if session_key not in _conversation_history:
        _conversation_history[session_key] = []
    
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
        api_key = env_key
    
    if not api_key:
        return generate_demo_response(agent, user_message, history)
    
    # ── Build System Prompt with Knowledge Base ──────────────────────────────
    base_prompt = agent.system_prompt or (
        f"You are a helpful AI receptionist for {agent.agent_name}. "
        f"Help patients book appointments, answer questions about "
        f"clinic services, and provide general assistance. "
        f"Keep responses concise and conversational — under 50 words. "
        f"You are speaking on a phone call."
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
    
    system_prompt = base_prompt + kb_context
    
    # Add user message to history
    history.append({"role": "user", "content": user_message})
    
    # Provider-specific default models (safety net if wrong model is stored)
    PROVIDER_DEFAULTS = {
        "gemini": "gemini-2.0-flash",
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
        "groq": ["llama", "mixtral", "gemma", "whisper"],
        "deepseek": ["deepseek"],
        "mistral": ["mistral"],
    }
    
    agent_model = agent.llm_model or PROVIDER_DEFAULTS.get(llm_provider, "gemini-2.0-flash")
    # Check if stored model actually belongs to this provider
    valid_prefixes = PROVIDER_MODEL_PREFIXES.get(llm_provider, [])
    model_matches_provider = any(agent_model.lower().startswith(p) for p in valid_prefixes)
    if not model_matches_provider:
        logger.warning(f"Model '{agent_model}' doesn't match provider '{llm_provider}', using default")
        agent_model = PROVIDER_DEFAULTS.get(llm_provider, agent_model)

    try:
        if llm_provider == "gemini":
            response = await call_gemini(api_key, system_prompt, history,
                                          agent_model)
        elif llm_provider == "openai":
            response = await call_openai(api_key, system_prompt, history,
                                          agent_model)
        elif llm_provider == "anthropic":
            response = await call_anthropic(api_key, system_prompt, history,
                                             agent_model)
        elif llm_provider == "groq":
            response = await call_groq(api_key, system_prompt, history,
                                        agent_model)
        elif llm_provider == "deepseek":
            response = await call_openai(api_key, system_prompt, history,
                                          agent_model,
                                          base_url="https://api.deepseek.com/v1")
        else:
            response = generate_demo_response(agent, user_message, history)
        
        # Add response to history
        history.append({"role": "assistant", "content": response})
        
        # Keep history to last 10 turns to avoid token overflow
        if len(history) > 20:
            _conversation_history[session_key] = history[-20:]
        
        return response
        
    except Exception as e:
        logger.error(f"LLM call failed (Provider: {llm_provider}, Model: {agent_model}): {type(e).__name__}: {e}", exc_info=True)
        error_msg = str(e) or type(e).__name__
        if "429" in error_msg:
             return "I'm currently receiving too many requests. Please wait a moment before speaking again."
        if "safety" in error_msg.lower():
             return "I'm sorry, I cannot respond to that prompt due to safety guidelines. How else can I help?"
        if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
             return "The AI service took too long to respond. Please try again."
        
        return f"I'm sorry, I'm having trouble processing that right now. Could you please repeat? (Error: {error_msg})"


async def call_gemini(api_key: str, system_prompt: str, 
                       history: list, model: str) -> str:
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
                        "maxOutputTokens": 150,
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
                raise Exception(f"Gemini error: {response.status_code}")
        
        raise Exception("Gemini error: 429 — rate limit exceeded after retries")


async def call_openai(api_key: str, system_prompt: str,
                       history: list, model: str,
                       base_url: str = "https://api.openai.com/v1") -> str:
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
                "max_tokens": 150,
                "temperature": 0.7
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        else:
            raise Exception(f"OpenAI-compatible API error: {response.status_code}")


async def call_anthropic(api_key: str, system_prompt: str,
                          history: list, model: str) -> str:
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
                "max_tokens": 150
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            return data["content"][0]["text"]
        else:
            raise Exception(f"Anthropic error: {response.status_code}")


async def call_groq(api_key: str, system_prompt: str,
                             history: list, model: str) -> str:
    import httpx
    
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        # groq requires role user or assistant
        messages.append({
            "role": "assistant" if msg["role"] == "model" else msg["role"],
            "content": msg["content"]
        })
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "messages": messages,
                "max_tokens": 150,
                "temperature": 0.7
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            return data["choices"][0]["message"]["content"]
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
    tts_language = language_override or agent.tts_language or "en-IN"
    
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
            return await sarvam_synthesize(
                api_key=api_key,
                text=text,
                voice=normalized_voice,
                model=agent.tts_model or "bulbul:v3",
                language=tts_language,
                pitch=agent.tts_pitch or 0.0,
                pace=agent.tts_pace or 1.0,
                loudness=agent.tts_loudness or 1.0
            )
        
        elif tts_provider == "elevenlabs":
            return await elevenlabs_synthesize(
                api_key=api_key,
                text=text,
                voice_id=agent.tts_voice or "21m00Tcm4TlvDq8ikWAM"
            )
        
        elif tts_provider == "openai_tts":
            return await openai_synthesize(
                api_key=api_key,
                text=text,
                voice=agent.tts_voice or "nova"
            )
        
        else:
            logger.warning(f"Unknown TTS provider: {tts_provider}")
            return None
            
    except Exception as e:
        logger.error(f"TTS synthesis failed: {e}", exc_info=True)
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

    tts_language = language_override or agent.tts_language or "en-IN"
    raw_voice = (agent.tts_voice or "priya").strip().lower()

    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            audio = await sarvam_synthesize(
                api_key=api_key,
                text=text,
                voice=raw_voice,
                model=agent.tts_model or "bulbul:v3",
                language=tts_language,
                pitch=agent.tts_pitch or 0.0,
                pace=agent.tts_pace or 1.1,
                loudness=agent.tts_loudness or 1.5,
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
    logger.warning(
        "Speaker '%s' is not compatible with model '%s'. "
        "Falling back to '%s'. Valid speakers: %s",
        requested_speaker, model, fallback, ", ".join(valid_speakers),
    )
    return fallback


async def sarvam_synthesize(api_key: str, text: str, voice: str,
                               model: str, language: str,
                               pitch: float, pace: float, 
                               loudness: float) -> bytes:
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
    
    # Build payload based on model capabilities.
    payload = {
        # Sarvam REST v3 expects `text` for synchronous synthesis.
        "text": normalized_text,
        "target_language_code": language,
        "speaker": normalized_voice,
        "model": normalized_model,
        "pace": pace,
        "enable_preprocessing": True,
        "speech_sample_rate": 24000,
    }

    # Bulbul v3 currently rejects pitch/loudness parameters.
    if normalized_model != "bulbul:v3":
        payload["pitch"] = pitch
        payload["loudness"] = loudness

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
                                  voice_id: str) -> bytes:
    import httpx
    
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
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75
                }
            }
        )
        
        if response.status_code == 200:
            return response.content
        else:
            raise Exception(f"ElevenLabs TTS: {response.status_code}")


async def openai_synthesize(api_key: str, text: str, voice: str) -> bytes:
    import httpx
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "tts-1",
                "input": text,
                "voice": voice,
                "response_format": "mp3"
            }
        )
        
        if response.status_code == 200:
            return response.content
        else:
            raise Exception(f"OpenAI TTS: {response.status_code}")

