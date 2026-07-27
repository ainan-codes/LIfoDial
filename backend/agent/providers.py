"""
backend/agent/providers.py — STT/TTS key resolution + newly-added provider builders
for the live call pipeline (backend/agent/pipeline.py).

Why this exists: a key saved through the AI Platform dashboard is written to
the DB (ApiKeyConfig), but pipeline.py used to only read the server's static
env vars — so a dashboard-saved BYOK key never reached a real call. resolve_key()
below is the DB-first (falls back to env) resolver already used elsewhere
(backend/services/provider_status.py — the same one agents.py and the System
Health check use), so a key saved in the dashboard now takes effect on the
very next call, no redeploy/restart required.

To add a new STT/TTS provider once its pipecat-ai adapter is installed and its
API key exists in config.py:
  1. If the provider's id doesn't map 1:1 to a `<provider>_api_key` settings
     attribute (e.g. "whisper" uses the OpenAI key), add the alias to
     _SPECIAL_ATTR in backend/services/provider_status.py.
  2. Add a builder function here (see build_assemblyai_stt / build_cartesia_tts
     for the pattern) and one `elif` branch in pipeline.py's STT/TTS sections
     that calls it, resolving the key via resolve_key() first.
That's the entire surface area — nothing else in the pipeline needs to change.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.provider_status import resolve_provider_key

log = logging.getLogger(__name__)


class ProviderNotAvailable(Exception):
    """Raised when a selected provider's pipecat-ai adapter isn't installed."""


async def resolve_key(db: AsyncSession, provider: str) -> str:
    """Effective API key for `provider`: an active DB row saved via the AI
    Platform dashboard wins, otherwise the server's env/settings value.
    Never raises — returns "" if nothing is configured anywhere."""
    return (await resolve_provider_key(db, provider)) or ""


def _require_import(provider: str, pip_extra: str, import_error: Exception):
    raise ProviderNotAvailable(
        f"Provider '{provider}' is selected but its pipecat-ai adapter isn't "
        f'installed. Run: pip install "pipecat-ai[{pip_extra}]". '
        f"(underlying error: {import_error})"
    )


# ── AssemblyAI STT ────────────────────────────────────────────────────────────
def build_assemblyai_stt(api_key: str):
    """AssemblyAI's streaming v3 API (used by this pipecat service) auto-detects
    language — it takes no language hint at connect time, unlike Sarvam/Deepgram."""
    try:
        from pipecat.services.assemblyai.stt import AssemblyAISTTService
    except ImportError as e:
        _require_import("assemblyai", "assemblyai", e)
    return AssemblyAISTTService(api_key=api_key)


# ── Cartesia TTS ───────────────────────────────────────────────────────────────
def build_cartesia_tts(api_key: str, voice_id: str | None, model: str | None = None):
    """voice_id must be a real Cartesia voice id (from https://play.cartesia.ai) —
    there is no universal safe default the way Sarvam/ElevenLabs have one, so
    this passes through whatever the agent is configured with."""
    try:
        from pipecat.services.cartesia.tts import CartesiaTTSService
    except ImportError as e:
        _require_import("cartesia", "cartesia", e)
    return CartesiaTTSService(
        api_key=api_key,
        voice_id=voice_id,
        model=model or "sonic-2",
    )
