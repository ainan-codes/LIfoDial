from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.auth import CurrentUser
from backend.config import settings
from backend.services.model_registry import (
    get_all_providers_summary,
    fetch_gemini_models,
    _set_cache,
    SARVAM_VOICES_DATA
)
import httpx
import base64
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["models"])

@router.get("/models/providers")
async def get_providers(user: CurrentUser = None):
    """Returns complete AI provider info for frontend dropdowns."""
    return await get_all_providers_summary(settings)

@router.get("/models/gemini/refresh")
async def refresh_gemini_models(user: CurrentUser = None):
    """Forces cache refresh for Gemini models."""
    if not settings.gemini_api_key:
        raise HTTPException(status_code=400, detail="Gemini API Key not set.")
    # Clear cache and refetch
    _set_cache("gemini_models", None, ttl_minutes=-1) 
    models = await fetch_gemini_models(settings.gemini_api_key)
    return {"status": "ok", "count": len(models), "models": models}

class PreviewRequest(BaseModel):
    provider: str
    model: str
    voice_id: str
    language: str
    text: str

@router.post("/voices/preview")
async def voice_preview(req: PreviewRequest, user: CurrentUser = None):
    """Calls provider TTS -> returns base64 audio."""
    if req.provider == "sarvam":
        if not settings.sarvam_api_key:
            raise HTTPException(status_code=400, detail="Sarvam API Key not set.")
            
        payload = {
            "inputs": [req.text[:500]],
            "target_language_code": req.language,
            "speaker": req.voice_id,
            "model": req.model,
            "speech_sample_rate": 16000,
            "enable_preprocessing": True,
            "pace": 1.0,
        }
        
        if "v3" in req.model:
            payload["temperature"] = 0.6
        else:
            payload["pitch"] = 0.0
            payload["loudness"] = 1.5
            
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://api.sarvam.ai/text-to-speech",
                    headers={
                        "api-subscription-key": settings.sarvam_api_key,
                        "Content-Type": "application/json"
                    },
                    json=payload
                )
                
                response.raise_for_status()
                data = response.json()
                audios = data.get("audios", [])
                
                if not audios:
                    raise HTTPException(status_code=500, detail="No audio returned from Sarvam.")
                    
                return {"audio_base64": audios[0]}
                
        except Exception as e:
            logger.error(f"Voice preview failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))
            
    raise HTTPException(status_code=400, detail="Unsupported provider.")
