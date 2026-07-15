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
   the same proven TextFrame→TTS path the greeting uses — so a failed turn is
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

from pipecat.frames.frames import ErrorFrame, Frame, TextFrame
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
    if m.startswith(("llama", "mixtral", "gemma", "compound", "deepseek-r1")):
        return "groq"
    if m.startswith(("gpt-", "o1", "o3", "chatgpt")):
        return "openai"
    if m.startswith("deepseek"):
        return "deepseek"
    return None


def _key_for(provider: str) -> str:
    return {
        "gemini": settings.gemini_api_key,
        "groq": settings.groq_api_key,
        "openai": settings.openai_api_key,
        "deepseek": settings.deepseek_api_key,
    }.get(provider, "") or ""


async def _probe(provider: str, key: str) -> bool:
    """Cheap reachability probe (list-models). True iff the key is live."""
    if not key.strip():
        return False
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
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
            else:
                return False
        healthy = r.status_code < 400
        if not healthy:
            log.warning("[RESILIENCE] provider probe %s -> HTTP %s (skipping)", provider, r.status_code)
        return healthy
    except Exception as e:
        log.warning("[RESILIENCE] provider probe %s failed: %s (skipping)", provider, str(e)[:100])
        return False


async def select_llm_provider(agent_config: dict) -> tuple[str, str, str]:
    """
    Return (provider, api_key, model) for the first reachable provider.

    Order: the agent's configured provider first (so a working configured choice
    is honored), then the remaining providers in PROVIDER_ORDER. The configured
    model is kept only when its own provider wins; otherwise the fallback
    provider's default model is used.

    Raises RuntimeError if NO provider is reachable — the caller decides whether
    that's fatal (it should be: a call with no LLM can't function).
    """
    configured_model = agent_config.get("llm_model") or ""
    preferred = _provider_from_model(configured_model) or "gemini"

    order: list[str] = [preferred] + [p for p in PROVIDER_ORDER if p != preferred]
    for provider in order:
        key = _key_for(provider)
        if await _probe(provider, key):
            model = configured_model if provider == preferred and configured_model else PROVIDER_DEFAULT_MODEL[provider]
            if provider != preferred:
                log.warning(
                    "[RESILIENCE] configured LLM provider '%s' unavailable — falling back to '%s' (model=%s)",
                    preferred, provider, model,
                )
            else:
                log.info("[RESILIENCE] LLM provider '%s' healthy (model=%s)", provider, model)
            return provider, key, model

    raise RuntimeError(
        f"No reachable LLM provider among {order}. Checked keys for each; all failed a "
        "list-models probe. Set at least one valid provider key (GEMINI/GROQ/OPENAI/DEEPSEEK)."
    )


def build_llm(provider: str, api_key: str, model: str, system_prompt: str, agent_config: dict):
    """Instantiate the Pipecat LLM service for the selected provider.

    All three services share Settings(system_instruction/temperature/max_tokens),
    so configuration is uniform. DeepSeek uses the OpenAI service against
    DeepSeek's OpenAI-compatible base URL.
    """
    temperature = float(agent_config.get("llm_temperature", 0.3))
    max_tokens = int(agent_config.get("max_response_tokens", 120))

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
            # Same mechanism the first-message greeting uses: a TextFrame injected
            # at the source is synthesized straight by TTS.
            await self._task.queue_frames([TextFrame(self._phrase)])
            log.info("[RESILIENCE] spoke fallback phrase (#%d) instead of silence.", self._count)
        except Exception as e:
            log.error("[RESILIENCE] failed to queue fallback phrase: %s", e)
