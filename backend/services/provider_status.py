"""
backend/services/provider_status.py — single source of truth for provider keys.

Audit P3: "System Health" and "AI Platform" disagreed about Gemini because they
read two different stores:
  • System Health probed the process env var (settings.gemini_api_key) only.
  • AI Platform read the DB row (api_key_configs.api_key_enc).
A key saved through the AI Platform UI is written to the DB (and .env on disk),
but does NOT update the already-running process env — and on Render GEMINI_API_KEY
is sync:false. So the DB had a key (AI Platform → ACTIVE) while the process env was
empty (System Health → "Set GEMINI_API_KEY in env").

`resolve_provider_key` is the union both must use: an active DB ApiKeyConfig row
with a stored key wins, otherwise the env/settings value. This is exactly what the
agent runtime uses to place a call (see agents.py::_resolve_llm_key, which now
delegates here), so "configured" here means "usable" — not merely "present in one
of two stores". The live-reachability probe in /admin/health-status stays as a
separate signal layered on top of the key this resolves.
"""
import json
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.api_key_config import ApiKeyConfig

# Providers whose settings attribute / env var don't follow the <provider>_api_key
# convention — mostly STT/TTS catalog ids (backend/routers/platform.py PROVIDERS)
# that share a key with a differently-named provider ("whisper" and "openai_tts"
# both use the OpenAI key; "azure_stt"/"azure_tts" share Azure Speech; "deepgram_aura"
# shares the Deepgram key). Without these, resolve_provider_key("whisper") would look
# for a nonexistent WHISPER_API_KEY env var / settings.whisper_api_key attribute and
# silently report "not configured" even when the real key is set.
_SPECIAL_ATTR = {
    "vobiz": ("vobiz_account_sid", "VOBIZ_ACCOUNT_SID"),
    "oxzygen": ("oxzygen_api_key", "OXZYGEN_API_KEY"),
    "whisper": ("openai_api_key", "OPENAI_API_KEY"),
    "openai_tts": ("openai_api_key", "OPENAI_API_KEY"),
    "azure_stt": ("azure_speech_key", "AZURE_SPEECH_KEY"),
    "azure_tts": ("azure_speech_key", "AZURE_SPEECH_KEY"),
    "deepgram_aura": ("deepgram_api_key", "DEEPGRAM_API_KEY"),
    "google_stt": ("google_speech_api_key", "GOOGLE_SPEECH_API_KEY"),
}


def _env_key(provider: str) -> str | None:
    """The env/settings key for a provider (no DB), or None if unset."""
    attr, env_name = _SPECIAL_ATTR.get(
        provider, (f"{provider}_api_key", f"{provider.upper()}_API_KEY")
    )
    val = getattr(settings, attr, "") or os.getenv(env_name, "") or ""
    val = val.strip()
    return val or None


async def resolve_provider_key(session: AsyncSession, provider: str, category: str | None = None) -> str | None:
    """Effective key for a provider: active DB ApiKeyConfig row first, then env.

    `category` ("llm" | "stt" | "tts" | ...) disambiguates a provider id that can
    be independently configured under more than one category (e.g. a custom
    provider named "elevenlabs" registered as an LLM endpoint vs. the real
    ElevenLabs TTS key) — pass it whenever the caller cares about ONE specific
    category. Left optional (unfiltered) for callers that intentionally want
    "is this provider id configured anywhere" (e.g. the System Health probe).

    Returns the raw key string or None. Never raises — if the DB is unreachable it
    falls back to the env value so the health check can still run.
    """
    try:
        conditions = [
            ApiKeyConfig.provider == provider,
            ApiKeyConfig.is_active == True,  # noqa: E712
        ]
        if category is not None:
            conditions.append(ApiKeyConfig.category == category)
        result = await session.execute(
            select(ApiKeyConfig).where(*conditions).limit(1)
        )
        cfg = result.scalars().first()
        if cfg and cfg.api_key_enc:
            raw = cfg.get_key_raw()
            if raw and raw.strip():
                return raw.strip()
    except Exception:
        pass
    return _env_key(provider)


async def is_provider_configured(session: AsyncSession, provider: str, category: str | None = None) -> bool:
    """True if a usable key exists in either store (DB-first, then env)."""
    return bool(await resolve_provider_key(session, provider, category=category))


async def resolve_custom_llm_endpoint(
    session: AsyncSession, provider: str
) -> tuple[str, str] | None:
    """(api_key, base_url) for a custom OpenAI-compatible LLM provider, or None.

    A provider id that isn't one of the built-in four is still legitimate if it
    was registered through the AI Platform's "Add Custom Provider" with a
    base_url in extra_config. Returns None when it has no key or no base_url —
    callers read that as "not set up", not as a transient failure.

    Lives HERE rather than in backend/agent/resilience.py because the API needs
    it to validate an agent save, and resilience.py imports pipecat, which is only
    installed on the agent worker. That import turned every PATCH /agents/{id}
    carrying an llm_provider into `500: No module named 'pipecat'` — a save that
    the dashboard could only report as "failed to save".
    """
    try:
        result = await session.execute(
            select(ApiKeyConfig).where(
                ApiKeyConfig.provider == provider,
                ApiKeyConfig.category == "llm",
                ApiKeyConfig.is_active == True,  # noqa: E712
            )
        )
        row = result.scalars().first()
        if not row:
            return None
        key = row.get_key_raw()
        base_url = (parse_extra_config(row.extra_config).get("base_url") or "").strip()
        if not key or not base_url:
            return None
        return key, base_url
    except Exception:
        return None


def parse_extra_config(raw: str | None) -> dict:
    """Safely parse an ApiKeyConfig.extra_config JSON blob (base_url, model,
    etc.) — malformed or missing JSON never raises, just yields {}."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}
