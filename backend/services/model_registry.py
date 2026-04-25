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
                "gemini-2.0-flash",
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
    {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash",
     "tags": ["⚡ Fast", "💰 Low cost"], "is_recommended": False,
     "is_preview": False},
    {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro",
     "tags": ["🎯 High quality"], "is_recommended": False,
     "is_preview": False},
]

# ── Sarvam Voices ─────────────────────────────────────────────
SARVAM_VOICES_DATA = {
    "bulbul:v3": {
        "model_label": "Bulbul v3 (Latest — Recommended)",
        "supports_pitch": False,
        "supports_loudness": False,
        "supports_temperature": True,
        "pace_range": [0.5, 2.0],
        "sample_rate": 24000,
        "male_voices": [
            {"id": "shubh", "name": "Shubh", "default": True, "style": "Professional, clear"},
            {"id": "aditya", "name": "Aditya", "style": "Warm, conversational"},
            {"id": "rahul", "name": "Rahul", "style": "Friendly, neutral"},
            {"id": "rohan", "name": "Rohan", "style": "Energetic, young"},
            {"id": "amit", "name": "Amit", "style": "Professional"},
            {"id": "dev", "name": "Dev", "style": "Deep, authoritative"},
            {"id": "kabir", "name": "Kabir", "style": "Warm, mature"},
            {"id": "anand", "name": "Anand", "style": "Neutral, clear"},
            {"id": "manan", "name": "Manan", "style": "Casual, friendly"},
            {"id": "sumit", "name": "Sumit", "style": "Professional"},
        ],
        "female_voices": [
            {"id": "ritu", "name": "Ritu", "style": "Warm, professional"},
            {"id": "priya", "name": "Priya", "style": "Friendly, clear"},
            {"id": "neha", "name": "Neha", "style": "Energetic, young"},
            {"id": "pooja", "name": "Pooja", "style": "Soft, caring"},
            {"id": "simran", "name": "Simran", "style": "Professional, neutral"},
            {"id": "kavya", "name": "Kavya", "style": "Warm, expressive"},
            {"id": "ishita", "name": "Ishita", "style": "Clear, professional"},
            {"id": "shreya", "name": "Shreya", "style": "Pleasant, friendly"},
            {"id": "roopa", "name": "Roopa", "style": "Mature, warm"},
            {"id": "tanya", "name": "Tanya", "style": "Modern, crisp"},
            {"id": "sophia", "name": "Sophia", "style": "International, clear"},
        ]
    },
    "bulbul:v3": {
        "model_label": "Bulbul v2 (Stable — Pitch control)",
        "supports_pitch": True,
        "supports_loudness": True,
        "supports_temperature": False,
        "pace_range": [0.3, 3.0],
        "sample_rate": 22050,
        "male_voices": [
            {"id": "abhilash", "name": "Abhilash", "style": "Professional"},
            {"id": "karun", "name": "Karun", "style": "Warm, deep"},
            {"id": "hitesh", "name": "Hitesh", "style": "Energetic"},
        ],
        "female_voices": [
            {"id": "anushka", "name": "Anushka", "default": True, "style": "Warm, natural"},
            {"id": "manisha", "name": "Manisha", "style": "Professional"},
            {"id": "vidya", "name": "Vidya", "style": "Clear, authoritative"},
            {"id": "arya", "name": "Arya", "style": "Youthful, friendly"},
        ]
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
                        {"id": "saarika:v2", "name": "Saarika v2", "label": "Best for Indian accents", "recommended": True},
                        {"id": "saarika:v1", "name": "Saarika v1", "label": "Stable"},
                    ]
                },
                {
                    "id": "gemini",
                    "name": "Google Gemini",
                    "flag": "🔵",
                    "connected": bool(settings.gemini_api_key),
                    "best_for": "Multilingual — same key as LLM",
                    "models": [
                        {"id": "gemini-2.0-flash", "name": "Gemini Flash STT", "recommended": True}
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
                            "id": "bulbul:v3",
                            "name": "Bulbul v2",
                            "label": "Stable — pitch control",
                            "voices": SARVAM_VOICES_DATA["bulbul:v3"]
                        }
                    ]
                }
            ]
        }
    }
