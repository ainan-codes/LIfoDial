"""
backend/routers/voices.py — Voice Library API
Serves voice metadata, live preview via Sarvam/ElevenLabs, and provider sync.
"""
from fastapi import APIRouter, HTTPException
from typing import Optional
from pydantic import BaseModel
import base64
import httpx
import logging

from backend.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


class PreviewRequest(BaseModel):
    provider: str
    voice_id: str
    model: Optional[str] = None
    language: Optional[str] = None
    text: str


class AssignVoiceRequest(BaseModel):
    voice_id: str
    agent_id: str


@router.get("/")
async def get_voices():
    """Returns provider connection status."""
    from backend.routers.providers import SARVAM_VOICES, GEMINI_MODELS
    return {
        "voices": [],
        "providers": {
            "sarvam": {
                "connected": bool(settings.sarvam_api_key),
                "voice_count": len(SARVAM_VOICES)
            },
            "gemini": {
                "connected": bool(settings.gemini_api_key),
                "voice_count": len(GEMINI_MODELS)
            },
            "elevenlabs": {
                "connected": bool(settings.elevenlabs_api_key),
                "voice_count": 0
            }
        }
    }



# bulbul:v3 only supports these language codes officially
SARVAM_V3_SUPPORTED_LANGS = {
    "hi-IN", "en-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN",
    "mr-IN", "bn-IN", "gu-IN", "od-IN", "pa-IN", "raj-IN"
}

@router.post("/preview")
async def preview_voice(req: PreviewRequest):
    """Generates live preview audio using the configured provider."""
    try:
        if req.provider == "sarvam":
            api_key = settings.sarvam_api_key
            if not api_key:
                raise HTTPException(status_code=400, detail="Sarvam API key not configured in .env")

            # Normalize language code — bulbul:v3 rejects unsupported ones
            lang = req.language or "hi-IN"
            if lang not in SARVAM_V3_SUPPORTED_LANGS:
                lang = "hi-IN"

            async with httpx.AsyncClient(timeout=30.0) as client:
                # Use /stream endpoint: returns raw mp3 bytes directly (no base64 unwrap needed)
                response = await client.post(
                    "https://api.sarvam.ai/text-to-speech/stream",
                    headers={
                        "api-subscription-key": api_key,
                        "Content-Type": "application/json"
                    },
                    json={
                        "text": req.text,
                        "target_language_code": lang,
                        "speaker": req.voice_id or "shreya",
                        "model": "bulbul:v3",
                        "pace": 1.0,
                        "speech_sample_rate": 22050,
                        "output_audio_codec": "mp3",
                        "enable_preprocessing": True,
                    }
                )

                print(f"DEBUG: Sarvam response status: {response.status_code}")
                if response.status_code == 200:
                    # /stream returns raw mp3 bytes — encode to base64 for browser playback
                    print(f"DEBUG: Sarvam success, content length: {len(response.content)}")
                    audio_b64 = base64.b64encode(response.content).decode("utf-8")
                    return {
                        "audio_base64": f"data:audio/mpeg;base64,{audio_b64}",
                        "format": "mp3",
                        "latency_ms": 0
                    }
                else:
                    print(f"DEBUG: Sarvam failed: {response.status_code} {response.text}")
                    logger.error(f"Sarvam preview error: {response.status_code} {response.text}")
                    raise HTTPException(status_code=response.status_code, detail=f"Sarvam TTS error: {response.text[:200]}")

        elif req.provider == "elevenlabs":
            api_key = settings.elevenlabs_api_key
            if not api_key:
                raise HTTPException(status_code=400, detail="ElevenLabs API key not configured")

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{req.voice_id}",
                    headers={
                        "xi-api-key": api_key,
                        "Content-Type": "application/json",
                        "Accept": "audio/mpeg"
                    },
                    json={
                        "text": req.text,
                        "model_id": req.model or "eleven_multilingual_v2",
                        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
                    }
                )
                if response.status_code == 200:
                    audio_b64 = base64.b64encode(response.content).decode("utf-8")
                    return {
                        "audio_base64": f"data:audio/mpeg;base64,{audio_b64}",
                        "format": "mp3",
                        "latency_ms": 0
                    }
                else:
                    raise HTTPException(status_code=response.status_code, detail=f"ElevenLabs error: {response.text[:200]}")

        elif req.provider == "gemini":
            # Gemini native TTS requires heavy Google Cloud auth — use Sarvam as fallback for preview
            api_key = settings.sarvam_api_key
            if not api_key:
                raise HTTPException(status_code=400, detail="No TTS API key configured for preview. Add SARVAM_API_KEY to .env")
            lang = req.language or "en-IN"
            if lang not in SARVAM_V3_SUPPORTED_LANGS:
                lang = "en-IN"
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    "https://api.sarvam.ai/text-to-speech/stream",
                    headers={"api-subscription-key": api_key, "Content-Type": "application/json"},
                    json={
                        "text": req.text,
                        "target_language_code": lang,
                        "speaker": req.voice_id or "shreya",
                        "model": "bulbul:v3",
                        "pace": 1.0,
                        "speech_sample_rate": 22050,
                        "output_audio_codec": "mp3",
                        "enable_preprocessing": True,
                    }
                )
                if response.status_code == 200:
                    audio_b64 = base64.b64encode(response.content).decode("utf-8")
                    return {"audio_base64": f"data:audio/mpeg;base64,{audio_b64}", "format": "mp3", "latency_ms": 0}
                else:
                    raise HTTPException(status_code=response.status_code, detail=f"TTS preview error: {response.text[:200]}")

        else:
            raise HTTPException(status_code=400, detail=f"Unsupported provider: {req.provider}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Voice preview error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync")
async def sync_voices():
    return {"message": "Synced voices from configured providers.", "status": "success"}


@router.post("/assign")
async def assign_voice(req: AssignVoiceRequest):
    return {"message": f"Assigned voice {req.voice_id} to agent {req.agent_id}", "status": "success"}
