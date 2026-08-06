"""
backend/agent/resilience.py — provider failover + never-silence for the REAL
(Pipecat) call path (audit FIX 2).

Two guarantees, split by where the failure happens:

1. SETUP-TIME provider selection (`select_llm_provider`):
   Before the pipeline is built, probe the configured LLM providers in order and
   pick the first one whose key is actually reachable. This is what handles the
   real production failure — a dead/leaked/misconfigured primary key (the Gemini
   key is currently revoked). The whole call then runs on a healthy provider;
   the caller never hits a dead primary. Probes are cheap HTTP GETs that run once
   at call setup — NOT in the per-turn hot loop.

2. MID-CALL never-silence (`ResilienceProcessor`):
   If the chosen provider (LLM or TTS) throws AFTER the call is underway
   (429, timeout, network blip), Pipecat emits an ErrorFrame. This processor
   catches it and speaks a short reassurance phrase in the agent's language via
   the same proven TTSSpeakFrame→TTS path the greeting uses — so a failed turn is
   never dead air. Debounced + capped so a hard-down provider can't loop.

Reuses the test path's provider preference order (groq→openai→...); it does not
re-implement per-turn streaming failover (Pipecat's static pipeline can't swap a
service mid-stream — documented limitation, tracked for Batch 2).
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from pipecat.frames.frames import ErrorFrame, Frame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from backend.config import settings

log = logging.getLogger(__name__)

# Preference order mirrors the test path (agent_test.py). Anthropic/DeepSeek are
# reachable via OpenAI-compatible calls; only providers with an installed Pipecat
# LLM service are buildable, so the buildable set is gemini/groq/openai/deepseek.
PROVIDER_ORDER = ["gemini", "groq", "openai", "deepseek"]

PROVIDER_DEFAULT_MODEL = {
    "gemini": "gemini-2.5-flash",
    "groq": "llama-3.3-70b-versatile",
    "openai": "gpt-4o-mini",
    "deepseek": "deepseek-chat",
}


def _provider_from_model(model: str) -> Optional[str]:
    m = (model or "").lower()
    if m.startswith("gemini"):
        return "gemini"
    if m.startswith(("llama", "mixtral", "gemma", "compound", "deepseek-r1", "moonshard", "whisper")):
        return "groq"
    if m.startswith(("gpt-", "o1", "o3", "chatgpt")):
        return "openai"
    if m.startswith("deepseek"):
        return "deepseek"
    return None


async def _resolve_key(provider: str) -> str:
    """DB-first key for an LLM provider (a key saved via the AI Platform
    dashboard reaches the very next call, no redeploy/restart needed) —
    falling back to the static env/settings value if the DB has nothing
    configured, or is unreachable. Delegates to backend/agent/providers.py's
    resolve_key(), the same DB-first/env-fallback resolver the STT/TTS side of
    the pipeline uses, so there's one implementation of that precedence."""
    from backend.db import AsyncSessionLocal
    from backend.agent.providers import resolve_key as _shared_resolve_key

    try:
        async with AsyncSessionLocal() as db:
            return await _shared_resolve_key(db, provider, category="llm")
    except Exception as e:
        log.warning("[RESILIENCE] DB key lookup failed for %s (using env fallback): %s", provider, e)
        from backend.services.provider_status import _env_key
        return _env_key(provider) or ""


async def _resolve_custom_provider(provider: str) -> tuple[str, str] | None:
    """(api_key, base_url) for a custom OpenAI-compatible LLM provider — one
    not in PROVIDER_ORDER, registered via the AI Platform dashboard's
    "Add Custom Provider" (category="llm", provider=<whatever id was chosen>,
    extra_config={"base_url": "..."}). Returns None if that provider has no
    key or no base_url configured — callers treat that as "not set up",
    not as a transient failure."""
    from backend.db import AsyncSessionLocal
    from backend.services.provider_status import resolve_custom_llm_endpoint

    try:
        async with AsyncSessionLocal() as db:
            return await resolve_custom_llm_endpoint(db, provider)
    except Exception as e:
        log.warning("[RESILIENCE] custom LLM provider lookup failed for %s: %s", provider, e)
        return None


async def _probe(provider: str, key: str, base_url: str | None = None) -> bool:
    """Cheap reachability probe (list-models). True iff the key is live.

    `base_url` is used for a custom OpenAI-compatible provider (not one of the
    4 known ones) — same list-models convention, against its own endpoint."""
    if not key.strip():
        return False
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=1.5, read=3.0)) as c:
            if provider == "gemini":
                r = await c.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}")
            elif provider == "groq":
                r = await c.get("https://api.groq.com/openai/v1/models",
                                headers={"Authorization": f"Bearer {key}"})
            elif provider == "openai":
                r = await c.get("https://api.openai.com/v1/models",
                                headers={"Authorization": f"Bearer {key}"})
            elif provider == "deepseek":
                r = await c.get("https://api.deepseek.com/models",
                                headers={"Authorization": f"Bearer {key}"})
            elif base_url:
                r = await c.get(f"{base_url.rstrip('/')}/models",
                                headers={"Authorization": f"Bearer {key}"})
            else:
                return False
        healthy = r.status_code < 400
        if not healthy:
            log.warning("[RESILIENCE] provider probe %s -> HTTP %s (skipping)", provider, r.status_code)
        return healthy
    except Exception as e:
        log.warning("[RESILIENCE] provider probe %s failed: %s (skipping)", provider, str(e)[:100])
        return False


# Setup-time probe results, cached per configured model for the life of the
# worker process (jobs run as threads in one process, so this survives calls).
#
# The probe is a real HTTP GET against the winning provider, and it used to run on
# EVERY call setup — the caller waits through it before hearing the greeting. A
# short TTL keeps the resilience property that made the probe worth having (a key
# revoked mid-day is still noticed within TTL, and an in-call failure is caught by
# ResilienceProcessor regardless) while making the common case free.
_SELECTION_TTL_SECS = 300.0
_selection_cache: dict[str, tuple[float, tuple[str, str, str]]] = {}


def reset_llm_selection_cache() -> None:
    """Drop cached probe results (tests, or to force a re-probe)."""
    _selection_cache.clear()


async def select_llm_provider(agent_config: dict) -> tuple[str, str, str]:
    """
    Return (provider, api_key, model) for the first reachable provider.

    Order: the agent's configured provider first (so a working configured choice
    is honored), then the remaining providers in PROVIDER_ORDER. The configured
    model is kept only when its own provider wins; otherwise the fallback
    provider's default model is used.

    The result is cached for _SELECTION_TTL_SECS so repeat calls skip the network
    probe entirely.
    """
    configured_model = agent_config.get("llm_model") or ""
    configured_provider = (agent_config.get("llm_provider") or "").strip()

    # Explicit custom provider (not one of the 4 known ones) — a deliberately
    # chosen endpoint, so it deliberately bypasses _selection_cache entirely
    # (that cache is keyed by provider+model, but a custom endpoint's health
    # can change between calls just like the standard pool's, and probing it
    # is one cheap local DB read plus one HTTP GET, not worth caching).
    if configured_provider and configured_provider not in PROVIDER_ORDER:
        custom = await _resolve_custom_provider(configured_provider)
        if custom is not None:
            key, base_url = custom
            model = configured_model or "gpt-3.5-turbo"
            if await _probe(configured_provider, key, base_url=base_url):
                log.info("[RESILIENCE] using custom LLM provider '%s' (model=%s)", configured_provider, model)
                return configured_provider, key, model
            log.warning(
                "[RESILIENCE] custom LLM provider '%s' is configured but unreachable — "
                "falling back to the standard provider pool.", configured_provider,
            )
        else:
            log.warning(
                "[RESILIENCE] custom LLM provider '%s' has no key/base_url configured — "
                "falling back to the standard provider pool.", configured_provider,
            )
        # Fall through to the standard pool below, exactly as if no provider
        # preference had been set at all.

    cache_key = f"{configured_provider}::{configured_model}"
    cached = _selection_cache.get(cache_key)
    if cached and (time.monotonic() - cached[0]) < _SELECTION_TTL_SECS:
        provider, _key, model = cached[1]
        log.info(
            "[RESILIENCE] using cached LLM selection provider=%s model=%s (no probe)",
            provider, model,
        )
        return cached[1]
    # Auto-sanitize decommissioned models
    if configured_model in {"mixtral-8x7b-32768", "llama3-8b-8192", "llama3-70b-8192", "gemma-7b-it"}:
        configured_model = "llama-3.3-70b-versatile"

    preferred = (
        configured_provider if configured_provider in PROVIDER_ORDER
        else _provider_from_model(configured_model) or "gemini"
    )

    order: list[str] = [preferred] + [p for p in PROVIDER_ORDER if p != preferred]
    for provider in order:
        key = await _resolve_key(provider)
        if await _probe(provider, key):
            model = configured_model if provider == preferred and configured_model else PROVIDER_DEFAULT_MODEL[provider]
            if provider != preferred:
                log.warning(
                    "[RESILIENCE] configured LLM provider '%s' unavailable — falling back to '%s' (model=%s)",
                    preferred, provider, model,
                )
            else:
                log.info("[RESILIENCE] LLM provider '%s' healthy (model=%s)", provider, model)
            _selection_cache[cache_key] = (time.monotonic(), (provider, key, model))
            return provider, key, model

    raise RuntimeError(
        f"No reachable LLM provider among {order}. Checked keys for each; all failed a "
        "list-models probe. Set at least one valid provider key (GEMINI/GROQ/OPENAI/DEEPSEEK)."
    )


async def build_llm(provider: str, api_key: str, model: str, system_prompt: str, agent_config: dict):
    """Instantiate the Pipecat LLM service for the selected provider.

    All services share Settings(system_instruction/temperature/max_tokens),
    so configuration is uniform. DeepSeek uses the OpenAI service against
    DeepSeek's OpenAI-compatible base URL — any other provider not in the 4
    known ones is treated the same way: a custom OpenAI-compatible endpoint,
    with its base_url read from that provider's ApiKeyConfig.extra_config
    (set via the AI Platform dashboard's "Add Custom Provider").
    """
    temperature = float(agent_config.get("llm_temperature", 0.3))

    # Language-aware, because a token is not a fixed amount of speech. The
    # configured value is an ENGLISH-EQUIVALENT budget; Malayalam and Kannada cost
    # ~7.6x and ~9x more tokens for the same sentence under Llama's tokenizer, so
    # the flat cap that suits Hindi cut their replies off mid-word. Measurements and
    # reasoning: backend/services/token_budget.py.
    #
    # Applied HERE deliberately — this is the single point where max_tokens reaches
    # every provider, so Gemini, OpenAI and DeepSeek get the same treatment as Groq.
    # (That also answers "is this Groq-specific?": the cap was ours, not Groq's.)
    from backend.services.token_budget import response_token_budget

    max_tokens = response_token_budget(
        agent_config.get("max_response_tokens"),
        agent_config.get("language") or agent_config.get("tts_language"),
    )

    # model is passed via Settings (not the deprecated `model=` kwarg) so the
    # newer pipecat services don't emit a DeprecationWarning and settings win.
    if provider == "gemini":
        from pipecat.services.google.llm import GoogleLLMService
        return GoogleLLMService(
            api_key=api_key, system_instruction=system_prompt,
            settings=GoogleLLMService.Settings(model=model, temperature=temperature, max_tokens=max_tokens),
        )
    if provider == "groq":
        from pipecat.services.groq.llm import GroqLLMService
        return GroqLLMService(
            api_key=api_key,
            settings=GroqLLMService.Settings(
                model=model, system_instruction=system_prompt, temperature=temperature, max_tokens=max_tokens),
        )
    if provider in ("openai", "deepseek"):
        from pipecat.services.openai.llm import OpenAILLMService
        kwargs = dict(
            api_key=api_key,
            settings=OpenAILLMService.Settings(
                model=model, system_instruction=system_prompt, temperature=temperature, max_tokens=max_tokens),
        )
        if provider == "deepseek":
            kwargs["base_url"] = "https://api.deepseek.com/v1"
        return OpenAILLMService(**kwargs)

    # Custom provider — resolved again here (cheap local DB read) rather than
    # threaded through select_llm_provider's return value, so that function's
    # (provider, key, model) signature — and the tests that destructure it —
    # stay unchanged. The freshly-resolved key (not the possibly-stale `api_key`
    # argument select_llm_provider returned earlier) is what's actually used,
    # so a key rotated between selection and build takes effect immediately.
    custom = await _resolve_custom_provider(provider)
    if custom is not None:
        custom_key, base_url = custom
        from pipecat.services.openai.llm import OpenAILLMService
        return OpenAILLMService(
            api_key=custom_key or api_key, base_url=base_url,
            settings=OpenAILLMService.Settings(
                model=model, system_instruction=system_prompt, temperature=temperature, max_tokens=max_tokens),
        )

    raise ValueError(f"Unbuildable LLM provider: {provider}")


# ── Spoken fallback phrases (short; agent's language) ─────────────────────────
_FALLBACK_PHRASES = {
    "hi-IN": "Ek pal ke liye kuch takneeki dikkat aa rahi hai, kripya thodi der rukiye.",
    "en-IN": "I'm having a little trouble right now, one moment please.",
    "ta-IN": "சிறிது தொழில்நுட்பச் சிக்கல் உள்ளது, ஒரு நிமிடம் காத்திருங்கள்.",
    "te-IN": "కొంచెం సాంకేతిక సమస్య వస్తోంది, ఒక్క క్షణం ఆగండి.",
    "kn-IN": "ಸ್ವಲ್ಪ ತಾಂತ್ರಿಕ ತೊಂದರೆ ಇದೆ, ಒಂದು ಕ್ಷಣ ನಿಲ್ಲಿ.",
    "ml-IN": "ചെറിയ ഒരു സാങ്കേതിക പ്രശ്നം ഉണ്ട്, ഒരു നിമിഷം കാത്തിരിക്കൂ.",
    "mr-IN": "थोडी तांत्रिक अडचण येत आहे, कृपया एक क्षण थांबा.",
    "bn-IN": "একটু প্রযুক্তিগত সমস্যা হচ্ছে, একটু অপেক্ষা করুন।",
}
_DEFAULT_FALLBACK = _FALLBACK_PHRASES["en-IN"]


def fallback_phrase(language: str) -> str:
    return _FALLBACK_PHRASES.get(language, _DEFAULT_FALLBACK)


class ResilienceProcessor(FrameProcessor):
    """Transparent processor placed at the END of the pipeline. Watches every
    frame flowing downstream; on an ErrorFrame (LLM or TTS provider failure) it
    speaks a reassurance phrase so the caller never hears silence.

    Debounced (min gap between fallback utterances) and capped (max per call) so
    a fully-down provider can't drive an infinite speak→fail→speak loop.
    """

    def __init__(self, language: str, min_gap_seconds: float = 8.0, max_fallbacks: int = 4) -> None:
        super().__init__()
        self._phrase = fallback_phrase(language)
        self._task = None                       # set by pipeline.py after PipelineTask creation
        self._last_spoken_ts: float = 0.0
        self._min_gap = min_gap_seconds
        self._count = 0
        self._max = max_fallbacks

    def bind_task(self, task) -> None:
        self._task = task

    def set_language(self, language: str) -> None:
        """Re-target the fallback phrase after a mid-call language switch.

        Called by LanguageSwitchProcessor: if the caller moved to Hindi, an
        error must not be apologised for in English.
        """
        self._phrase = fallback_phrase(language)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        # REQUIRED first (pipecat 1.5): handle system frames + mark started.
        await super().process_frame(frame, direction)
        if isinstance(frame, ErrorFrame):
            await self._speak_fallback(frame)
        # Always pass frames through — never block the pipeline.
        await self.push_frame(frame, direction)

    async def _speak_fallback(self, frame: ErrorFrame) -> None:
        now = time.time()
        err = getattr(frame, "error", None) or str(frame)
        log.error("[RESILIENCE] provider ErrorFrame mid-call: %s", str(err)[:160])

        if self._task is None:
            log.error("[RESILIENCE] no task bound — cannot speak fallback (would be silence).")
            return
        if self._count >= self._max:
            log.error("[RESILIENCE] fallback cap (%d) reached — not speaking again this call.", self._max)
            return
        if now - self._last_spoken_ts < self._min_gap:
            return  # debounce: a burst of ErrorFrames yields one spoken phrase

        self._last_spoken_ts = now
        self._count += 1
        try:
            # Same mechanism the first-message greeting uses: a TTSSpeakFrame
            # injected at the source is synthesized straight by TTS. (A bare
            # TextFrame is NOT — it only gets flushed as part of an LLM response
            # turn, which is exactly the thing that just failed.)
            await self._task.queue_frames([TTSSpeakFrame(self._phrase, append_to_context=False)])
            log.info("[RESILIENCE] spoke fallback phrase (#%d) instead of silence.", self._count)
        except Exception as e:
            log.error("[RESILIENCE] failed to queue fallback phrase: %s", e)
