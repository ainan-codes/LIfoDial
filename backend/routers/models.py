from fastapi import APIRouter, HTTPException
from backend.auth import CurrentUser
from backend.config import settings
from backend.services.model_registry import (
    get_all_providers_summary,
    fetch_gemini_models,
    _set_cache,
)

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

