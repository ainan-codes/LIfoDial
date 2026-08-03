"""
backend/routers/providers.py — Provider Discovery API.
Equivalent to OmniDim's client.providers.list_llms() / list_voices().

GET /providers             → all providers + live status
GET /providers/voices      → all Sarvam voices grouped by model/gender
GET /providers/llms        → Gemini model list (live + cached)
POST /providers/test-connection → test an API key
"""
import logging
import time
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.auth import CurrentUser
from backend.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Sarvam voices catalogue ────────────────────────────────────────────────────
# Moved to backend/services/sarvam_catalog.py so the Voice Library page, the
# agent-creation wizard and this router cannot drift apart again (they had:
# three lists, two of them containing speakers Sarvam rejects). Re-exported here
# because several modules already import SARVAM_VOICES from this router.
from backend.services.sarvam_catalog import (  # noqa: E402
    SARVAM_TTS_LANGUAGE_CODES,
    SARVAM_TTS_LANGUAGES,
    SARVAM_VOICES,
    voices_for_model,
)

GEMINI_MODELS = [
    {"id": "gemini-2.5-flash",               "name": "Gemini 2.5 Flash",            "tier": "fast",    "context": 1048576, "recommended": True},
    {"id": "gemini-2.5-pro",                 "name": "Gemini 2.5 Pro",              "tier": "pro",     "context": 2097152, "recommended": False},
    {"id": "gemini-2.0-flash-lite",          "name": "Gemini 2.0 Flash Lite",       "tier": "fastest", "context": 1048576, "recommended": False},
    {"id": "gemini-1.5-flash",               "name": "Gemini 1.5 Flash",            "tier": "fast",    "context": 1048576, "recommended": False},
    {"id": "gemini-1.5-pro",                 "name": "Gemini 1.5 Pro",              "tier": "pro",     "context": 2097152, "recommended": False},
]

STT_MODELS = [
    {"id": "saarika:v2.5",  "name": "Saarika v2.5",  "provider": "sarvam", "languages": ["hi-IN","en-IN","ta-IN","te-IN","bn-IN","mr-IN","gu-IN","kn-IN","ml-IN","pa-IN","or-IN"], "recommended": False},
    {"id": "saaras:v3",   "name": "Saaras v3",   "provider": "sarvam", "languages": ["hi-IN","en-IN","ta-IN","te-IN","bn-IN","mr-IN","gu-IN","kn-IN","ml-IN","pa-IN","or-IN","ar-SA"], "recommended": True},
]


# ── GET /providers ─────────────────────────────────────────────────────────────

@router.get("/providers")
async def get_providers(user: CurrentUser = None) -> dict:
    """Returns all configured providers with live connection status."""
    sarvam_ok = bool(settings.sarvam_api_key)
    gemini_ok = bool(settings.gemini_api_key)
    elevenlabs_ok = bool(settings.elevenlabs_api_key)
    openai_ok = bool(settings.openai_api_key)

    return {
        "providers": [
            {
                "id": "sarvam",
                "name": "Sarvam AI",
                "type": ["stt", "tts"],
                "connected": sarvam_ok,
                "voice_count": len(SARVAM_VOICES),
                "stt_models": [m["id"] for m in STT_MODELS],
                "description": "Best for Indian languages. 22+ languages. Low latency.",
                "website": "https://sarvam.ai",
            },
            {
                "id": "gemini",
                "name": "Google Gemini",
                "type": ["llm"],
                "connected": gemini_ok,
                "model_count": len(GEMINI_MODELS),
                "description": "Default LLM. Gemini 2.5 Flash is recommended for voice.",
                "website": "https://ai.google.dev",
            },
            {
                "id": "elevenlabs",
                "name": "ElevenLabs",
                "type": ["stt", "tts"],
                "connected": elevenlabs_ok,
                "voice_count": 0 if not elevenlabs_ok else "sync required",
                "stt_models": ["scribe_v2_realtime", "scribe_v2"],
                "description": "Premium TTS & Scribe v2 STT. High naturalness.",
                "website": "https://elevenlabs.io",
            },
            {
                "id": "openai",
                "name": "OpenAI",
                "type": ["llm", "stt"],
                "connected": openai_ok,
                "description": "GPT-4o. Good for English-primary agents.",
                "website": "https://openai.com",
            },
        ],
        "summary": {
            "stt_ready": sarvam_ok or elevenlabs_ok,
            "tts_ready": sarvam_ok or elevenlabs_ok,
            "llm_ready": gemini_ok or openai_ok,
            "can_run_calls": (sarvam_ok or elevenlabs_ok) and gemini_ok,
        },
    }


# ── GET /providers/voices ──────────────────────────────────────────────────────

@router.get("/providers/voices")
async def list_all_voices(language: str | None = None, model: str | None = None, gender: str | None = None, user: CurrentUser = None) -> dict:
    """Returns all Sarvam voices with optional filtering."""
    voices = voices_for_model(model)
    # No Sarvam speaker is restricted to one language — verified against the API:
    # every bulbul:v3 speaker renders all 11 GA languages. So a language filter
    # selects voices that CAN speak it, which for a supported language is all of
    # them. Matching on the per-voice display tag instead is exactly why
    # Malayalam looked like it had no voices at all.
    if language and language not in SARVAM_TTS_LANGUAGE_CODES:
        voices = []
    if gender:
        voices = [v for v in voices if v["gender"] == gender]

    # Group by model
    by_model: dict[str, list] = {}
    for v in voices:
        by_model.setdefault(v["model"], []).append(v)

    return {
        "total": len(voices),
        "voices": voices,
        "by_model": by_model,
        "models_available": ["bulbul:v3", "bulbul:v2"],
        # Every voice speaks every one of these, so this is the whole GA list,
        # not the distinct set of per-voice display tags (which was missing
        # Malayalam, Gujarati, Punjabi and Odia entirely).
        "languages_available": list(SARVAM_TTS_LANGUAGE_CODES),
        "languages": SARVAM_TTS_LANGUAGES,
    }


# ── GET /providers/llms ────────────────────────────────────────────────────────

@router.get("/providers/llms")
async def list_llms(user: CurrentUser = None) -> dict:
    """Returns available LLM models. Gemini models are from our catalogue."""
    return {
        "gemini": {
            "connected": bool(settings.gemini_api_key),
            "models": GEMINI_MODELS,
        },
        "openai": {
            "connected": bool(settings.openai_api_key),
            "models": [
                {"id": "gpt-4o",       "name": "GPT-4o",           "tier": "pro",     "recommended": True},
                {"id": "gpt-4o-mini",  "name": "GPT-4o Mini",      "tier": "fast",    "recommended": False},
                {"id": "gpt-4-turbo",  "name": "GPT-4 Turbo",      "tier": "pro",     "recommended": False},
            ] if settings.openai_api_key else [],
        },
        "recommended_for_voice": "gemini-2.5-flash",
    }


# ── GET /providers/voices/{voice_id}/preview ───────────────────────────────────

@router.get("/providers/voices/{voice_id}/preview")
async def preview_voice_by_id(voice_id: str, language: str = "hi-IN", model: str = "bulbul:v3", user: CurrentUser = None) -> dict:
    """Generate audio preview for a specific Sarvam voice_id."""
    if not settings.sarvam_api_key:
        raise HTTPException(status_code=400, detail="Sarvam API key not configured")

    # Sample text per language
    samples = {
        "hi-IN": "Namaste! Main aapki kaise madad kar sakti hoon?",
        "en-IN": "Hello! How can I assist you today?",
        "ta-IN": "Vanakkam! Naan ungalukkku eppadi udavi seyya mudiyum?",
        "te-IN": "Namaskaram! Meeru ela sahayam kavaalantunnaru?",
        "ml-IN": "Namaskaram! Njan ningale enthu sahayikkam?",
        "kn-IN": "Namaskara! Naanu nimage hege sahaya maadali?",
        "mr-IN": "Namaskar! Mi tumchi kashi madat karu shakto?",
        "bn-IN": "Nomoskar! Ami apnake kivabe sahajjo korte pari?",
        "gu-IN": "Namaste! Hu tamari kevi rite madad kari shaku?",
        "pa-IN": "Sat sri akal! Main tuhadi kiven madad kar sakda haan?",
        "od-IN": "Namaskar! Mu apananku kipari sahajya kari paribi?",
        "ar-SA": "مرحباً! كيف يمكنني مساعدتك اليوم؟",
    }
    sample_text = samples.get(language, samples["en-IN"])

    try:
        import base64
        async with httpx.AsyncClient(timeout=20.0) as client:
            payload: dict = {
                "inputs": [sample_text],
                "target_language_code": language,
                "speaker": voice_id,
                "model": model,
                "speech_sample_rate": 22050,
                "enable_preprocessing": True,
                "pace": 1.0,
            }
            if "v3" in model:
                payload["temperature"] = 0.6
            else:
                payload["pitch"] = 0.0
                payload["loudness"] = 1.5

            resp = await client.post(
                "https://api.sarvam.ai/text-to-speech",
                headers={
                    "api-subscription-key": settings.sarvam_api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            audios = data.get("audios", [])
            if not audios:
                raise HTTPException(status_code=500, detail="Sarvam returned empty audio")
            return {
                "voice_id": voice_id,
                "language": language,
                "model": model,
                "audio_base64": f"data:audio/wav;base64,{audios[0]}",
                "text": sample_text,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Voice preview error for {voice_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /providers/test-connection ───────────────────────────────────────────

class TestConnectionRequest(BaseModel):
    provider: str
    api_key: str


@router.post("/providers/test-connection")
async def test_connection(req: TestConnectionRequest, user: CurrentUser = None) -> dict:
    """Test if an API key is valid for the given provider."""
    t0 = time.time()

    if req.provider == "gemini":
        try:
            from google import genai
            client = genai.Client(api_key=req.api_key)
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents="Say: OK",
            )
            latency_ms = int((time.time() - t0) * 1000)
            return {
                "provider": "gemini",
                "connected": True,
                "latency_ms": latency_ms,
                "models_count": len(GEMINI_MODELS),
                "test_response": resp.text[:30] if resp.text else "OK",
            }
        except Exception as e:
            return {"provider": "gemini", "connected": False, "error": str(e)[:100]}

    elif req.provider == "sarvam":
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Use 'priya' — a confirmed valid bulbul:v3 speaker
                resp = await client.post(
                    "https://api.sarvam.ai/text-to-speech",
                    headers={"api-subscription-key": req.api_key, "Content-Type": "application/json"},
                    json={
                        "text": "test",
                        "target_language_code": "hi-IN",
                        "speaker": "priya",
                        "model": "bulbul:v3",
                        "speech_sample_rate": 16000,
                        "enable_preprocessing": False,
                        "pace": 1.0,
                    },
                )
                latency_ms = int((time.time() - t0) * 1000)
                if resp.status_code == 200:
                    return {
                        "provider": "sarvam",
                        "connected": True,
                        "latency_ms": latency_ms,
                        "voices_count": len(SARVAM_VOICES),
                    }
                else:
                    return {"provider": "sarvam", "connected": False, "error": f"HTTP {resp.status_code}: {resp.text[:100]}"}
        except Exception as e:
            return {"provider": "sarvam", "connected": False, "error": str(e)[:100]}

    elif req.provider == "elevenlabs":
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.elevenlabs.io/v1/voices",
                    headers={"xi-api-key": req.api_key},
                )
                latency_ms = int((time.time() - t0) * 1000)
                if resp.status_code == 200:
                    data = resp.json()
                    voices = data.get("voices", [])
                    return {
                        "provider": "elevenlabs",
                        "connected": True,
                        "latency_ms": latency_ms,
                        "voices_count": len(voices),
                    }
                else:
                    return {"provider": "elevenlabs", "connected": False, "error": f"HTTP {resp.status_code}: {resp.text[:100]}"}
        except Exception as e:
            return {"provider": "elevenlabs", "connected": False, "error": str(e)[:100]}

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {req.provider}")
