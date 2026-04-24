"""
backend/routers/platform.py
AI Platform configuration: API key management + provider selection.
Includes: env-sync, model fetching, TTS preview, voice listing.
"""
import json
import logging
import uuid
import base64
import os
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query, File, UploadFile
from fastapi.responses import StreamingResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
import httpx

from backend.db import AsyncSessionLocal
from backend.models.api_key_config import ApiKeyConfig

logger = logging.getLogger(__name__)
router = APIRouter()

# ── DB Dep ────────────────────────────────────────────────────────────────────
async def get_db():
    async with AsyncSessionLocal() as s:
        yield s

# ── Provider catalogue ────────────────────────────────────────────────────────
PROVIDERS = {
    "llm": [
        {"id": "gemini",    "name": "Google Gemini",    "env_var": "GEMINI_API_KEY",    "models": ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.0-pro"], "key_label": "GEMINI_API_KEY",    "key_url": "https://aistudio.google.com/app/apikey",   "icon": "G"},
        {"id": "openai",    "name": "OpenAI",           "env_var": "OPENAI_API_KEY",    "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],                 "key_label": "OPENAI_API_KEY",    "key_url": "https://platform.openai.com/api-keys",     "icon": "O"},
        {"id": "anthropic", "name": "Anthropic Claude", "env_var": "ANTHROPIC_API_KEY", "models": ["claude-3-5-sonnet-20241022", "claude-3-haiku-20240307"],"key_label": "ANTHROPIC_API_KEY", "key_url": "https://console.anthropic.com/settings/keys","icon": "A"},
        {"id": "deepseek",  "name": "DeepSeek",         "env_var": "DEEPSEEK_API_KEY",  "models": ["deepseek-chat", "deepseek-reasoner"],                   "key_label": "DEEPSEEK_API_KEY",  "key_url": "https://platform.deepseek.com",            "icon": "DS"},
        {"id": "groq",      "name": "Groq",             "env_var": "GROQ_API_KEY",      "models": ["llama-3.3-70b-versatile", "mixtral-8x7b-32768"],         "key_label": "GROQ_API_KEY",      "key_url": "https://console.groq.com/keys",            "icon": "Gq"},
        {"id": "mistral",   "name": "Mistral AI",       "env_var": "MISTRAL_API_KEY",   "models": ["mistral-large-latest", "mistral-small-latest"],          "key_label": "MISTRAL_API_KEY",   "key_url": "https://console.mistral.ai",               "icon": "M"},
        {"id": "ollama",    "name": "Ollama (Local)",   "env_var": "",                  "models": [],                                                        "key_label": "Base URL (no key)", "key_url": "https://ollama.com",                       "icon": "Ol"},
    ],
    "stt": [
        {"id": "sarvam",     "name": "Sarvam AI",      "env_var": "SARVAM_API_KEY",    "models": ["saaras:v3", "saarika:v2"],      "key_label": "SARVAM_API_KEY",    "key_url": "https://dashboard.sarvam.ai",         "icon": "S"},
        {"id": "deepgram",   "name": "Deepgram",       "env_var": "DEEPGRAM_API_KEY",  "models": ["nova-2", "nova-2-medical"],      "key_label": "DEEPGRAM_API_KEY",  "key_url": "https://console.deepgram.com",        "icon": "D"},
        {"id": "whisper",    "name": "OpenAI Whisper", "env_var": "OPENAI_API_KEY",    "models": ["whisper-1"],                     "key_label": "OPENAI_API_KEY",    "key_url": "https://platform.openai.com/api-keys","icon": "W"},
        {"id": "assemblyai", "name": "AssemblyAI",     "env_var": "ASSEMBLYAI_API_KEY","models": ["best", "nano"],                  "key_label": "ASSEMBLYAI_API_KEY","key_url": "https://www.assemblyai.com",           "icon": "As"},
    ],
    "tts": [
        {"id": "sarvam",     "name": "Sarvam AI",   "env_var": "SARVAM_API_KEY",     "models": ["bulbul:v3", "bulbul:v2"],                      "key_label": "SARVAM_API_KEY",     "key_url": "https://dashboard.sarvam.ai",        "icon": "S"},
        {"id": "elevenlabs", "name": "ElevenLabs",  "env_var": "ELEVENLABS_API_KEY", "models": ["eleven_turbo_v2", "eleven_multilingual_v2"],   "key_label": "ELEVENLABS_API_KEY", "key_url": "https://elevenlabs.io",              "icon": "El"},
        {"id": "openai_tts", "name": "OpenAI TTS",  "env_var": "OPENAI_API_KEY",     "models": ["tts-1", "tts-1-hd"],                          "key_label": "OPENAI_API_KEY",     "key_url": "https://platform.openai.com/api-keys","icon": "O"},
    ],
    "telephony": [
        {"id": "livekit", "name": "LiveKit",  "env_var": "LIVEKIT_API_KEY", "models": [], "key_label": "LIVEKIT_URL + LIVEKIT_API_KEY", "key_url": "https://cloud.livekit.io", "icon": "Lk"},
        {"id": "vobiz",   "name": "Vobiz",    "env_var": "VOBIZ_AUTH_TOKEN","models": [], "key_label": "VOBIZ_ACCOUNT_SID + AUTH",    "key_url": "https://vobiz.in",          "icon": "V"},
        {"id": "exotel",  "name": "Exotel",   "env_var": "EXOTEL_API_KEY",  "models": [], "key_label": "EXOTEL_API_KEY",               "key_url": "https://exotel.com",        "icon": "Ex"},
    ],
    "his": [
        {"id": "oxzygen", "name": "Oxzygen HIS", "env_var": "OXZYGEN_API_KEY", "models": [], "key_label": "OXZYGEN_API_KEY", "key_url": "https://oxzygen.com", "icon": "O"},
        {"id": "custom",  "name": "Custom REST",  "env_var": "",                "models": [], "key_label": "Custom Base URL",  "key_url": "",                    "icon": "C"},
    ],
}

# ── Hardcoded Anthropic models (no public API) ─────────────────────────────────
ANTHROPIC_MODELS = [
    "claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5",
    "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229", "claude-3-sonnet-20240229", "claude-3-haiku-20240307"
]

# ── Sarvam voices catalogue ────────────────────────────────────────────────────
SARVAM_VOICES = [
    {"id": "shreya",   "name": "Shreya",   "gender": "female", "language": "Hindi/English", "description": "Warm, professional"},
    {"id": "kavitha",  "name": "Kavitha",  "gender": "female", "language": "Kannada/English","description": "Clear, friendly"},
    {"id": "priya",    "name": "Priya",    "gender": "female", "language": "Hindi",          "description": "Soft, natural"},
    {"id": "rahul",    "name": "Rahul",    "gender": "male",   "language": "Hindi/English", "description": "Professional, calm"},
    {"id": "aditya",   "name": "Aditya",   "gender": "male",   "language": "Marathi/English","description": "Energetic, clear"},
    {"id": "rohan",    "name": "Rohan",    "gender": "male",   "language": "Bengali/English","description": "Deep, calm"},
    {"id": "ritu",     "name": "Ritu",     "gender": "female", "language": "Hindi",          "description": "Young, vibrant"},
    {"id": "amit",     "name": "Amit",     "gender": "male",   "language": "Hindi",          "description": "Authoritative"},
    {"id": "simran",   "name": "Simran",   "gender": "female", "language": "Hindi",          "description": "Cheerful"},
    {"id": "shubh",    "name": "Shubh",    "gender": "male",   "language": "English",        "description": "Neutral, clear"},
]

OPENAI_TTS_VOICES = [
    {"id": "alloy",   "name": "Alloy",   "gender": "neutral", "language": "English", "description": "Well-rounded, neutral"},
    {"id": "echo",    "name": "Echo",    "gender": "male",    "language": "English", "description": "Soft, emotive"},
    {"id": "fable",   "name": "Fable",   "gender": "male",    "language": "English", "description": "Expressive, British"},
    {"id": "onyx",    "name": "Onyx",    "gender": "male",    "language": "English", "description": "Deep, authoritative"},
    {"id": "nova",    "name": "Nova",    "gender": "female",  "language": "English", "description": "Bright, energetic"},
    {"id": "shimmer", "name": "Shimmer", "gender": "female",  "language": "English", "description": "Clear, warm"},
]

# ── Schemas ────────────────────────────────────────────────────────────────────
class KeyUpsert(BaseModel):
    provider: str
    category: str
    api_key: str
    extra_config: Optional[str] = None
    is_active: Optional[bool] = None

# ── Helper: get raw key for a provider ────────────────────────────────────────
async def _get_raw_key(provider: str, db: AsyncSession) -> str | None:
    result = await db.execute(
        select(ApiKeyConfig).where(ApiKeyConfig.provider == provider, ApiKeyConfig.is_active == True)
    )
    rec = result.scalar_one_or_none()
    if rec:
        return rec.get_key_raw()
    # fallback: check all records (not just active)
    result2 = await db.execute(
        select(ApiKeyConfig).where(ApiKeyConfig.provider == provider)
    )
    rec2 = result2.scalar_one_or_none()
    return rec2.get_key_raw() if rec2 and rec2.api_key_enc else None

# ── ENV SYNC (called on startup + via endpoint) ───────────────────────────────
async def sync_keys_from_env(db: AsyncSession) -> int:
    """Pull API keys from environment/.env and insert them into api_key_configs if not already set."""
    from backend.config import settings

    env_map = [
        ("gemini",    "llm",       settings.gemini_api_key),
        ("openai",    "llm",       getattr(settings, "openai_api_key", "")),
        ("anthropic", "llm",       getattr(settings, "anthropic_api_key", "")),
        ("deepseek",  "llm",       getattr(settings, "deepseek_api_key", "")),
        ("groq",      "llm",       getattr(settings, "groq_api_key", "")),
        ("mistral",   "llm",       getattr(settings, "mistral_api_key", "")),
        ("sarvam",    "stt",       settings.sarvam_api_key),
        ("sarvam",    "tts",       settings.sarvam_api_key),
        ("elevenlabs","tts",       getattr(settings, "elevenlabs_api_key", "")),
        ("openai_tts","tts",       getattr(settings, "openai_api_key", "")),
        ("deepgram",  "stt",       getattr(settings, "deepgram_api_key", "")),
        ("livekit",   "telephony", settings.livekit_api_key),
        ("vobiz",     "telephony", settings.vobiz_auth_token),
        ("exotel",    "telephony", getattr(settings, "exotel_api_key", "")),
        ("oxzygen",   "his",       settings.oxzygen_api_key),
    ]

    synced = 0
    for provider_id, cat_id, val in env_map:
        if not val or not val.strip():
            continue

        existing = (await db.execute(
            select(ApiKeyConfig).where(
                ApiKeyConfig.provider == provider_id,
                ApiKeyConfig.category == cat_id,
            )
        )).scalar_one_or_none()

        if existing:
            # Only fill in if key is currently empty
            if not existing.api_key_enc:
                existing.set_key(val.strip())
                synced += 1
        else:
            dname = provider_id
            for cat_providers in PROVIDERS.values():
                for p in cat_providers:
                    if p["id"] == provider_id:
                        dname = p["name"]

            # This provider is the first (and only) — make it active
            has_active_in_cat = (await db.execute(
                select(ApiKeyConfig).where(
                    ApiKeyConfig.category == cat_id,
                    ApiKeyConfig.is_active == True,
                )
            )).scalar_one_or_none() is not None

            new_key = ApiKeyConfig(
                id=str(uuid.uuid4()),
                provider=provider_id,
                category=cat_id,
                display_name=dname,
                is_active=not has_active_in_cat,
            )
            new_key.set_key(val.strip())
            db.add(new_key)
            synced += 1

    if synced:
        await db.commit()
    return synced

# ── GET /platform/providers ───────────────────────────────────────────────────
@router.get("/platform/providers")
async def list_providers():
    return PROVIDERS

# ── GET /platform/keys ────────────────────────────────────────────────────────
@router.get("/platform/keys")
async def list_keys(db: AsyncSession = Depends(get_db)):
    try:
        await sync_keys_from_env(db)
        result = await db.execute(select(ApiKeyConfig).order_by(ApiKeyConfig.category, ApiKeyConfig.provider))
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "provider": r.provider,
                "category": r.category,
                "display_name": r.display_name,
                "key_masked": r.get_key_masked(),
                "has_key": bool(r.api_key_enc),
                "is_active": r.is_active,
                "extra_config": r.extra_config,
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── POST /platform/keys ───────────────────────────────────────────────────────
@router.post("/platform/keys")
async def upsert_key(data: KeyUpsert, db: AsyncSession = Depends(get_db)):
    try:
        dname = data.provider
        for cat_providers in PROVIDERS.values():
            for p in cat_providers:
                if p["id"] == data.provider:
                    dname = p["name"]

        existing = (await db.execute(
            select(ApiKeyConfig).where(
                ApiKeyConfig.provider == data.provider,
                ApiKeyConfig.category == data.category,
            )
        )).scalar_one_or_none()

        if existing:
            if data.api_key.strip():
                existing.set_key(data.api_key.strip())
            if data.extra_config is not None:
                existing.extra_config = data.extra_config
            if data.is_active is not None:
                existing.is_active = data.is_active
            await db.commit()
            return {"id": existing.id, "status": "updated", "has_key": bool(existing.api_key_enc)}
        else:
            new_key = ApiKeyConfig(
                id=str(uuid.uuid4()),
                provider=data.provider, category=data.category, display_name=dname,
                is_active=data.is_active if data.is_active is not None else False,
                extra_config=data.extra_config,
            )
            if data.api_key.strip():
                new_key.set_key(data.api_key.strip())
            db.add(new_key)
            await db.commit()
            return {"id": new_key.id, "status": "created", "has_key": bool(new_key.api_key_enc)}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# ── DELETE /platform/keys/{key_id} ───────────────────────────────────────────
@router.delete("/platform/keys/{key_id}")
async def delete_key(key_id: str, db: AsyncSession = Depends(get_db)):
    try:
        key = (await db.execute(select(ApiKeyConfig).where(ApiKeyConfig.id == key_id))).scalar_one_or_none()
        if not key:
            raise HTTPException(status_code=404, detail="Key not found")
        await db.delete(key)
        await db.commit()
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── PATCH /platform/keys/{key_id}/activate ───────────────────────────────────
@router.patch("/platform/keys/{key_id}/activate")
async def set_active_provider(key_id: str, db: AsyncSession = Depends(get_db)):
    try:
        key = (await db.execute(select(ApiKeyConfig).where(ApiKeyConfig.id == key_id))).scalar_one_or_none()
        if not key:
            raise HTTPException(status_code=404, detail="Key not found")
        for k in (await db.execute(select(ApiKeyConfig).where(ApiKeyConfig.category == key.category))).scalars().all():
            k.is_active = (k.id == key_id)
        await db.commit()
        return {"activated": key_id, "category": key.category}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── GET /platform/keys/check/{category} ──────────────────────────────────────
@router.get("/platform/keys/check/{category}")
async def check_key(category: str, db: AsyncSession = Depends(get_db)):
    try:
        await sync_keys_from_env(db)
        key = (await db.execute(
            select(ApiKeyConfig).where(
                ApiKeyConfig.category == category, ApiKeyConfig.is_active == True
            )
        )).scalar_one_or_none()
        return {
            "category": category,
            "configured": bool(key and key.api_key_enc),
            "provider": key.provider if key else None,
            "display_name": key.display_name if key else None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── POST /platform/sync-from-env ─────────────────────────────────────────────
@router.post("/platform/sync-from-env")
async def trigger_env_sync(db: AsyncSession = Depends(get_db)):
    try:
        count = await sync_keys_from_env(db)
        return {"synced": count, "message": f"Synced {count} key(s) from environment"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── POST /platform/providers/{provider}/fetch-models ─────────────────────────
@router.post("/platform/providers/{provider}/fetch-models")
async def fetch_provider_models(provider: str, db: AsyncSession = Depends(get_db)):
    """Fetch available models from provider API using stored key."""
    raw_key = await _get_raw_key(provider, db)

    models: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if provider == "gemini":
                if not raw_key:
                    raise HTTPException(400, "Gemini API key not configured")
                r = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    headers={"x-goog-api-key": raw_key}
                )
                r.raise_for_status()
                data = r.json()
                models = sorted([
                    m["name"].replace("models/", "")
                    for m in data.get("models", [])
                    if "generateContent" in m.get("supportedGenerationMethods", [])
                ])

            elif provider == "openai":
                if not raw_key:
                    raise HTTPException(400, "OpenAI API key not configured")
                r = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {raw_key}"}
                )
                r.raise_for_status()
                models = sorted([
                    m["id"] for m in r.json().get("data", [])
                    if any(m["id"].startswith(p) for p in ("gpt-", "o1", "o3", "chatgpt"))
                ])

            elif provider == "anthropic":
                models = ANTHROPIC_MODELS

            elif provider == "deepseek":
                if not raw_key:
                    raise HTTPException(400, "DeepSeek API key not configured")
                r = await client.get(
                    "https://api.deepseek.com/models",
                    headers={"Authorization": f"Bearer {raw_key}"}
                )
                r.raise_for_status()
                models = [m["id"] for m in r.json().get("data", [])]

            elif provider == "groq":
                if not raw_key:
                    raise HTTPException(400, "Groq API key not configured")
                r = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {raw_key}"}
                )
                r.raise_for_status()
                models = sorted([m["id"] for m in r.json().get("data", [])])

            elif provider == "mistral":
                if not raw_key:
                    raise HTTPException(400, "Mistral API key not configured")
                r = await client.get(
                    "https://api.mistral.ai/v1/models",
                    headers={"Authorization": f"Bearer {raw_key}"}
                )
                r.raise_for_status()
                models = sorted([m["id"] for m in r.json().get("data", [])])

            elif provider == "ollama":
                base_url = raw_key or "http://localhost:11434"
                r = await client.get(f"{base_url}/api/tags")
                r.raise_for_status()
                models = [m["name"] for m in r.json().get("models", [])]

            else:
                raise HTTPException(400, f"Unknown provider: {provider}")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch models from {provider}: {str(e)}")

    # Cache the fetched models in extra_config
    if models:
        try:
            rec = (await db.execute(
                select(ApiKeyConfig).where(ApiKeyConfig.provider == provider)
            )).scalar_one_or_none()
            if rec:
                ec = json.loads(rec.extra_config or "{}")
                ec["models"] = models
                ec["models_fetched_at"] = datetime.now(timezone.utc).isoformat()
                rec.extra_config = json.dumps(ec)
                await db.commit()
        except Exception as cache_err:
            logger.warning("Model cache write failed: %s", cache_err)

    return {
        "provider": provider,
        "models": models,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }

# ── GET /platform/tts/voices/sarvam ──────────────────────────────────────────
@router.get("/platform/tts/voices/sarvam")
async def sarvam_voices():
    return {
        "provider": "sarvam",
        "voices": [
            {"id": "meera", "name": "Meera", "gender": "female", "language": "Hindi / English", "language_code": "hi-IN"},
            {"id": "pavithra", "name": "Pavithra", "gender": "female", "language": "Kannada / English", "language_code": "kn-IN"},
            {"id": "maitreyi", "name": "Maitreyi", "gender": "female", "language": "Hindi", "language_code": "hi-IN"},
            {"id": "arvind", "name": "Arvind", "gender": "male", "language": "Hindi / English", "language_code": "hi-IN"},
            {"id": "amol", "name": "Amol", "gender": "male", "language": "Marathi / English", "language_code": "mr-IN"},
            {"id": "amartya", "name": "Amartya", "gender": "male", "language": "Bengali / English", "language_code": "bn-IN"},
            {"id": "diya", "name": "Diya", "gender": "female", "language": "Hindi", "language_code": "hi-IN"},
            {"id": "neel", "name": "Neel", "gender": "male", "language": "Hindi", "language_code": "hi-IN"},
            {"id": "misha", "name": "Misha", "gender": "female", "language": "Hindi", "language_code": "hi-IN"},
            {"id": "vian", "name": "Vian", "gender": "male", "language": "English (India)", "language_code": "en-IN"},
            {"id": "arjun", "name": "Arjun", "gender": "male", "language": "Hindi", "language_code": "hi-IN"},
            {"id": "maya", "name": "Maya", "gender": "female", "language": "Tamil / English", "language_code": "ta-IN"},
            {"id": "shubh", "name": "Shubh", "gender": "male", "language": "Hindi", "language_code": "hi-IN"},
            {"id": "anushka", "name": "Anushka", "gender": "female", "language": "Hindi", "language_code": "hi-IN"},
            {"id": "karun", "name": "Karun", "gender": "male", "language": "Kannada", "language_code": "kn-IN"},
            {"id": "hitesh", "name": "Hitesh", "gender": "male", "language": "Hindi", "language_code": "hi-IN"}
        ]
    }

# KEEPING THE OLD list_voices endpoint for other providers (if needed)
@router.get("/platform/tts/voices/{provider}")
async def list_voices(provider: str, db: AsyncSession = Depends(get_db)):
    if provider == "sarvam":
        return await sarvam_voices()

    elif provider == "openai_tts":
        raw_key = await _get_raw_key("openai_tts", db)
        return {"provider": "openai_tts", "has_key": bool(raw_key), "voices": OPENAI_TTS_VOICES}

    elif provider == "elevenlabs":
        raw_key = await _get_raw_key("elevenlabs", db)
        if not raw_key:
            return {"provider": "elevenlabs", "has_key": False, "voices": []}
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    "https://api.elevenlabs.io/v1/voices",
                    headers={"xi-api-key": raw_key}
                )
                r.raise_for_status()
                voices = [
                    {
                        "id": v["voice_id"],
                        "name": v["name"],
                        "gender": v.get("labels", {}).get("gender", "neutral"),
                        "language": v.get("labels", {}).get("accent", "English"),
                        "description": v.get("labels", {}).get("description", ""),
                        "preview_url": v.get("preview_url", ""),
                    }
                    for v in r.json().get("voices", [])
                ]
            return {"provider": "elevenlabs", "has_key": True, "voices": voices}
        except Exception as e:
            raise HTTPException(500, f"ElevenLabs voice fetch failed: {e}")

    raise HTTPException(400, f"Unknown TTS provider: {provider}")

# ── GET /platform/tts/preview ────────────────────────────────────────────────
@router.get("/platform/tts/preview")
async def tts_preview(
    provider: str = "sarvam",
    voice_id: str = "meera",
    language: str = "hi-IN",
    text: str = "Hello! I am your AI receptionist. How can I help you today?",
    pitch: float = 0.0,
    pace: float = 1.0,
    loudness: float = 1.0,
    db: AsyncSession = Depends(get_db),
):
    if provider == "sarvam":
        api_key = os.getenv("SARVAM_API_KEY")
        if not api_key:
            raw_key = await _get_raw_key("sarvam", db)
            api_key = raw_key
            
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.sarvam.ai/text-to-speech",
                headers={
                    "api-subscription-key": api_key,
                    "Content-Type": "application/json"
                },
                json={
                    "inputs": [text],
                    "target_language_code": language,
                    "speaker": voice_id,
                    "model": "bulbul:v2",
                    "pitch": pitch,
                    "pace": pace,
                    "loudness": loudness,
                    "speech_sample_rate": 22050,
                    "enable_preprocessing": True,
                    "output_audio_codec": "mp3"
                }
            )
            
            data = response.json()
            if "audios" not in data or not data["audios"]:
                raise HTTPException(status_code=400, detail=f"Sarvam TTS preview failed: {data}")
            audio_base64 = data["audios"][0]
            audio_bytes = base64.b64decode(audio_base64)
            
            return Response(
                content=audio_bytes,
                media_type="audio/mpeg",
                headers={"Content-Disposition": "inline"}
            )
            
    # Keep support for other providers just in case
    raw_key = await _get_raw_key(provider, db)
    if not raw_key:
        raise HTTPException(400, f"No API key configured for provider '{provider}'. Add it in AI Platform → Text-to-Speech.")

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            if provider == "elevenlabs":
                r = await client.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream",
                    headers={"xi-api-key": raw_key, "Content-Type": "application/json"},
                    json={"text": text, "model_id": "eleven_turbo_v2", "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
                )
                r.raise_for_status()
                return Response(content=r.content, media_type="audio/mpeg")

            elif provider == "openai_tts":
                r = await client.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers={"Authorization": f"Bearer {raw_key}", "Content-Type": "application/json"},
                    json={"model": "tts-1", "input": text, "voice": voice_id},
                )
                r.raise_for_status()
                return Response(content=r.content, media_type="audio/mpeg")

            else:
                raise HTTPException(400, f"TTS preview not supported for provider: {provider}")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"TTS preview failed: {str(e)}")


# ── POST /stt/transcribe ───────────────────────────────────────────────────────
@router.post("/stt/transcribe")
async def transcribe_audio(
    audio_file: UploadFile = File(...),
    language: str = "hi-IN"
):
    api_key = os.getenv("SARVAM_API_KEY")
    if not api_key:
        from backend.db import AsyncSessionLocal
        async with AsyncSessionLocal() as s:
            api_key = await _get_raw_key("sarvam", s)
            
    audio_bytes = await audio_file.read()
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "https://api.sarvam.ai/speech-to-text",
            headers={"api-subscription-key": api_key},
            files={
                "file": (audio_file.filename, audio_bytes, "audio/wav")
            },
            data={
                "model": "saarika:v2",
                "language_code": language,
                "with_timestamps": "false",
                "debug": "false"
            }
        )
        
        data = response.json()
        return {
            "transcript": data.get("transcript", ""),
            "language_code": data.get("language_code", language)
        }


# ── GET /platform/models/{provider} — quick model list for dropdowns ─────────
@router.get("/platform/models/{provider}")
async def get_models_for_provider(
    provider: str,
    category: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db)
):
    """Return model list for a provider. Uses cached dynamic models if available, else static defaults."""
    # Check if we have cached dynamic models
    rec = (await db.execute(
        select(ApiKeyConfig).where(ApiKeyConfig.provider == provider)
    )).scalars().first()

    if rec and rec.extra_config:
        try:
            ec = json.loads(rec.extra_config)
            if ec.get("models"):
                return {"provider": provider, "models": ec["models"], "source": "dynamic"}
        except Exception:
            pass

    # Fallback to static PROVIDERS catalogue (category-aware if provided)
    if category and category in PROVIDERS:
        for p in PROVIDERS[category]:
            if p["id"] == provider:
                return {"provider": provider, "category": category, "models": p.get("models", []), "source": "static"}

    for cat_name, cat in PROVIDERS.items():
        for p in cat:
            if p["id"] == provider:
                return {"provider": provider, "category": cat_name, "models": p.get("models", []), "source": "static"}

    return {"provider": provider, "category": category, "models": [], "source": "unknown"}


# ── GET /platform/env-status — show which .env keys are configured ───────────
@router.get("/platform/env-status")
async def env_status():
    """Returns which API keys are configured in .env (no raw values exposed)."""
    from backend.config import settings
    return {
        "gemini": bool(settings.gemini_api_key),
        "openai": bool(settings.openai_api_key),
        "anthropic": bool(settings.anthropic_api_key),
        "sarvam": bool(settings.sarvam_api_key),
        "groq": bool(settings.groq_api_key),
        "elevenlabs": bool(settings.elevenlabs_api_key),
        "deepgram": bool(settings.deepgram_api_key),
        "deepseek": bool(settings.deepseek_api_key),
        "mistral": bool(settings.mistral_api_key),
        "livekit": bool(settings.livekit_api_key),
        "vobiz": bool(settings.vobiz_auth_token),
    }

# Note: POST /platform/sync-from-env is already defined above (trigger_env_sync)
# which uses the well-tested sync_keys_from_env() helper.
