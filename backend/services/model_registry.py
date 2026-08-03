import httpx
import asyncio
from datetime import datetime, timedelta
from backend.config import settings
import logging

logger = logging.getLogger(__name__)

# ── In-memory cache (TTL: 1 hour) ─────────────────────────────
_cache = {
    "gemini_models": {"data": None, "expires": None},
    "sarvam_voices": {"data": None, "expires": None},
}

def _is_cached(key: str) -> bool:
    entry = _cache.get(key)
    if not entry or not entry["data"] or not entry["expires"]:
        return False
    return datetime.utcnow() < entry["expires"]

def _set_cache(key: str, data, ttl_minutes: int = 60):
    _cache[key] = {
        "data": data,
        "expires": datetime.utcnow() + timedelta(minutes=ttl_minutes)
    }

# ── Gemini Models ─────────────────────────────────────────────
async def fetch_gemini_models(api_key: str) -> list:
    """
    Dynamically fetch ALL available Gemini models from Google API.
    Filters to only text generation models suitable for voice agents.
    Returns sorted list with recommended model first.
    """
    if _is_cached("gemini_models"):
        return _cache["gemini_models"]["data"]
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://generativelanguage.googleapis.com/"
                "v1beta/models",
                params={"key": api_key, "pageSize": 100}
            )
            response.raise_for_status()
            data = response.json()
        
        all_models = data.get("models", [])
        
        # Filter to only usable chat/generation models
        voice_models = []
        for model in all_models:
            name = model.get("name", "")
            methods = model.get("supportedGenerationMethods", [])
            display = model.get("displayName", "")
            
            # Only include models that can generate content
            if "generateContent" not in methods:
                continue
            
            # Only Gemini models (not image/video/embedding)
            if "gemini" not in name.lower():
                continue
            
            # Skip deprecated or retired models
            desc = model.get("description", "").lower()
            if any(x in desc for x in ["deprecated", "retired", "shutdown"]):
                continue
            
            # Extract clean model ID
            model_id = name.replace("models/", "")
            
            # Determine category and tags
            is_flash = "flash" in model_id.lower()
            is_pro = "pro" in model_id.lower()
            is_preview = "preview" in model_id.lower() or \
                         "exp" in model_id.lower()
            
            # Speed/cost tags
            tags = []
            if is_flash:
                tags.append("⚡ Fast")
                tags.append("💰 Low cost")
            if is_pro:
                tags.append("🎯 High quality")
            if is_preview:
                tags.append("🔬 Preview")
            if "lite" in model_id.lower():
                tags.append("🚀 Fastest")
                tags.append("💰 Cheapest")
            
            # Recommended for voice AI
            recommended = model_id in [
                "gemini-2.5-flash",
                "gemini-2.5-flash-lite-preview",
            ]
            
            voice_models.append({
                "id": model_id,
                "name": model.get("displayName", model_id),
                "description": model.get("description", ""),
                "input_token_limit": model.get("inputTokenLimit", 0),
                "output_token_limit": model.get("outputTokenLimit", 0),
                "tags": tags,
                "is_recommended": recommended,
                "is_preview": is_preview,
                "is_flash": is_flash,
                "is_pro": is_pro,
            })
        
        # Sort: recommended first, then flash, then pro, then others
        def sort_key(m):
            if m["is_recommended"]: return 0
            if m["is_flash"] and not m["is_preview"]: return 1
            if m["is_flash"]: return 2
            if m["is_pro"]: return 3
            return 4
        
        voice_models.sort(key=sort_key)
        
        logger.info(f"Fetched {len(voice_models)} Gemini models")
        _set_cache("gemini_models", voice_models, ttl_minutes=60)
        return voice_models
        
    except Exception as e:
        logger.error(f"Failed to fetch Gemini models: {e}")
        # Return hardcoded fallback if API fails
        return GEMINI_FALLBACK_MODELS

GEMINI_FALLBACK_MODELS = [
    {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash",
     "tags": ["⚡ Fast", "💰 Low cost"], "is_recommended": True,
     "is_preview": False},
    {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro",
     "tags": ["🎯 High quality"], "is_recommended": False,
     "is_preview": False},
    {"id": "gemini-2.0-flash-lite", "name": "Gemini 2.0 Flash Lite",
     "tags": ["🚀 Fastest", "💰 Cheapest"], "is_recommended": False,
     "is_preview": False},
]

# ── Sarvam Voices ─────────────────────────────────────────────
# Built from backend/services/sarvam_catalog.py rather than restated. The list
# that used to live here had drifted badly from the one in
# backend/routers/providers.py that the Voice Library serves: it was missing 17
# real bulbul:v3 speakers and offered "sophia", which Sarvam does not recognise.
# The agent-creation wizard reads this, the Voice Library reads that — they now
# describe the same 37 speakers because there is only one list.
from backend.services.sarvam_catalog import (
    BULBUL_V2_VOICES,
    BULBUL_V3_VOICES,
    SARVAM_TTS_LANGUAGE_CODES,
    SARVAM_TTS_LANGUAGES,
)


def _wizard_voices(catalog: list[dict], gender: str) -> list[dict]:
    """Catalogue rows -> the {id, name, style} shape the wizard's cards render."""
    out = []
    for v in catalog:
        if v["gender"] != gender:
            continue
        entry = {"id": v["id"], "name": v["name"], "style": v.get("description", "")}
        if v.get("default"):
            entry["default"] = True
        out.append(entry)
    return out


SARVAM_VOICES_DATA = {
    "bulbul:v3": {
        "model_label": "Bulbul v3 (Latest — Recommended)",
        "supports_pitch": False,
        "supports_loudness": False,
        "supports_temperature": True,
        "pace_range": [0.5, 2.0],
        "sample_rate": 24000,
        # Every speaker below renders every one of these languages.
        "languages": SARVAM_TTS_LANGUAGES,
        "language_codes": list(SARVAM_TTS_LANGUAGE_CODES),
        "male_voices": _wizard_voices(BULBUL_V3_VOICES, "male"),
        "female_voices": _wizard_voices(BULBUL_V3_VOICES, "female"),
    },
    "bulbul:v2": {
        "model_label": "Bulbul v2 (Stable — Pitch control)",
        "supports_pitch": True,
        "supports_loudness": True,
        "supports_temperature": False,
        "pace_range": [0.3, 3.0],
        "sample_rate": 22050,
        # bulbul:v2 serves the same 11 GA languages as v3 — verified: it answers
        # "<code> is only supported by bulbul:v3" for exactly the gated twelve.
        "languages": SARVAM_TTS_LANGUAGES,
        "language_codes": list(SARVAM_TTS_LANGUAGE_CODES),
        "male_voices": _wizard_voices(BULBUL_V2_VOICES, "male"),
        "female_voices": _wizard_voices(BULBUL_V2_VOICES, "female"),
    }
}

async def get_sarvam_voices(model: str = "bulbul:v3") -> dict:
    """Returns voice data for the specified Sarvam model."""
    return SARVAM_VOICES_DATA.get(model, SARVAM_VOICES_DATA["bulbul:v3"])

async def get_all_providers_summary(settings) -> dict:
    """Returns complete AI provider info for frontend dropdowns."""
    gemini_models = []
    if settings.gemini_api_key:
        gemini_models = await fetch_gemini_models(settings.gemini_api_key)
    
    return {
        "providers": {
            "stt": [
                {
                    "id": "sarvam",
                    "name": "Sarvam AI",
                    "flag": "🇮🇳",
                    "connected": bool(settings.sarvam_api_key),
                    "best_for": "Indian languages — Hindi, Tamil, Telugu, Malayalam",
                    "models": [
                        {"id": "saaras:v3", "name": "Saaras v3", "label": "State-of-the-Art — Recommended", "recommended": True},
                        {"id": "saarika:v2.5", "name": "Saarika v2.5", "label": "Best for Indian accents"},
                    ]
                },
                {
                    "id": "gemini",
                    "name": "Google Gemini",
                    "flag": "🔵",
                    "connected": bool(settings.gemini_api_key),
                    "best_for": "Multilingual — same key as LLM",
                    "models": [
                        {"id": "gemini-2.5-flash", "name": "Gemini Flash STT", "recommended": True}
                    ]
                },
                {
                    "id": "elevenlabs",
                    "name": "ElevenLabs",
                    "flag": "✨",
                    "connected": bool(settings.elevenlabs_api_key),
                    "best_for": "Multilingual & Low Latency — Scribe v2",
                    "models": [
                        {"id": "scribe_v2_realtime", "name": "Scribe v2 Realtime", "label": "Streaming — Recommended", "recommended": True},
                        {"id": "scribe_v2", "name": "Scribe v2 Batch", "label": "Batch processing"},
                    ]
                }
            ],
            "llm": [
                {
                    "id": "gemini",
                    "name": "Google Gemini",
                    "flag": "🔵",
                    "connected": bool(settings.gemini_api_key),
                    "models": gemini_models,  # DYNAMIC from API
                    "best_for": "Fast, multilingual, free tier"
                }
            ],
            "tts": [
                {
                    "id": "sarvam",
                    "name": "Sarvam AI",
                    "flag": "🇮🇳",
                    "connected": bool(settings.sarvam_api_key),
                    "best_for": "Indian voices — 35+ speakers",
                    "models": [
                        {
                            "id": "bulbul:v3",
                            "name": "Bulbul v3",
                            "label": "Latest — 35+ voices",
                            "recommended": True,
                            "voices": SARVAM_VOICES_DATA["bulbul:v3"]
                        },
                        {
                            "id": "bulbul:v2",
                            "name": "Bulbul v2",
                            "label": "Stable — pitch control",
                            "voices": SARVAM_VOICES_DATA["bulbul:v2"]
                        }
                    ]
                },
                {
                    "id": "elevenlabs",
                    "name": "ElevenLabs",
                    "flag": "✨",
                    "connected": bool(settings.elevenlabs_api_key),
                    "best_for": "Premium voices — High naturalness",
                    "models": [
                        {
                            "id": "eleven_flash_v2_5",
                            "name": "Eleven Flash v2.5",
                            "label": "Ultra-low Latency (~75ms) — Recommended",
                            "recommended": True
                        },
                        {
                            "id": "eleven_multilingual_v2",
                            "name": "Eleven Multilingual v2",
                            "label": "Expressive / Stable Quality"
                        }
                    ]
                }
            ]
        }
    }
