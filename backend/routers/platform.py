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

from backend.auth import CurrentUser, SuperAdmin
from backend.db import AsyncSessionLocal
from backend.models.api_key_config import ApiKeyConfig

logger = logging.getLogger(__name__)
router = APIRouter()

# ── DB Dep ────────────────────────────────────────────────────────────────────
async def get_db():
    async with AsyncSessionLocal() as s:
        yield s

# ── .env writing (local dev convenience; production uses Render env vars) ──────
from pathlib import Path
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def _provider_env_var(provider: str, category: str) -> str | None:
    """The env var name a provider's key maps to (from the PROVIDERS catalog)."""
    for p in PROVIDERS.get(category, []):
        if p["id"] == provider:
            return p.get("env_var") or None
    # provider may exist under a different category (e.g. sarvam stt+tts share a key)
    for cat in PROVIDERS.values():
        for p in cat:
            if p["id"] == provider and p.get("env_var"):
                return p["env_var"]
    return None


def _read_env_var(env_var: str) -> str | None:
    try:
        from dotenv import get_key
        return get_key(str(_ENV_PATH), env_var)
    except Exception:
        return None


def _write_env_var(env_var: str, value: str) -> None:
    """Write a single var into the local .env. Raises on failure so the caller
    can report it (never silently swallow a partial success)."""
    from dotenv import set_key
    if not _ENV_PATH.exists():
        _ENV_PATH.touch()
    set_key(str(_ENV_PATH), env_var, value)


def _clear_env_var(env_var: str) -> None:
    try:
        from dotenv import set_key
        if _ENV_PATH.exists():
            set_key(str(_ENV_PATH), env_var, "")
    except Exception as e:
        logger.warning("Could not clear %s in .env: %s", env_var, e)


def _validate_key_format(provider: str, raw: str) -> str | None:
    """Return an error string if the key is obviously malformed, else None.
    Guards against pasting garbage that only fails later at call time."""
    k = (raw or "").strip()
    if len(k) < 8:
        return "That key looks too short to be valid."
    if any(ch.isspace() for ch in k):
        return "Key contains whitespace — check for a stray copy/paste newline or space."
    if not all(32 <= ord(c) < 127 for c in k):
        return "Key contains non-printable/non-ASCII characters."
    return None


# ── Configured-providers cache (feeds the agent builder — Phase B) ─────────────
# Short in-process TTL so the agent builder never full-scans api_key_configs on
# every page load. Invalidated immediately on any key add/update/delete/activate
# so a deleted key disappears from the builder right away.
import time as _time
_CONFIGURED_CACHE: dict = {"data": None, "ts": 0.0}
_CONFIGURED_TTL = 20.0


def _invalidate_configured_cache() -> None:
    _CONFIGURED_CACHE["data"] = None
    _CONFIGURED_CACHE["ts"] = 0.0


# ── Audit log (Phase D) — records who/what/when, NEVER the key value ──────────
async def _audit(db: AsyncSession, actor: str, action: str, target: str = "", detail: str = "") -> None:
    from backend.models.audit_log import AuditLog
    try:
        db.add(AuditLog(actor=actor or "unknown", action=action, target=target[:120], detail=detail[:500]))
        await db.commit()
    except Exception as e:
        logger.warning("audit log write failed (%s/%s): %s", action, target, e)


# ── Rate limiting for key-management endpoints (Phase D) ──────────────────────
# In-process sliding window per actor. Guards against brute-force key
# enumeration / abuse of the write endpoints.
from collections import defaultdict
_KEY_OP_TIMES: dict = defaultdict(list)
_KEY_OP_LIMIT = 10       # ops
_KEY_OP_WINDOW = 60.0    # seconds


def _rate_limit_key_ops(actor: str) -> None:
    now = _time.monotonic()
    times = [t for t in _KEY_OP_TIMES[actor] if now - t < _KEY_OP_WINDOW]
    if len(times) >= _KEY_OP_LIMIT:
        raise HTTPException(status_code=429, detail="Too many key operations. Please wait a minute and try again.")
    times.append(now)
    _KEY_OP_TIMES[actor] = times

# Shared preview timeout for all TTS providers. Long enough to absorb a cold
# synthesis on a normal connection, short enough that a hung provider surfaces a
# clear error instead of the browser's own abort (which produced the old
# misleading "Sarvam took too long" message regardless of provider).
_TTS_PREVIEW_TIMEOUT = 12.0

# ── Provider catalogue ────────────────────────────────────────────────────────
PROVIDERS = {
    "llm": [
        {"id": "gemini",    "name": "Google Gemini",    "env_var": "GEMINI_API_KEY",
         "models": ["gemini-2.5-flash", "gemini-2.5-flash-8b", "gemini-2.5-pro-preview-05-06", "gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
         "key_label": "GEMINI_API_KEY",    "key_url": "https://aistudio.google.com/app/apikey",   "icon": "G"},
        {"id": "openai",    "name": "OpenAI",           "env_var": "OPENAI_API_KEY",
         "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1-mini", "o1", "o3-mini"],
         "key_label": "OPENAI_API_KEY",    "key_url": "https://platform.openai.com/api-keys",     "icon": "O"},
        {"id": "anthropic", "name": "Anthropic Claude", "env_var": "ANTHROPIC_API_KEY",
         "models": ["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5", "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022", "claude-3-haiku-20240307"],
         "key_label": "ANTHROPIC_API_KEY", "key_url": "https://console.anthropic.com/settings/keys","icon": "A"},
        {"id": "deepseek",  "name": "DeepSeek",         "env_var": "DEEPSEEK_API_KEY",
         "models": ["deepseek-chat", "deepseek-reasoner"],
         "key_label": "DEEPSEEK_API_KEY",  "key_url": "https://platform.deepseek.com",            "icon": "DS"},
        {"id": "groq",      "name": "Groq",             "env_var": "GROQ_API_KEY",
         "models": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "llama-3.1-70b-versatile", "llama3-8b-8192", "llama3-70b-8192", "mixtral-8x7b-32768", "gemma2-9b-it", "gemma-7b-it", "deepseek-r1-distill-llama-70b", "compound-beta-mini"],
         "key_label": "GROQ_API_KEY",      "key_url": "https://console.groq.com/keys",            "icon": "Gq"},
        {"id": "mistral",   "name": "Mistral AI",       "env_var": "MISTRAL_API_KEY",
         "models": ["mistral-large-latest", "mistral-small-latest", "open-mixtral-8x7b", "open-mistral-nemo"],
         "key_label": "MISTRAL_API_KEY",   "key_url": "https://console.mistral.ai",               "icon": "M"},
        {"id": "cerebras",  "name": "Cerebras",         "env_var": "CEREBRAS_API_KEY",
         "models": ["llama-3.3-70b", "llama3.1-8b", "llama-4-scout-17b-16e-instruct", "qwen-3-32b"],
         "key_label": "CEREBRAS_API_KEY",  "key_url": "https://cloud.cerebras.ai",                "icon": "Cb"},
        {"id": "ollama",    "name": "Ollama (Local)",   "env_var": "",
         "models": [],
         "key_label": "Base URL (no key)", "key_url": "https://ollama.com",                       "icon": "Ol"},
    ],
    "stt": [
        {"id": "sarvam",     "name": "Sarvam AI",      "env_var": "SARVAM_API_KEY",
         "models": ["saaras:v3", "saarika:v2.5"],
         "key_label": "SARVAM_API_KEY",    "key_url": "https://dashboard.sarvam.ai",         "icon": "S"},
        {"id": "elevenlabs", "name": "ElevenLabs",     "env_var": "ELEVENLABS_API_KEY",
         "models": ["scribe_v2_realtime", "scribe_v2"],
         "key_label": "ELEVENLABS_API_KEY", "key_url": "https://elevenlabs.io",               "icon": "El"},
        {"id": "deepgram",   "name": "Deepgram",       "env_var": "DEEPGRAM_API_KEY",
         "models": ["nova-2", "nova-2-medical", "nova-2-meeting", "nova-2-phonecall"],
         "key_label": "DEEPGRAM_API_KEY",  "key_url": "https://console.deepgram.com",        "icon": "D"},
        {"id": "whisper",    "name": "OpenAI Whisper", "env_var": "OPENAI_API_KEY",
         "models": ["whisper-1"],
         "key_label": "OPENAI_API_KEY",    "key_url": "https://platform.openai.com/api-keys","icon": "W"},
        {"id": "assemblyai", "name": "AssemblyAI",     "env_var": "ASSEMBLYAI_API_KEY",
         "models": ["best", "nano"],
         "key_label": "ASSEMBLYAI_API_KEY","key_url": "https://www.assemblyai.com",           "icon": "As"},
        {"id": "google_stt", "name": "Google Cloud Speech", "env_var": "GOOGLE_SPEECH_API_KEY",
         "models": ["latest_long", "latest_short", "telephony", "medical_conversation"],
         "key_label": "GOOGLE_SPEECH_API_KEY", "key_url": "https://console.cloud.google.com/apis/credentials", "icon": "GC"},
        {"id": "azure_stt",  "name": "Azure Speech",   "env_var": "AZURE_SPEECH_KEY",
         "models": ["default"],
         "key_label": "AZURE_SPEECH_KEY",  "key_url": "https://portal.azure.com",            "icon": "Az"},
    ],
    "tts": [
        {"id": "sarvam",     "name": "Sarvam AI",   "env_var": "SARVAM_API_KEY",
         "models": ["bulbul:v3", "bulbul:v2", "bulbul:v1"],
         "key_label": "SARVAM_API_KEY",     "key_url": "https://dashboard.sarvam.ai",        "icon": "S"},
        {"id": "elevenlabs", "name": "ElevenLabs",  "env_var": "ELEVENLABS_API_KEY",
         "models": ["eleven_v3", "eleven_flash_v2_5", "eleven_multilingual_v2", "eleven_turbo_v2_5"],
         "key_label": "ELEVENLABS_API_KEY", "key_url": "https://elevenlabs.io",              "icon": "El"},
        {"id": "openai_tts", "name": "OpenAI TTS",  "env_var": "OPENAI_API_KEY",
         "models": ["tts-1", "tts-1-hd", "gpt-4o-mini-tts"],
         "key_label": "OPENAI_API_KEY",     "key_url": "https://platform.openai.com/api-keys","icon": "O"},
        {"id": "cartesia",   "name": "Cartesia",   "env_var": "CARTESIA_API_KEY",
         "models": ["sonic-2", "sonic-turbo", "sonic-english"],
         "key_label": "CARTESIA_API_KEY",   "key_url": "https://play.cartesia.ai",            "icon": "Ct"},
        {"id": "playht",     "name": "PlayHT",     "env_var": "PLAYHT_API_KEY",
         "models": ["PlayDialog", "Play3.0-mini", "PlayHT2.0-turbo"],
         "key_label": "PLAYHT_API_KEY",     "key_url": "https://play.ht/studio/api-access",   "icon": "Ph"},
        {"id": "azure_tts",  "name": "Azure Neural TTS", "env_var": "AZURE_SPEECH_KEY",
         "models": ["neural"],
         "key_label": "AZURE_SPEECH_KEY",   "key_url": "https://portal.azure.com",            "icon": "Az"},
        {"id": "deepgram_aura", "name": "Deepgram Aura", "env_var": "DEEPGRAM_API_KEY",
         "models": ["aura-2", "aura-asteria-en", "aura-luna-en"],
         "key_label": "DEEPGRAM_API_KEY",   "key_url": "https://console.deepgram.com",        "icon": "D"},
    ],
    "voice_clone": [
        {"id": "elevenlabs", "name": "ElevenLabs (Instant + Professional)", "env_var": "ELEVENLABS_API_KEY",
         "models": ["instant", "professional"],
         "key_label": "ELEVENLABS_API_KEY", "key_url": "https://elevenlabs.io/voice-cloning",  "icon": "El"},
        {"id": "cartesia",   "name": "Cartesia",    "env_var": "CARTESIA_API_KEY",
         "models": ["voice-clone"],
         "key_label": "CARTESIA_API_KEY",   "key_url": "https://play.cartesia.ai",            "icon": "Ct"},
        {"id": "playht",     "name": "PlayHT",      "env_var": "PLAYHT_API_KEY",
         "models": ["instant-clone", "high-fidelity-clone"],
         "key_label": "PLAYHT_API_KEY",     "key_url": "https://play.ht",                     "icon": "Ph"},
        {"id": "resemble",   "name": "Resemble AI", "env_var": "RESEMBLE_API_KEY",
         "models": ["core", "rapid-clone"],
         "key_label": "RESEMBLE_API_KEY",   "key_url": "https://app.resemble.ai",             "icon": "Rs"},
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

# ── Static authoritative model lists for providers without a list API ──────────
# Sarvam has no public model enumeration endpoint
SARVAM_TTS_MODELS = ["bulbul:v3", "bulbul:v2", "bulbul:v1"]
SARVAM_STT_MODELS = ["saarika:v2.5", "saarika:v2", "saaras:v3", "saaras:v2"]

# ElevenLabs STT (Scribe) — listed here as fallback; also fetched live via /v1/models
ELEVENLABS_STT_MODELS = ["scribe_v2_realtime", "scribe_v2"]

# Deepgram Nova models
DEEPGRAM_STT_MODELS = [
    "nova-2", "nova-2-general", "nova-2-meeting", "nova-2-phonecall",
    "nova-2-finance", "nova-2-conversationalai", "nova-2-voicemail",
    "nova-2-video", "nova-2-medical", "nova-2-drivethru", "nova-2-automotive",
    "nova-3", "base", "enhanced"
]

# AssemblyAI models
ASSEMBLYAI_STT_MODELS = ["best", "nano"]

# OpenAI TTS models
OPENAI_TTS_MODELS = ["gpt-4o-mini-tts", "tts-1", "tts-1-hd"]

# OpenAI STT models  
OPENAI_STT_MODELS = ["whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"]

# NOTE: SARVAM_VOICES is now imported from backend.routers.providers (authoritative, full list)

OPENAI_TTS_VOICES = [
    {"id": "alloy",   "name": "Alloy",   "gender": "neutral", "language": "English", "description": "Well-rounded, neutral"},
    {"id": "echo",    "name": "Echo",    "gender": "male",    "language": "English", "description": "Soft, emotive"},
    {"id": "fable",   "name": "Fable",   "gender": "male",    "language": "English", "description": "Expressive, British"},
    {"id": "onyx",    "name": "Onyx",    "gender": "male",    "language": "English", "description": "Deep, authoritative"},
    {"id": "nova",    "name": "Nova",    "gender": "female",  "language": "English", "description": "Bright, energetic"},
    {"id": "shimmer", "name": "Shimmer", "gender": "female",  "language": "English", "description": "Clear, warm"},
]

# Static voice catalogs for providers without a public list-voices API (or whose
# API needs extra setup). These let the Voice Library stay provider-agnostic: any
# provider with a configured key shows its voices. Voice ids are the provider's
# own canonical ids so the preview call works unmodified.
CARTESIA_VOICES = [
    {"id": "a0e99841-438c-4a64-b679-ae501e7d6091", "name": "Barbershop Man", "gender": "male",   "language": "en-US", "description": "Warm American male"},
    {"id": "729651dc-c6c3-4ee5-97fa-350da1f88600", "name": "Sarah",          "gender": "female", "language": "en-US", "description": "Clear American female"},
    {"id": "79a125e8-cd45-4c13-8a67-188112f4dd22", "name": "British Lady",   "gender": "female", "language": "en-GB", "description": "Refined British female"},
]
DEEPGRAM_AURA_VOICES = [
    {"id": "aura-2-thalia-en",  "name": "Thalia",  "gender": "female", "language": "en-US", "description": "Aura 2 · clear, friendly"},
    {"id": "aura-2-andromeda-en","name": "Andromeda","gender": "female","language": "en-US", "description": "Aura 2 · warm"},
    {"id": "aura-2-apollo-en",  "name": "Apollo",  "gender": "male",   "language": "en-US", "description": "Aura 2 · confident"},
    {"id": "aura-asteria-en",   "name": "Asteria", "gender": "female", "language": "en-US", "description": "Aura · natural"},
    {"id": "aura-luna-en",      "name": "Luna",    "gender": "female", "language": "en-US", "description": "Aura · soft"},
]
AZURE_TTS_VOICES = [
    {"id": "en-US-JennyNeural",   "name": "Jenny",   "gender": "female", "language": "en-US", "description": "Neural · assistant"},
    {"id": "en-US-GuyNeural",     "name": "Guy",     "gender": "male",   "language": "en-US", "description": "Neural · newscaster"},
    {"id": "en-IN-NeerjaNeural",  "name": "Neerja",  "gender": "female", "language": "en-IN", "description": "Neural · Indian English"},
    {"id": "hi-IN-SwaraNeural",   "name": "Swara",   "gender": "female", "language": "hi-IN", "description": "Neural · Hindi"},
]
PLAYHT_VOICES = [
    {"id": "s3://voice-cloning-zero-shot/d9ff78ba-d016-47f6-b0ef-dd630f59414e/female-cs/manifest.json", "name": "Delilah", "gender": "female", "language": "en-US", "description": "PlayHT · conversational"},
    {"id": "s3://voice-cloning-zero-shot/e040bd1b-f190-4bdb-83f0-75ef85b18f84/original/manifest.json",   "name": "Angelo",  "gender": "male",   "language": "en-US", "description": "PlayHT · conversational"},
]
STATIC_TTS_VOICE_CATALOG = {
    "openai_tts": OPENAI_TTS_VOICES,
    "cartesia": CARTESIA_VOICES,
    "deepgram_aura": DEEPGRAM_AURA_VOICES,
    "azure_tts": AZURE_TTS_VOICES,
    "playht": PLAYHT_VOICES,
}

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
async def list_providers(user: CurrentUser = None):
    return PROVIDERS

# ── GET /platform/keys ────────────────────────────────────────────────────────
@router.get("/platform/keys")
async def list_keys(user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    try:
        await sync_keys_from_env(db)
        result = await db.execute(select(ApiKeyConfig).order_by(ApiKeyConfig.category, ApiKeyConfig.provider))
        rows = result.scalars().all()

        def _safe_extra(raw: str | None) -> str | None:
            # Never leak the LiveKit secret (even Fernet-encrypted) through this
            # list. Strip secret_enc; expose only non-sensitive hints.
            if not raw:
                return raw
            try:
                d = json.loads(raw)
                if isinstance(d, dict) and "secret_enc" in d:
                    d = {**d, "secret_enc": None, "secret_set": True}
                    return json.dumps(d)
            except Exception:
                pass
            return raw

        return [
            {
                "id": r.id,
                "provider": r.provider,
                "category": r.category,
                "display_name": r.display_name,
                "key_masked": r.get_key_masked(),
                "has_key": bool(r.api_key_enc),
                "is_active": r.is_active,
                "extra_config": _safe_extra(r.extra_config),
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── POST /platform/keys ───────────────────────────────────────────────────────
@router.post("/platform/keys")
async def upsert_key(data: KeyUpsert, user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    """
    Add/update a provider key. On a key change we write BOTH the encrypted DB
    row AND the local .env value, atomically: if the .env write fails we roll
    back the DB and report an error — never a silent partial success. Does NOT
    push to Render; that's a separate, explicitly-confirmed action.
    """
    actor = getattr(user, "subject", None) or "superadmin"
    _rate_limit_key_ops(actor)
    new_key_value = (data.api_key or "").strip()

    # 1. Validate format up-front so we never store garbage that breaks at call time.
    if new_key_value:
        err = _validate_key_format(data.provider, new_key_value)
        if err:
            raise HTTPException(status_code=422, detail=err)

    dname = data.provider
    for cat_providers in PROVIDERS.values():
        for p in cat_providers:
            if p["id"] == data.provider:
                dname = p["name"]

    env_var = _provider_env_var(data.provider, data.category)
    prior_env_value = _read_env_var(env_var) if env_var else None
    env_written = False

    try:
        existing = (await db.execute(
            select(ApiKeyConfig).where(
                ApiKeyConfig.provider == data.provider,
                ApiKeyConfig.category == data.category,
            )
        )).scalar_one_or_none()

        target = existing or ApiKeyConfig(
            id=str(uuid.uuid4()),
            provider=data.provider, category=data.category, display_name=dname,
            is_active=data.is_active if data.is_active is not None else False,
        )
        if new_key_value:
            target.set_key(new_key_value)
        if data.extra_config is not None:
            target.extra_config = data.extra_config
        if data.is_active is not None:
            target.is_active = data.is_active
        if not existing:
            db.add(target)

        # 2. Write .env BEFORE committing the DB. If it throws, we abort and the
        #    DB commit never happens — no partial state.
        if new_key_value and env_var:
            _write_env_var(env_var, new_key_value)
            env_written = True

        # 3. Commit DB. If this fails, restore the prior .env value so the two
        #    stores don't drift.
        try:
            await db.commit()
        except Exception:
            if env_written:
                try:
                    _write_env_var(env_var, prior_env_value or "")
                except Exception:
                    logger.error("DB commit failed AND .env restore failed for %s — manual check needed", env_var)
            raise

        await db.refresh(target)
        _invalidate_configured_cache()
        await _audit(db, actor, "key.update" if existing else "key.create",
                     target=f"{data.provider}/{data.category}",
                     detail=f"key {'changed' if new_key_value else 'metadata-only'}, env_written={env_written}")
        return {
            "id": target.id,
            "status": "updated" if existing else "created",
            "has_key": bool(target.api_key_enc),
            "env_written": env_written,
            "env_var": env_var,
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Save failed (no partial state persisted): {str(e)[:200]}")

# ── DELETE /platform/keys/{key_id} ───────────────────────────────────────────
@router.delete("/platform/keys/{key_id}")
async def delete_key(key_id: str, user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    try:
        actor = getattr(user, "subject", None) or "superadmin"
        _rate_limit_key_ops(actor)
        key = (await db.execute(select(ApiKeyConfig).where(ApiKeyConfig.id == key_id))).scalar_one_or_none()
        if not key:
            raise HTTPException(status_code=404, detail="Key not found")
        _deleted_target = f"{key.provider}/{key.category}"
        env_var = _provider_env_var(key.provider, key.category)
        # Only clear the .env var if no OTHER category still uses this provider's
        # key (e.g. Sarvam is shared by stt+tts — deleting the stt row must not
        # yank the key out from under the tts row).
        shared = (await db.execute(
            select(ApiKeyConfig).where(
                ApiKeyConfig.provider == key.provider,
                ApiKeyConfig.id != key_id,
            )
        )).scalars().first()
        await db.delete(key)
        await db.commit()
        _invalidate_configured_cache()
        if env_var and not shared:
            _clear_env_var(env_var)
        await _audit(db, actor, "key.delete", target=_deleted_target,
                     detail=f"env_cleared={bool(env_var and not shared)}")
        return {"deleted": True, "env_cleared": bool(env_var and not shared)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /platform/configured-providers — feeds the agent builder (Phase B) ────
@router.get("/platform/configured-providers")
async def configured_providers(user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    """
    Per-category list of providers that actually have a key configured — this is
    what the agent builder's dropdowns read (so unconfigured/deleted providers
    never appear as selectable, even though the admin catalog still lists them).
    Cached with a short TTL; invalidated on any key change.
    """
    now = _time.monotonic()
    if _CONFIGURED_CACHE["data"] is not None and (now - _CONFIGURED_CACHE["ts"]) < _CONFIGURED_TTL:
        return _CONFIGURED_CACHE["data"]

    rows = (await db.execute(
        select(ApiKeyConfig).where(ApiKeyConfig.api_key_enc.isnot(None))
    )).scalars().all()

    out: dict = {}
    for r in rows:
        if not r.api_key_enc:
            continue
        models: List[str] = []
        for p in PROVIDERS.get(r.category, []):
            if p["id"] == r.provider:
                models = p.get("models", [])
                break
        out.setdefault(r.category, []).append({
            "id": r.provider,
            "display_name": r.display_name,
            "is_active": r.is_active,
            "models": models,
        })

    _CONFIGURED_CACHE["data"] = out
    _CONFIGURED_CACHE["ts"] = now
    return out


# ── POST /platform/push-to-render — explicit, confirmed production sync ────────
class RenderPushRequest(BaseModel):
    category: Optional[str] = None   # push one category, or all configured keys if None
    include_infra: Optional[bool] = False  # also sync infrastructure vars (fill-only)


# Infrastructure env vars this endpoint MAY sync, mapped to their settings field.
# These are synced FILL-ONLY (see below): pushed only when missing/empty on Render,
# never overwriting an existing prod value — so a push from a dev machine can never
# clobber a correct production SECRET_KEY / ENVIRONMENT / DATABASE_URL.
_INFRA_ENV_FIELDS = [
    ("DATABASE_URL", "database_url"),
    ("ENVIRONMENT", "environment"),
    ("SECRET_KEY", "secret_key"),
    ("LIVEKIT_URL", "livekit_url"),
    ("LIVEKIT_API_SECRET", "livekit_api_secret"),
    ("CORS_ORIGIN", "cors_origin"),
    ("FRONTEND_URL", "frontend_url"),
    ("SUPABASE_URL", "supabase_url"),
    ("SUPABASE_SERVICE_ROLE_KEY", "supabase_service_role_key"),
    ("SUPERADMIN_EMAIL", "superadmin_email"),
    ("SUPERADMIN_PASSWORD", "superadmin_password"),
]


@router.post("/platform/push-to-render")
async def push_to_render(data: RenderPushRequest, user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    """
    Push configuration to the Render service's environment variables.
    Fires ONLY on this explicit call (the UI gates it behind a confirm dialog).
    If Render is unreachable, fails cleanly and leaves local/.env/DB untouched.

    Two scopes:
      • Provider keys (always): the configured AI/telephony keys from the DB. These
        OVERWRITE the matching Render var — updating a key is the whole point.
      • Infrastructure vars (only when include_infra=True): DATABASE_URL, ENVIRONMENT,
        SECRET_KEY, LIVEKIT_URL/SECRET, CORS/FRONTEND, SUPABASE_*, SUPERADMIN_*.
        Synced FILL-ONLY — pushed only when the var is missing/empty on Render, never
        overwriting an existing value. This lets a fresh service be bootstrapped
        without risking a dev machine stomping a correct prod SECRET_KEY/ENVIRONMENT.
        Extra guards: ENVIRONMENT is only ever pushed as "production"; a weak/short
        SECRET_KEY is refused outright.
    """
    from backend.config import settings, _WEAK_SECRETS
    api_key = settings.render_api_key or os.getenv("api_key") or ""
    service_id = settings.render_service_id or os.getenv("service_id") or ""
    if not api_key or not service_id:
        raise HTTPException(status_code=400, detail="Render API key / service id not configured in .env (RENDER_API_KEY, RENDER_SERVICE_ID).")

    # Collect configured keys → their env var names + raw values.
    q = select(ApiKeyConfig).where(ApiKeyConfig.api_key_enc.isnot(None))
    if data.category:
        q = q.where(ApiKeyConfig.category == data.category)
    rows = (await db.execute(q)).scalars().all()

    key_updates: dict[str, str] = {}
    for r in rows:
        ev = _provider_env_var(r.provider, r.category)
        raw = r.get_key_raw()
        if ev and raw:
            key_updates[ev] = raw
    if not key_updates and not data.include_infra:
        raise HTTPException(status_code=400, detail="No configured keys to push.")

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"}
    infra_added: list[str] = []
    infra_skipped: dict[str, str] = {}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            # Merge with existing Render env vars (PUT replaces the whole set).
            cur = await client.get(f"https://api.render.com/v1/services/{service_id}/env-vars", headers=headers)
            if cur.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Render unreachable (list env-vars: HTTP {cur.status_code}). Nothing changed.")
            merged: dict[str, str] = {}
            for item in cur.json():
                ev = item.get("envVar", item)
                if ev.get("key"):
                    merged[ev["key"]] = ev.get("value", "")

            # Provider keys overwrite (that's the intent of a key push).
            merged.update(key_updates)

            # Infra vars: FILL-ONLY. Only set when missing/empty on Render.
            if data.include_infra:
                for env_name, field in _INFRA_ENV_FIELDS:
                    val = (getattr(settings, field, "") or "").strip()
                    if not val:
                        infra_skipped[env_name] = "empty locally"
                        continue
                    if merged.get(env_name, "").strip():
                        infra_skipped[env_name] = "already set on Render (not overwritten)"
                        continue
                    if env_name == "ENVIRONMENT" and val.lower() != "production":
                        infra_skipped[env_name] = f"refused to push non-production value '{val}'"
                        continue
                    if env_name == "SECRET_KEY" and (val in _WEAK_SECRETS or len(val) < 32):
                        infra_skipped[env_name] = "refused to push weak/short SECRET_KEY"
                        continue
                    merged[env_name] = val
                    infra_added.append(env_name)

            payload = [{"key": k, "value": v} for k, v in merged.items()]
            put = await client.put(f"https://api.render.com/v1/services/{service_id}/env-vars", headers=headers, json=payload)
            if put.status_code not in (200, 201):
                raise HTTPException(status_code=502, detail=f"Render push failed (HTTP {put.status_code}). Nothing changed on Render.")
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Render unreachable: {str(e)[:150]}. Local/.env/DB untouched.")

    await _audit(db, getattr(user, "subject", None) or "superadmin", "render.push",
                 target=data.category or "all",
                 detail=f"pushed {len(key_updates)} keys, {len(infra_added)} infra (fill-only)")
    return {"pushed": sorted(key_updates.keys()), "count": len(key_updates),
            "infra_added": sorted(infra_added), "infra_skipped": infra_skipped,
            "note": "Render will redeploy the service to apply the new env vars."}


# ── LiveKit: 3-field management (URL + API key + write-only secret) ────────────
# Stored: api key in api_key_enc (Fernet, maskable); url + Fernet(secret) in
# extra_config JSON. The SECRET is write-only — never returned by any endpoint,
# not even masked (it signs room tokens; a leak lets anyone join any call).
_LIVEKIT_CAT = "telephony"
_LIVEKIT_PROVIDER = "livekit"


async def _get_livekit_row(db: AsyncSession):
    return (await db.execute(select(ApiKeyConfig).where(
        ApiKeyConfig.provider == _LIVEKIT_PROVIDER, ApiKeyConfig.category == _LIVEKIT_CAT,
    ))).scalar_one_or_none()


def _livekit_extra(row) -> dict:
    if not row or not row.extra_config:
        return {}
    try:
        return json.loads(row.extra_config)
    except Exception:
        return {}


@router.get("/platform/livekit")
async def get_livekit(user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    """Masked LiveKit view. URL shown; API key masked; secret NEVER returned —
    only whether one is set."""
    from backend.config import settings
    row = await _get_livekit_row(db)
    extra = _livekit_extra(row)
    url = extra.get("url") or settings.livekit_url or ""
    secret_set = bool(extra.get("secret_enc")) or bool(settings.livekit_api_secret)
    return {
        "url": url,
        "api_key_masked": row.get_key_masked() if row else (
            (settings.livekit_api_key[:4] + "•" * 6 + settings.livekit_api_key[-4:]) if settings.livekit_api_key else ""
        ),
        "secret_set": secret_set,   # boolean only — the secret itself is write-only
    }


class LiveKitUpdate(BaseModel):
    url: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None


@router.put("/platform/livekit")
async def update_livekit(data: LiveKitUpdate, user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    from backend.security import encrypt_secret
    actor = getattr(user, "subject", None) or "superadmin"
    _rate_limit_key_ops(actor)

    # Pair-consistency warning: changing key or secret without the other is a
    # common way to silently break token signing.
    warning = None
    if (data.api_key and not data.api_secret) or (data.api_secret and not data.api_key):
        warning = ("You changed only one of API Key / API Secret. If they came as a pair from LiveKit, "
                   "update both — a mismatched key/secret silently breaks all room tokens.")

    row = await _get_livekit_row(db)
    if not row:
        row = ApiKeyConfig(id=str(uuid.uuid4()), provider=_LIVEKIT_PROVIDER, category=_LIVEKIT_CAT,
                           display_name="LiveKit", is_active=True)
        db.add(row)
    extra = _livekit_extra(row)

    if data.api_key:
        err = _validate_key_format("livekit", data.api_key)
        if err:
            raise HTTPException(status_code=422, detail=err)
        row.set_key(data.api_key.strip())
        _write_env_var("LIVEKIT_API_KEY", data.api_key.strip())
    if data.url is not None:
        extra["url"] = data.url.strip()
        _write_env_var("LIVEKIT_URL", data.url.strip())
    if data.api_secret:
        if len(data.api_secret.strip()) < 8:
            raise HTTPException(status_code=422, detail="LiveKit secret looks too short.")
        extra["secret_enc"] = encrypt_secret(data.api_secret.strip())  # Fernet at rest
        _write_env_var("LIVEKIT_API_SECRET", data.api_secret.strip())
    row.extra_config = json.dumps(extra)

    await db.commit()
    _invalidate_configured_cache()
    await _audit(db, actor, "livekit.update", target="livekit",
                 detail=f"fields={'url,' if data.url is not None else ''}{'key,' if data.api_key else ''}{'secret' if data.api_secret else ''}")
    return {"saved": True, "warning": warning}


@router.post("/platform/livekit/test")
async def test_livekit(user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    """Real connectivity check (list rooms) using the CURRENT stored values —
    reads the freshly-saved DB row (not stale in-process settings), so it works
    right after an update."""
    from backend.config import settings
    from backend.security import decrypt_secret
    row = await _get_livekit_row(db)
    extra = _livekit_extra(row)
    url = extra.get("url") or settings.livekit_url
    api_key = (row.get_key_raw() if row else None) or settings.livekit_api_key
    secret = decrypt_secret(extra["secret_enc"]) if extra.get("secret_enc") else settings.livekit_api_secret
    if not (url and api_key and secret):
        return {"ok": False, "detail": "LiveKit URL, API key, and secret must all be set."}
    try:
        import asyncio as _aio
        async with _lk.LiveKitAPI(url, api_key, secret) as client:
            res = await _aio.wait_for(client.room.list_rooms(_lk.ListRoomsRequest()), timeout=8)
            return {"ok": True, "detail": f"Connected ✓ ({len(res.rooms)} active room(s))"}
    except Exception as e:
        return {"ok": False, "detail": f"Connection failed: {str(e)[:150]}"}


# ── GET /platform/audit-logs — recent sensitive-action trail ──────────────────
@router.get("/platform/audit-logs")
async def get_audit_logs(limit: int = 50, user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    from backend.models.audit_log import AuditLog
    rows = (await db.execute(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(min(limit, 200))
    )).scalars().all()
    return [
        {"id": r.id, "actor": r.actor, "action": r.action, "target": r.target,
         "detail": r.detail, "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]

# ── PATCH /platform/keys/{key_id}/activate ───────────────────────────────────
@router.patch("/platform/keys/{key_id}/activate")
async def set_active_provider(key_id: str, user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    try:
        key = (await db.execute(select(ApiKeyConfig).where(ApiKeyConfig.id == key_id))).scalar_one_or_none()
        if not key:
            raise HTTPException(status_code=404, detail="Key not found")
        for k in (await db.execute(select(ApiKeyConfig).where(ApiKeyConfig.category == key.category))).scalars().all():
            k.is_active = (k.id == key_id)
        await db.commit()
        _invalidate_configured_cache()
        return {"activated": key_id, "category": key.category}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── GET /platform/keys/check/{category} ──────────────────────────────────────
@router.get("/platform/keys/check/{category}")
async def check_key(category: str, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
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
async def trigger_env_sync(user: SuperAdmin = None, db: AsyncSession = Depends(get_db)):
    try:
        count = await sync_keys_from_env(db)
        return {"synced": count, "message": f"Synced {count} key(s) from environment"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── POST /platform/providers/{provider}/fetch-models ─────────────────────────
class FetchModelsRequest(BaseModel):
    api_key: Optional[str] = None  # Override: test a key before saving


@router.post("/platform/providers/{provider}/fetch-models")
async def fetch_provider_models(
    provider: str,
    body: FetchModelsRequest = FetchModelsRequest(),
    user: CurrentUser = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Fetch available models from provider API.
    - Uses stored DB/env key by default.
    - If 'api_key' is provided in the request body, that key is used instead
      (allows testing a freshly pasted key before saving it).
    - Results are cached in the provider's extra_config for fast subsequent reads.
    """
    # Resolve key: body override > DB > env
    raw_key: str | None = body.api_key.strip() if body.api_key else None
    if not raw_key:
        raw_key = await _get_raw_key(provider, db)
    if not raw_key:
        from backend.config import settings as _s
        raw_key = getattr(_s, f"{provider}_api_key", None) or ""

    models: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            # ── LLM providers ──────────────────────────────────────────────────
            if provider == "gemini":
                if not raw_key:
                    raise HTTPException(400, "Gemini API key not configured")
                r = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    headers={"x-goog-api-key": raw_key}
                )
                r.raise_for_status()
                models = sorted([
                    m["name"].replace("models/", "")
                    for m in r.json().get("models", [])
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
                    if any(m["id"].startswith(p) for p in ("gpt-", "o1", "o3", "chatgpt", "o4"))
                ])

            elif provider == "anthropic":
                models = ANTHROPIC_MODELS  # No public list API

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
                # Filter to language models only (exclude audio/vision)
                models = sorted([
                    m["id"] for m in r.json().get("data", [])
                    if m.get("object") == "model"
                ])

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

            # ── TTS providers ──────────────────────────────────────────────────
            elif provider == "elevenlabs":
                if raw_key:
                    # Fetch live TTS model list from ElevenLabs /v1/models
                    r = await client.get(
                        "https://api.elevenlabs.io/v1/models",
                        headers={"xi-api-key": raw_key}
                    )
                    if r.status_code == 200:
                        all_models = r.json()
                        # Filter to TTS-capable models, sort newest first
                        models = [
                            m["model_id"] for m in all_models
                            if m.get("can_do_text_to_speech", False)
                        ]
                        if not models:
                            models = all_models[0:] and [m["model_id"] for m in all_models]
                    else:
                        logger.warning("ElevenLabs models fetch returned %s", r.status_code)
                if not models:
                    # Authoritative static fallback — newest models first
                    models = [
                        "eleven_v3", "eleven_flash_v2_5", "eleven_multilingual_v2",
                        "eleven_turbo_v2_5", "eleven_turbo_v2", "eleven_monolingual_v1",
                    ]

            elif provider in ("openai_tts", "openai-tts"):
                if raw_key:
                    # OpenAI TTS models — fetch from /v1/models and filter
                    r = await client.get(
                        "https://api.openai.com/v1/models",
                        headers={"Authorization": f"Bearer {raw_key}"}
                    )
                    if r.status_code == 200:
                        models = sorted([
                            m["id"] for m in r.json().get("data", [])
                            if any(m["id"].startswith(p) for p in ("tts-", "gpt-4o-mini-tts", "gpt-4o-audio"))
                        ])
                if not models:
                    models = OPENAI_TTS_MODELS

            elif provider == "sarvam":
                # Sarvam has no public model enumeration — use authoritative list
                # Detect category from query param if provided
                models = SARVAM_TTS_MODELS  # default to TTS; STT caller will send category

            # ── STT providers ──────────────────────────────────────────────────
            elif provider == "deepgram":
                # Deepgram has no unauthenticated model list API
                models = DEEPGRAM_STT_MODELS

            elif provider == "assemblyai":
                models = ASSEMBLYAI_STT_MODELS

            elif provider == "whisper":
                models = OPENAI_STT_MODELS

            else:
                raise HTTPException(400, f"Unknown provider: {provider}")

    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Failed to fetch models from %s: %s", provider, e)
        raise HTTPException(500, f"Failed to fetch models from {provider}: {str(e)}")

    # ── Cache in extra_config ──────────────────────────────────────────────────
    if models and not body.api_key:  # Don't cache when using a temp override key
        try:
            rec = (await db.execute(
                select(ApiKeyConfig).where(ApiKeyConfig.provider == provider)
            )).scalars().first()
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
        "source": "live" if raw_key else "static",
    }

# ── GET /platform/tts/voices/sarvam ──────────────────────────────────────────
@router.get("/platform/tts/voices/sarvam")
async def sarvam_voices(model: Optional[str] = Query(default=None), user: CurrentUser = None):
    """Return Sarvam voices, optionally filtered by model (e.g. bulbul:v3)."""
    from backend.routers.providers import SARVAM_VOICES as _SARVAM_V
    voices = _SARVAM_V
    if model:
        voices = [v for v in voices if v.get("model") == model]
    return {
        "provider": "sarvam",
        "model": model,
        "voices": [
            {
                "id": v["id"],
                "voice_id": v["id"],
                "name": v["name"],
                "gender": v["gender"],
                "language": v["language"],
                "language_code": v["language"],
                "model": v.get("model", ""),
                "description": v.get("description", ""),
            } for v in voices
        ]
    }

# ── GET /platform/tts/voices/{provider} ───────────────────────────────────────
@router.get("/platform/tts/voices/{provider}")
async def list_voices(
    provider: str,
    model: Optional[str] = Query(default=None),
    user: CurrentUser = None,
    db: AsyncSession = Depends(get_db)
):
    """Return voices for a TTS provider, filtered by model if provided."""
    if provider == "sarvam":
        return await sarvam_voices(model=model, user=user)

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
                        "voice_id": v["voice_id"],
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

    # Providers served from a static catalog (no public list API). Only surface
    # voices when a key is actually configured, so the library stays consistent
    # with the "configured providers only" rule.
    if provider in STATIC_TTS_VOICE_CATALOG:
        raw_key = await _get_raw_key(provider, db)
        return {
            "provider": provider,
            "has_key": bool(raw_key),
            "voices": STATIC_TTS_VOICE_CATALOG[provider] if raw_key else [],
        }

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
    model: Optional[str] = Query(default=None),
    input_preprocessing: bool = True,
    stability: Optional[float] = Query(default=None),
    similarity_boost: Optional[float] = Query(default=None),
    style: Optional[float] = Query(default=None),
    use_speaker_boost: Optional[bool] = Query(default=None),
    speed: Optional[float] = Query(default=None),
    user: CurrentUser = None,
    db: AsyncSession = Depends(get_db),
):
    # Friendly provider label for error messages — never Sarvam-only.
    _prov_meta = next((p for p in PROVIDERS["tts"] if p["id"] == provider), None)
    prov_label = _prov_meta["name"] if _prov_meta else provider

    if provider == "sarvam":
        api_key = os.getenv("SARVAM_API_KEY") or await _get_raw_key("sarvam", db)
        if not api_key:
            raise HTTPException(400, "Sarvam AI: no API key configured. Add SARVAM_API_KEY in AI Platform → Text-to-Speech.")

        # Reflect the actually-selected model (was previously hardcoded to
        # bulbul:v3 regardless of the Voice Model dropdown) so "Play Sample"
        # matches what the live pipeline will actually use.
        sarvam_model = model or "bulbul:v3"
        sarvam_payload = {
            "text": text,
            "target_language_code": language,
            "speaker": voice_id,
            "model": sarvam_model,
            "pace": pace,
            "speech_sample_rate": 22050,
            # /stream returns raw MP3 bytes directly (no base64 round-trip).
            "output_audio_codec": "mp3",
            "enable_preprocessing": input_preprocessing,
        }
        # bulbul:v2 is the only Sarvam model that accepts pitch/loudness — the
        # raw REST API errors if they're sent for v3/v3-beta, so only include
        # them for v2 (mirrors the guard already used in agent_test.py).
        if sarvam_model == "bulbul:v2":
            sarvam_payload["pitch"] = pitch
            sarvam_payload["loudness"] = loudness

        try:
            async with httpx.AsyncClient(timeout=_TTS_PREVIEW_TIMEOUT) as client:
                # ROOT-CAUSE FIX: the non-streaming /text-to-speech endpoint
                # intermittently hangs 12-30s+ (it synthesizes the whole clip,
                # base64-encodes it, and returns one large JSON blob). The
                # /text-to-speech/stream endpoint returns raw MP3 bytes in ~0.9s.
                response = await client.post(
                    "https://api.sarvam.ai/text-to-speech/stream",
                    headers={
                        "api-subscription-key": api_key,
                        "Content-Type": "application/json",
                    },
                    json=sarvam_payload,
                )
            if response.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Sarvam AI: {response.text[:200]}")
            if not response.content:
                raise HTTPException(status_code=502, detail="Sarvam AI: empty audio returned")
            return Response(
                content=response.content,
                media_type="audio/mpeg",
                headers={"Content-Disposition": "inline"},
            )
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail=f"Sarvam AI: synthesis timed out after {int(_TTS_PREVIEW_TIMEOUT)}s")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Sarvam AI: {str(e)[:200]}")

    # ── All other providers: resolve the configured key, then dispatch ──────────
    raw_key = await _get_raw_key(provider, db)
    if not raw_key:
        raise HTTPException(400, f"{prov_label}: no API key configured. Add it in AI Platform → Text-to-Speech.")

    try:
        async with httpx.AsyncClient(timeout=_TTS_PREVIEW_TIMEOUT) as client:
            if provider == "elevenlabs":
                # ── STRATEGY: Use preview_url first (free, zero characters) ──
                # Fetching the voice's preview_url costs 0 characters.
                # Only fall back to TTS generation if preview_url is unavailable.
                try:
                    voices_resp = await client.get(
                        "https://api.elevenlabs.io/v1/voices",
                        headers={"xi-api-key": raw_key},
                    )
                    if voices_resp.status_code == 200:
                        voices_data = voices_resp.json().get("voices", [])
                        voice_entry = next((v for v in voices_data if v.get("voice_id") == voice_id), None)
                        if voice_entry and voice_entry.get("preview_url"):
                            # Stream the pre-built preview MP3 — no characters consumed
                            preview_audio = await client.get(voice_entry["preview_url"])
                            if preview_audio.status_code == 200:
                                return Response(
                                    content=preview_audio.content,
                                    media_type="audio/mpeg",
                                    headers={"X-Preview-Source": "preview_url"},
                                )
                except Exception as preview_err:
                    logger.warning("ElevenLabs preview_url fetch failed, trying TTS generation: %s", preview_err)

                # ── FALLBACK: Generate TTS (uses characters) ──
                selected_model = model or "eleven_flash_v2_5"
                voice_settings = {
                    "stability": stability if stability is not None else 0.5,
                    "similarity_boost": similarity_boost if similarity_boost is not None else 0.75,
                }
                if style is not None:
                    voice_settings["style"] = style
                if use_speaker_boost is not None:
                    voice_settings["use_speaker_boost"] = use_speaker_boost
                if speed is not None:
                    # ElevenLabs only accepts speed in [0.7, 1.2]; the agent's
                    # slider goes 0.5-2.0, so clamp rather than let the API 400.
                    voice_settings["speed"] = min(max(speed, 0.7), 1.2)
                r = await client.post(
                    f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream",
                    headers={"xi-api-key": raw_key, "Content-Type": "application/json"},
                    json={"text": text, "model_id": selected_model, "voice_settings": voice_settings},
                )
                if r.status_code == 401:
                    raise HTTPException(
                        status_code=402,
                        detail="ElevenLabs character quota exhausted. Your free tier (10,000 chars/month) is used up. Upgrade at elevenlabs.io or wait for monthly reset."
                    )
                r.raise_for_status()
                return Response(content=r.content, media_type="audio/mpeg", headers={"X-Preview-Source": "tts_generated"})

            elif provider == "openai_tts":
                r = await client.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers={"Authorization": f"Bearer {raw_key}", "Content-Type": "application/json"},
                    json={"model": model or "tts-1", "input": text, "voice": voice_id},
                )
                r.raise_for_status()
                return Response(content=r.content, media_type="audio/mpeg")

            elif provider == "cartesia":
                # Cartesia TTS bytes API — returns raw audio directly.
                r = await client.post(
                    "https://api.cartesia.ai/tts/bytes",
                    headers={
                        "X-API-Key": raw_key,
                        "Cartesia-Version": "2024-11-13",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model_id": model or "sonic-2",
                        "transcript": text,
                        "voice": {"mode": "id", "id": voice_id},
                        "language": (language or "en-US").split("-")[0],
                        "output_format": {"container": "mp3", "sample_rate": 44100, "bit_rate": 128000},
                    },
                )
                r.raise_for_status()
                return Response(content=r.content, media_type="audio/mpeg")

            elif provider == "deepgram_aura":
                # Deepgram Aura — the voice model is passed as the `model` query param.
                aura_model = voice_id or model or "aura-2-thalia-en"
                r = await client.post(
                    f"https://api.deepgram.com/v1/speak?model={aura_model}",
                    headers={"Authorization": f"Token {raw_key}", "Content-Type": "application/json"},
                    json={"text": text},
                )
                r.raise_for_status()
                return Response(content=r.content, media_type="audio/mpeg")

            elif provider == "azure_tts":
                # Azure Neural TTS needs a region alongside the key. Region comes
                # from AZURE_SPEECH_REGION (env) since the key store holds one value.
                region = os.getenv("AZURE_SPEECH_REGION") or os.getenv("AZURE_REGION")
                if not region:
                    raise HTTPException(400, "Azure Neural TTS: set AZURE_SPEECH_REGION (e.g. 'centralindia') to enable preview.")
                lang_tag = language or "en-US"
                ssml = (
                    f"<speak version='1.0' xml:lang='{lang_tag}'>"
                    f"<voice xml:lang='{lang_tag}' name='{voice_id}'>{text}</voice></speak>"
                )
                r = await client.post(
                    f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
                    headers={
                        "Ocp-Apim-Subscription-Key": raw_key,
                        "Content-Type": "application/ssml+xml",
                        "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                    },
                    content=ssml.encode("utf-8"),
                )
                r.raise_for_status()
                return Response(content=r.content, media_type="audio/mpeg")

            elif provider == "playht":
                # PlayHT requires a user id in addition to the secret key.
                user_id = os.getenv("PLAYHT_USER_ID")
                if not user_id:
                    raise HTTPException(400, "PlayHT: set PLAYHT_USER_ID (from play.ht API access) to enable preview.")
                r = await client.post(
                    "https://api.play.ht/api/v2/tts/stream",
                    headers={
                        "Authorization": f"Bearer {raw_key}",
                        "X-USER-ID": user_id,
                        "Content-Type": "application/json",
                        "Accept": "audio/mpeg",
                    },
                    json={"text": text, "voice": voice_id, "voice_engine": model or "PlayDialog", "output_format": "mp3"},
                )
                r.raise_for_status()
                return Response(content=r.content, media_type="audio/mpeg")

            else:
                raise HTTPException(400, f"{prov_label}: TTS preview not supported for this provider.")

    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail=f"{prov_label}: synthesis timed out after {int(_TTS_PREVIEW_TIMEOUT)}s")
    except httpx.HTTPStatusError as e:
        body = ""
        try:
            body = e.response.text[:200]
        except Exception:
            pass
        raise HTTPException(status_code=502, detail=f"{prov_label}: {e.response.status_code} {body}".strip())
    except Exception as e:
        raise HTTPException(500, f"{prov_label}: preview failed — {str(e)[:200]}")


# ── POST /stt/transcribe ───────────────────────────────────────────────────────
@router.post("/stt/transcribe")
async def transcribe_audio(
    audio_file: UploadFile = File(...),
    language: str = "hi-IN",
    user: CurrentUser = None,
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
                "model": "saaras:v3",
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
    user: CurrentUser = None,
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

    # Fallback to PROVIDERS catalogue (category-aware if provided)
    if category and category in PROVIDERS:
        for p in PROVIDERS[category]:
            if p["id"] == provider:
                return {"provider": provider, "category": category, "models": p.get("models", []), "source": "static"}

    for cat_name, cat in PROVIDERS.items():
        for p in cat:
            if p["id"] == provider:
                return {"provider": provider, "category": cat_name, "models": p.get("models", []), "source": "static"}

    # ── Ultimate fallback: hardcoded authoritative lists ──────────────────────
    # For providers that don't appear in PROVIDERS catalogue but are known
    _STATIC_FALLBACKS: dict[str, list[str]] = {
        "sarvam":     SARVAM_TTS_MODELS if (not category or category == "tts") else SARVAM_STT_MODELS,
        "elevenlabs": ["eleven_v3", "eleven_flash_v2_5", "eleven_multilingual_v2", "eleven_turbo_v2_5", "eleven_turbo_v2"],
        "openai_tts": OPENAI_TTS_MODELS,
        "deepgram":   DEEPGRAM_STT_MODELS,
        "assemblyai": ASSEMBLYAI_STT_MODELS,
        "whisper":    OPENAI_STT_MODELS,
        "anthropic":  ANTHROPIC_MODELS,
    }
    if provider in _STATIC_FALLBACKS:
        return {"provider": provider, "category": category, "models": _STATIC_FALLBACKS[provider], "source": "static"}

    return {"provider": provider, "category": category, "models": [], "source": "unknown"}


# ── GET /platform/env-status — show which .env keys are configured ───────────
@router.get("/platform/env-status")
async def env_status(user: SuperAdmin = None):
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


# ── GET /platform/sarvam/languages ───────────────────────────────────────────
@router.get("/platform/sarvam/languages")
async def sarvam_languages(user: CurrentUser = None):
    """Returns all Sarvam-supported Indian languages with auto-detect info.
    Language codes are returned by the STT API in the `language_code` field.
    Default speakers are optimised for bulbul:v3 model.
    """
    return {
        "languages": [
            {"code": "hi-IN", "name": "Hindi",            "script": "\u0939\u093f\u0928\u094d\u0926\u0940",       "default_speaker": "meera"},
            {"code": "en-IN", "name": "English (India)",  "script": "English",        "default_speaker": "vian"},
            {"code": "ta-IN", "name": "Tamil",            "script": "\u0ba4\u0bae\u0bbf\u0bb4\u0bcd",       "default_speaker": "pavithra"},
            {"code": "te-IN", "name": "Telugu",           "script": "\u0c24\u0c46\u0c32\u0c41\u0c17\u0c41",       "default_speaker": "arvind"},
            {"code": "kn-IN", "name": "Kannada",          "script": "\u0c95\u0ca8\u0ccd\u0ca8\u0ca1",       "default_speaker": "karun"},
            {"code": "ml-IN", "name": "Malayalam",        "script": "\u0d2e\u0d32\u0d2f\u0d3e\u0d33\u0d02",     "default_speaker": "maya"},
            {"code": "mr-IN", "name": "Marathi",          "script": "\u092e\u0930\u093e\u0920\u0940",       "default_speaker": "amol"},
            {"code": "bn-IN", "name": "Bengali",          "script": "\u09ac\u09be\u0982\u09b2\u09be",       "default_speaker": "amartya"},
            {"code": "gu-IN", "name": "Gujarati",         "script": "\u0a97\u0ac1\u0a9c\u0ab0\u0abe\u0aa4\u0ac0",     "default_speaker": "neel"},
            {"code": "pa-IN", "name": "Punjabi",          "script": "\u0a2a\u0a70\u0a1c\u0a3e\u0a2c\u0a40",       "default_speaker": "arjun"},
            {"code": "or-IN", "name": "Odia",             "script": "\u0b13\u0b21\u0b3c\u0b3f\u0b06",       "default_speaker": "diya"},
            {"code": "unknown","name": "Auto-detect",     "script": "Auto",           "default_speaker": "meera"},
        ]
    }
