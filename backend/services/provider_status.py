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
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.api_key_config import ApiKeyConfig

# Providers whose settings attribute / env var don't follow the <provider>_api_key
# convention.
_SPECIAL_ATTR = {
    "vobiz": ("vobiz_account_sid", "VOBIZ_ACCOUNT_SID"),
    "oxzygen": ("oxzygen_api_key", "OXZYGEN_API_KEY"),
}


def _env_key(provider: str) -> str | None:
    """The env/settings key for a provider (no DB), or None if unset."""
    attr, env_name = _SPECIAL_ATTR.get(
        provider, (f"{provider}_api_key", f"{provider.upper()}_API_KEY")
    )
    val = getattr(settings, attr, "") or os.getenv(env_name, "") or ""
    val = val.strip()
    return val or None


async def resolve_provider_key(session: AsyncSession, provider: str) -> str | None:
    """Effective key for a provider: active DB ApiKeyConfig row first, then env.

    Returns the raw key string or None. Never raises — if the DB is unreachable it
    falls back to the env value so the health check can still run.
    """
    try:
        result = await session.execute(
            select(ApiKeyConfig)
            .where(
                ApiKeyConfig.provider == provider,
                ApiKeyConfig.is_active == True,  # noqa: E712
            )
            .limit(1)
        )
        cfg = result.scalars().first()
        if cfg and cfg.api_key_enc:
            raw = cfg.get_key_raw()
            if raw and raw.strip():
                return raw.strip()
    except Exception:
        pass
    return _env_key(provider)


async def is_provider_configured(session: AsyncSession, provider: str) -> bool:
    """True if a usable key exists in either store (DB-first, then env)."""
    return bool(await resolve_provider_key(session, provider))
