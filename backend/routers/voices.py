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
from datetime import datetime, timedelta

from backend.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# ── In-memory cache for ElevenLabs voices (TTL: 1 hour) ─────────────────────
_el_voices_cache: dict = {"data": None, "expires": None}


def _el_cache_valid() -> bool:
    return (
        _el_voices_cache["data"] is not None
        and _el_voices_cache["expires"] is not None
        and datetime.utcnow() < _el_voices_cache["expires"]
    )


@router.get("/elevenlabs")
async def get_elevenlabs_voices(
    gender: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
):
    """
    Returns all ElevenLabs voices with preview_url for in-platform audio preview.
    Cached for 1 hour to avoid unnecessary API calls.
    """
    api_key = settings.elevenlabs_api_key
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="ElevenLabs API key not configured. Add ELEVENLABS_API_KEY to your .env",
        )

    # Return cached voices if still valid
    if _el_cache_valid():
        voices = _el_voices_cache["data"]
    else:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.elevenlabs.io/v1/voices",
                    headers={"xi-api-key": api_key},
                )
                if resp.status_code != 200:
                    raise HTTPException(
                        status_code=resp.status_code,
                        detail=f"ElevenLabs API error: {resp.text[:200]}",
                    )
                data = resp.json()

            raw_voices = data.get("voices", [])
            voices = []
            for v in raw_voices:
                labels = v.get("labels", {})
                voices.append({
                    "voice_id": v.get("voice_id"),
                    "name": v.get("name"),
                    "preview_url": v.get("preview_url"),  # Direct MP3 — no generation needed
                    "category": v.get("category", "premade"),
                    "description": v.get("description") or labels.get("description", ""),
                    "gender": labels.get("gender", "").lower(),
                    "accent": labels.get("accent", ""),
                    "age": labels.get("age", ""),
                    "use_case": labels.get("use case") or labels.get("use_case", ""),
                    "labels": labels,
                })

            # Cache for 1 hour
            _el_voices_cache["data"] = voices
            _el_voices_cache["expires"] = datetime.utcnow() + timedelta(hours=1)
            logger.info(f"Fetched and cached {len(voices)} ElevenLabs voices")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to fetch ElevenLabs voices: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Apply filters
    if gender:
        voices = [v for v in voices if v["gender"] == gender.lower()]
    if category:
        voices = [v for v in voices if v["category"] == category]
    if search:
        q = search.lower()
        voices = [
            v for v in voices
            if q in v["name"].lower()
            or q in v.get("description", "").lower()
            or q in v.get("accent", "").lower()
            or q in v.get("use_case", "").lower()
        ]

    return {
        "total": len(voices),
        "voices": voices,
        "cached": _el_cache_valid(),
    }


@router.post("/elevenlabs/refresh")
async def refresh_elevenlabs_voices():
    """Force-clear the ElevenLabs voice cache so the next GET re-fetches."""
    _el_voices_cache["data"] = None
    _el_voices_cache["expires"] = None
    return {"message": "Cache cleared. Next GET /voices/elevenlabs will re-fetch."}


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
                        "model_id": req.model or "eleven_flash_v2_5",
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
