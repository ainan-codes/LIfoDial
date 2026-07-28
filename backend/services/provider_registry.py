"""
backend/services/provider_registry.py — the single source of truth for which
providers the live call pipeline can ACTUALLY build.

Why this exists
---------------
Three independent lists used to decide whether a provider "works":

  1. `PROVIDERS` in backend/routers/platform.py — the catalog the AI Platform UI
     lists (deliberately aspirational: it includes providers we'd like to支持).
  2. `ApiKeyConfig` rows — whether a key has been saved.
  3. The `if/elif` chains in backend/agent/pipeline.py and
     backend/agent/resilience.py — the only place that can really construct a
     service object.

Nothing joined them, and every UI surface treated "a key is saved" as "this
provider works". The consequences were all real, and all silent:

  * Selecting a TTS provider with no build branch (playht / azure_tts /
    deepgram_aura) fell through to the Sarvam `else:` and raised
    ``KeyError: 'sarvam'`` inside the job — the agent never joined the room and
    the caller heard dead air with nothing useful in the logs.
  * Selecting google_stt / azure_stt resolved a key successfully (so the
    deaf-agent guard passed), then also fell through to Sarvam — transcribing
    with a provider the dashboard never showed, or running the call completely
    deaf when no Sarvam key existed.
  * Selecting an LLM whose name couldn't be inferred from the model string
    (anthropic / mistral / ollama) silently ran Gemini instead.

This module is the join. It is deliberately free of any pipecat import so that
routers (which run in the web process) and the pipeline (which runs in the agent
worker) can share exactly one definition.

Adding a provider
-----------------
1. Install its pipecat extra in requirements.agent.txt.
2. Add a builder in backend/agent/providers.py.
3. Add the `elif` branch in backend/agent/pipeline.py (or resilience.py for LLM).
4. Move its id from UNSUPPORTED to the BUILDABLE_* set below.
Until step 4, the UI marks it unavailable and the API refuses to save it — which
is the entire point.
"""
from __future__ import annotations

# ── Providers with a real build branch ────────────────────────────────────────
# Verified against backend/agent/pipeline.py and backend/agent/resilience.py.

#: LLM — backend/agent/resilience.py::build_llm. Any id NOT in this set is
#: treated as a custom OpenAI-compatible endpoint and requires a base_url in its
#: ApiKeyConfig.extra_config; without one it cannot be built.
BUILDABLE_LLM = frozenset({"gemini", "groq", "openai", "deepseek"})

#: STT — the branch chain in backend/agent/pipeline.py.
BUILDABLE_STT = frozenset({"sarvam", "deepgram", "openai", "whisper", "elevenlabs", "assemblyai"})

#: TTS — the branch chain in backend/agent/pipeline.py.
BUILDABLE_TTS = frozenset({"sarvam", "elevenlabs", "openai_tts", "cartesia"})

BUILDABLE_BY_CATEGORY: dict[str, frozenset[str]] = {
    "llm": BUILDABLE_LLM,
    "stt": BUILDABLE_STT,
    "tts": BUILDABLE_TTS,
}

#: Fallback used when a configured provider cannot be built. Sarvam is the
#: `else:` branch in both chains, so it is the only honest answer here.
FALLBACK_BY_CATEGORY: dict[str, str] = {"stt": "sarvam", "tts": "sarvam", "llm": "gemini"}


# ── Providers in the catalog that CANNOT currently be built ───────────────────
# Kept explicit (rather than just "not in the buildable set") so the UI and the
# API can explain *why* instead of silently rejecting. Reasons verified against
# the installed pipecat-ai 1.5.0 and requirements.agent.txt.
UNSUPPORTED: dict[tuple[str, str], str] = {
    ("llm", "anthropic"): "The anthropic SDK is not installed in the agent worker.",
    ("llm", "mistral"): "The mistralai SDK is not installed in the agent worker.",
    ("llm", "cerebras"): "No Cerebras branch exists in the call pipeline yet.",
    ("llm", "ollama"): "Ollama needs a self-hosted base URL; add it as a custom provider instead.",
    ("stt", "google_stt"): "No Google STT branch exists in the call pipeline yet.",
    ("stt", "azure_stt"): "The azure-cognitiveservices SDK is not installed in the agent worker.",
    ("tts", "azure_tts"): "The azure-cognitiveservices SDK is not installed in the agent worker.",
    ("tts", "playht"): "pipecat-ai 1.5.0 ships no PlayHT service; it cannot be used for live calls.",
    ("tts", "deepgram_aura"): "No Deepgram Aura TTS branch exists in the call pipeline yet.",
    ("tts", "resemble"): "Resemble is voice-cloning only; it has no live TTS branch.",
}


def is_buildable(category: str, provider: str, *, has_base_url: bool = False) -> bool:
    """Can the live pipeline construct this provider for this category?

    ``has_base_url`` matters only for LLM: an unknown id with a base_url in its
    ApiKeyConfig.extra_config is a valid custom OpenAI-compatible endpoint.
    """
    provider = (provider or "").strip()
    if not provider:
        return False
    category = (category or "").strip().lower()

    buildable = BUILDABLE_BY_CATEGORY.get(category)
    if buildable is None:
        # Categories with no pipeline branch at all (telephony, his, voice_clone)
        # are not validated here — they are not part of the call pipeline.
        return True
    if provider in buildable:
        return True
    if category == "llm" and has_base_url:
        return True
    return False


def unsupported_reason(category: str, provider: str) -> str:
    """Human-readable explanation for a provider that cannot be built."""
    category = (category or "").strip().lower()
    provider = (provider or "").strip()
    known = UNSUPPORTED.get((category, provider))
    if known:
        return known
    if category == "llm":
        return (
            f"'{provider}' is not a built-in LLM provider. Register it as a custom "
            "OpenAI-compatible provider with a base_url to use it."
        )
    buildable = sorted(BUILDABLE_BY_CATEGORY.get(category, ()))
    return (
        f"'{provider}' has no {category.upper()} implementation in the call pipeline. "
        f"Supported: {', '.join(buildable)}."
    )


def validate_or_raise(category: str, provider: str | None, *, has_base_url: bool = False) -> None:
    """Reject an unbuildable provider at WRITE time.

    The whole point is to fail here — where a human is looking at a form and can
    pick something else — instead of at 3am inside a live call, where the same
    mistake is either a crash or a silent substitution nobody notices.

    Raises fastapi.HTTPException(422). No-ops when ``provider`` is None so it can
    be called unconditionally against optional PATCH fields.
    """
    if provider is None or not str(provider).strip():
        return
    if is_buildable(category, provider, has_base_url=has_base_url):
        return

    from fastapi import HTTPException

    raise HTTPException(
        status_code=422,
        detail=(
            f"{category.upper()} provider '{provider}' cannot be used for live calls. "
            f"{unsupported_reason(category, provider)}"
        ),
    )
