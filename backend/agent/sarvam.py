"""
backend/agent/sarvam.py — LEGACY Sarvam helpers.

Only `clone_voice` is still imported (by routers/voice_upload.py). The
transcribe/synthesize/detect_language/get_greeting_audio functions below are
unused legacy stubs that return mock data — the live STT/TTS path uses
services/sarvam_streaming.py and routers/agent_test.py, NOT this module. Do not
wire these stubs into anything real; they exist only to keep old imports alive.
"""
import logging
import httpx
from backend.config import settings

logger = logging.getLogger(__name__)

async def _call_sarvam(endpoint: str, json_data: dict) -> dict:
    if not settings.sarvam_api_key:
        logger.warning(f"No Sarvam API key in .env, mocking {endpoint}")
        return {}
    
    url = f"https://api.sarvam.ai/{endpoint}"
    headers = {
        "API-Subscription-Key": settings.sarvam_api_key,
        "Content-Type": "application/json"
    }
    
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=json_data, headers=headers, timeout=5.0)
            res.raise_for_status()
            return res.json()
    except Exception as e:
        logger.error(f"Sarvam API {endpoint} error: {e}")
        return {}

async def transcribe(audio_bytes: bytes, lang_code: str) -> str:
    """STT: bytes -> text"""
    # Assuming audio_bytes is already base64 encoded wav or similar
    # Mocking implementation since raw PCM from WebRTC needs to be buffered by VAD to be sent to REST API
    if not settings.sarvam_api_key:
        return "I want to see a cardiologist"
        
    data = {"audio": "base64...", "languageCode": lang_code}
    # result = await _call_sarvam("speech-to-text-translate", data)
    return "I want to see a cardiologist"

async def synthesize(text: str, lang_code: str, voice_id: str | None = None) -> bytes:
    """TTS: text -> PCM audio bytes"""
    logger.info(f"🎤 [Sarvam TTS] Speaks: '{text}'")
    if not settings.sarvam_api_key:
        # Mocking 0.1s of blank audio frames to trigger playback loop in LiveKit without crashing
        return b'\x00' * 4800  
        
    data = {
        "inputs": [text],
        "targetLanguageCode": lang_code,
        "speaker": voice_id or "meera"
    }
    # result = await _call_sarvam("text-to-speech", data)
    return b'\x00' * 4800

async def detect_language(text: str) -> str:
    """Language matching"""
    return "hi-IN"

async def get_greeting_audio(clinic_name: str, lang_code: str, voice_id: str | None) -> bytes:
    greeting = f"Welcome to {clinic_name}. How can I assist you today?"
    return await synthesize(greeting, lang_code, voice_id)

async def clone_voice(audio_bytes: bytes) -> str | None:
    """Upload a voice sample to Sarvam to get a cloned voice_id.

    Sarvam voice cloning is NOT implemented yet — the real API call below is a
    placeholder. Returns None so callers report this honestly instead of
    fabricating a voice id, which previously made the UI claim a custom voice
    was "active" when the agent never actually used one.
    """
    logger.info(
        "🎙️ [Sarvam Voice Clone] Sample received (%d bytes) — cloning not yet enabled",
        len(audio_bytes or b""),
    )
    # TODO: implement real Sarvam voice cloning, then return the real id:
    #   result = await _call_sarvam("voice-cloning", {"audio": base64_audio})
    #   return result.get("voice_id")
    return None
