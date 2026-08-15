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

2. MID-CALL recovery, then never-silence (`ResilienceProcessor`):
   If the chosen provider (LLM or TTS) throws AFTER the call is underway
   (429, timeout, network blip), Pipecat emits an ErrorFrame. This processor
   catches it and, in order of preference:

   a. On a Groq RATE LIMIT, switches the live LLM service onto the next Groq model
      that still has budget and re-runs the same turn, so the caller gets a real
      answer to the question they actually asked.
   b. Otherwise (or once the models are exhausted) speaks a short reassurance
      phrase in the agent's language via the same proven TTSSpeakFrame→TTS path the
      greeting uses — so a failed turn is never dead air. Debounced + capped so a
      hard-down provider can't loop.

Why (a) exists at all, given the note that used to be here
----------------------------------------------------------
This module previously stated that per-turn failover was impossible: "Pipecat's
static pipeline can't swap a service mid-stream — documented limitation, tracked for
Batch 2". That is true of REPLACING a service object, and it is the wrong conclusion
— the model is not the service. Verified against the installed pipecat-ai 1.5.0:

* ``BaseOpenAILLMService`` reads the model from ``self._settings.model`` on every
  request, and ``LLMUpdateSettingsFrame(delta=Settings(model=...))`` is the
  supported way to change it (``LLMService.process_frame`` routes it to
  ``_update_settings``, which reports ``updated settings fields: {'model'}`` and
  leaves every unset field at NOT_GIVEN).
* ``LLMUserAggregator`` handles ``LLMRunFrame`` by calling ``push_context_frame()``,
  which re-runs inference on the context as it already stands — i.e. re-answers the
  caller's last question, with nothing to rebuild.

So the failed turn can genuinely be retried on another model without touching the
pipeline. This matters because the SETUP-time probe in (1) cannot catch an exhausted
token budget: listing models costs no tokens, so ``GET /v1/models`` answers 200 for
a key whose daily budget is spent. Before this, every voice call started on the
exhausted model and answered every single question with the apology phrase.

Retrying is restricted to rate limits on purpose. A 429 is rejected BEFORE any
tokens are generated, so re-running produces one reply rather than a duplicated or
half-spoken one; a mid-stream network failure has no such guarantee.

The model chain, the per-model cooldowns and the evidence for both live in
backend/services/llm_failover.py, shared with the chat path so the two cannot drift.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    LLMRunFrame,
    LLMUpdateSettingsFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from backend.config import settings
from backend.services import llm_failover

log = logging.getLogger(__name__)

# Preference order mirrors the test path (agent_test.py). Anthropic/DeepSeek are
# reachable via OpenAI-compatible calls; only providers with an installed Pipecat
# LLM service are buildable, so the buildable set is gemini/groq/openai/deepseek.
PROVIDER_ORDER = ["gemini", "groq", "openai", "deepseek"]

PROVIDER_DEFAULT_MODEL = {
    # An ALIAS, not a pinned version. Verified against the live API on
    # 2026-08-13: "gemini-2.5-flash" and "gemini-2.0-flash" — the two values this
    # file and pipeline.py used to carry — both now return
    #   404 "This model is no longer available to new users"
    # while still appearing in the ListModels response, so nothing that merely
    # enumerates models would have caught it. Gemini is FIRST in PROVIDER_ORDER,
    # so this is the model the failover reaches for when Groq rate-limits, and a
    # 404 there means the fallback silently has no fallback.
    #
    # Google retires dated Gemini snapshots on a rolling basis; a pinned id here
    # is a scheduled outage. The "-latest" aliases track whatever is current and
    # were confirmed generating on the same date. Same reasoning as the no-
    # hardcoded-model-list rule in services/groq_catalog.py.
    "gemini": "gemini-flash-latest",
    "groq": "llama-3.3-70b-versatile",
    "openai": "gpt-4o-mini",
    "deepseek": "deepseek-chat",
}


#: A reasoning model spends its token allowance on reasoning BEFORE it emits a
#: single visible character, so the app's ordinary spoken-reply budget (150-300
#: tokens, services/token_budget.response_token_budget) buys it nothing to say.
#: Measured 2026-08-10: at 150-250 max_tokens openai/gpt-oss-* returns EMPTY
#: content every time.
#:
#: The chat path has compensated for this since that measurement
#: (routers/agent_test.py::call_groq). The VOICE path never did — and voice is
#: where it matters most, because openai/gpt-oss-120b is the FIRST fallback the
#: rate-limit switch reaches. Live on 2026-08-15 the primary hit its daily budget,
#: voice moved to gpt-oss-120b exactly as designed, and the caller then got
#: TTS "400: Text must contain at least one character from the allowed languages"
#: — the signature of an empty completion reaching the speech service. The
#: failover was working and the model it failed over to could not speak.
GROQ_REASONING_MIN_TOKENS = 800


def groq_reasoning_settings(model: str, max_tokens: int) -> dict:
    """The Groq request knobs this model needs, as Settings kwargs.

    Returns ``max_tokens`` always, plus ``extra={"reasoning_effort": ...}`` for a
    reasoning model. ``extra`` is pipecat's passthrough for model-specific request
    params — merged into the request body at
    pipecat/services/openai/base_llm.py:361 (``params.update(self._settings.extra)``).

    The effort VALUE is per family and not free-form; ``low`` is a hard 400 on
    qwen3. See services/llm_failover.GROQ_REASONING_EFFORT.
    """
    effort = llm_failover.reasoning_effort_for(model)
    if effort is None:
        return {"max_tokens": max_tokens}
    return {
        "max_tokens": max(max_tokens, GROQ_REASONING_MIN_TOKENS),
        "extra": {"reasoning_effort": effort},
    }


def _settings_max_tokens(llm, default: int = 150) -> int:
    """The reply-token budget an already-built LLM service was configured with.

    Best-effort and never raises: pipecat stores a sentinel (`_NotGiven`) rather
    than None for unset fields, and this runs on a live call where being wrong
    must cost a slightly different budget, never an exception.
    """
    try:
        value = getattr(getattr(llm, "_settings", None), "max_tokens", None)
        return int(value) if isinstance(value, int) and value > 0 else default
    except Exception:  # noqa: BLE001
        return default


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


def _skip_exhausted_model(provider: str, key: str, model: str) -> tuple[str, str, str]:
    """Swap in a Groq model that still has budget, if this one has none.

    Applied to the selection on the way OUT, after the reachability probe and after
    the selection cache, because the two answer different questions and only one of
    them can be cached: "is the key reachable?" is stable for minutes, while "does
    this model have budget right now?" is exactly what changed. Caching the swap
    would pin a call to the fallback model for the cache's full TTL after the
    primary's budget had already refilled.

    This is the setup-time half of the fix. The probe cannot detect an exhausted
    budget — ``GET /v1/models`` costs no tokens and answers 200 either way — so
    without this, every new call starts on a model that will 429 on the caller's
    first question, and the ResilienceProcessor has to recover a turn that never
    needed to fail.
    """
    if provider != "groq":
        return provider, key, model
    chosen, reason = llm_failover.preferred_model(model)
    if reason:
        log.warning("[RESILIENCE] %s", reason)
    return provider, key, chosen


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
        return _skip_exhausted_model(*cached[1])
    # Auto-sanitize decommissioned models
    if configured_model in {"mixtral-8x7b-32768", "llama3-8b-8192", "llama3-70b-8192", "gemma-7b-it"}:
        configured_model = "llama-3.3-70b-versatile"

    preferred = (
        configured_provider if configured_provider in PROVIDER_ORDER
        else _provider_from_model(configured_model) or "gemini"
    )

    # The operator's CHOICE is honoured whenever it is usable at all, and
    # "usable" means it has a key — not that a 3-second list-models GET happened
    # to answer just now.
    #
    # The probe used to gate the configured provider too, and that made every
    # transient blip — a slow DNS answer, a 1.5s connect timeout, one 503 from
    # the vendor's edge — silently move the call to a DIFFERENT VENDOR and, worse,
    # discard the configured model with it (the `provider == preferred` condition
    # below). The operator picked a model in the dashboard and the call ran on
    # something else, with only a log line to say so.
    #
    # It also could not protect against the failure that actually happens: a
    # list-models GET returns 200 for a key whose token budget is fully spent
    # (documented at _probe). So the probe was buying a false negative and no
    # true positive. Real exhaustion is handled where it is detectable — the 429
    # path in llm_failover / _try_another_model.
    preferred_key = await _resolve_key(preferred)
    if preferred_key.strip():
        model = configured_model or PROVIDER_DEFAULT_MODEL.get(preferred, "")
        log.info(
            "[RESILIENCE] using the configured LLM provider '%s' (model=%s) — no probe gate.",
            preferred, model,
        )
        _selection_cache[cache_key] = (time.monotonic(), (preferred, preferred_key, model))
        return _skip_exhausted_model(preferred, preferred_key, model)

    log.warning(
        "[RESILIENCE] the configured LLM provider '%s' has NO key configured — this is a "
        "setup gap, not a transient failure, so falling back to another provider.", preferred,
    )
    order: list[str] = [p for p in PROVIDER_ORDER if p != preferred]
    for provider in order:
        key = await _resolve_key(provider)
        if await _probe(provider, key):
            model = PROVIDER_DEFAULT_MODEL[provider]
            log.warning(
                "[RESILIENCE] configured LLM provider '%s' unavailable — falling back to '%s' (model=%s)",
                preferred, provider, model,
            )
            # The UNSWAPPED selection is what gets cached — see _skip_exhausted_model
            # for why the budget check must not be cached alongside the probe.
            _selection_cache[cache_key] = (time.monotonic(), (provider, key, model))
            return _skip_exhausted_model(provider, key, model)

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
                model=model, system_instruction=system_prompt, temperature=temperature,
                **groq_reasoning_settings(model, max_tokens)),
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
#
# These must RESOLVE the turn, not defer it. Every one of them used to promise a
# wait — "one moment please", "kripya thodi der rukiye", "ஒரு நிமிடம்
# காத்திருங்கள்", "ഒരു നിമിഷം കാത്തിരിക്കൂ" — and nothing schedules what they
# promise: _speak_fallback is only ever reached AFTER _try_another_model has
# declined or run out of models, so at that point nothing further is coming for
# this turn at all. The caller waits, hears nothing, and says "हेलो? हेलो?" into an
# open line. That is the identical defect this codebase already bans in MODEL
# output through action_tag.promises_followup — it was simply sitting in our own
# constants instead, where no test looked.
#
# So each phrase now apologises and hands the turn back to the caller, whose next
# utterance is the only thing that can actually produce a reply. Enforced by
# test_no_outbound_constant_promises_a_followup, which runs this whole table
# through promises_followup — and note that detector had to be widened first: it
# had patterns for English, Hindi and Marathi only, so it caught exactly one of
# the eight offending phrases.
#
# hi-IN is Devanagari now, not romanized Latin. The same TTS already speaks
# Devanagari for every phrase in agent/spoken_fallback.py, and romanized text is
# invisible to the Devanagari half of the guard.
_FALLBACK_PHRASES = {
    "hi-IN": "माफ़ कीजिए, एक तकनीकी दिक्कत आ गई। कृपया अपनी बात दोबारा कहिए।",
    "en-IN": "Sorry, I hit a technical problem just then. Could you say that again?",
    "ta-IN": "மன்னிக்கவும், ஒரு தொழில்நுட்பச் சிக்கல் ஏற்பட்டது. மீண்டும் சொல்ல முடியுமா?",
    "te-IN": "క్షమించండి, ఒక సాంకేతిక సమస్య వచ్చింది. మళ్ళీ చెప్పగలరా?",
    "kn-IN": "ಕ್ಷಮಿಸಿ, ಒಂದು ತಾಂತ್ರಿಕ ತೊಂದರೆ ಆಯಿತು. ದಯವಿಟ್ಟು ಇನ್ನೊಮ್ಮೆ ಹೇಳಿ.",
    "ml-IN": "ക്ഷമിക്കണം, ഒരു സാങ്കേതിക പ്രശ്നം ഉണ്ടായി. ഒന്നു കൂടി പറയാമോ?",
    "mr-IN": "क्षमस्व, एक तांत्रिक अडचण आली. कृपया पुन्हा सांगाल का?",
    "bn-IN": "দুঃখিত, একটি প্রযুক্তিগত সমস্যা হয়েছে। আবার বলবেন কি?",
}
_DEFAULT_FALLBACK = _FALLBACK_PHRASES["en-IN"]


def fallback_phrase(language: str) -> str:
    return _FALLBACK_PHRASES.get(language, _DEFAULT_FALLBACK)


# ── Default end-of-call phrase (agent's language) ─────────────────────────────
# Spoken by speak_and_end_call() (call_logger_processor.py) via a bare
# TTSSpeakFrame — it never passes through the LLM, so unlike every other reply
# it cannot pick up the call's language on its own. Before this, every call in
# every language ended with the literal English default below; a clinic that
# sets its own custom end_call_message (AgentConfig.end_call_message) is
# unaffected — this is only the fallback for clinics that never configured one.
_END_CALL_PHRASES = {
    "hi-IN": "Hamein call karne ke liye dhanyavaad. Namaste!",
    "en-IN": "Thank you for calling. Goodbye!",
    "ta-IN": "அழைத்ததற்கு நன்றி. போய் வருகிறேன்!",
    "te-IN": "కాల్ చేసినందుకు ధన్యవాదాలు. వీడ్కోలు!",
    "kn-IN": "ಕರೆ ಮಾಡಿದ್ದಕ್ಕೆ ಧನ್ಯವಾದಗಳು. ವಿದಾಯ!",
    "ml-IN": "വിളിച്ചതിന് നന്ദി. വീണ്ടും കാണാം!",
    "mr-IN": "कॉल केल्याबद्दल धन्यवाद. नमस्कार!",
    "bn-IN": "কল করার জন্য ধন্যবাদ। বিদায়!",
}
_DEFAULT_END_CALL_MESSAGE = _END_CALL_PHRASES["en-IN"]


def default_end_call_message(language: str) -> str:
    return _END_CALL_PHRASES.get(language, _DEFAULT_END_CALL_MESSAGE)


class ResilienceProcessor(FrameProcessor):
    """Transparent processor placed at the END of the pipeline. Watches every
    frame flowing downstream; on an ErrorFrame (LLM or TTS provider failure) it
    first tries to RECOVER the turn on another Groq model, and only if that is
    impossible speaks a reassurance phrase so the caller never hears silence.

    Debounced (min gap between fallback utterances) and capped (max per call) so
    a fully-down provider can't drive an infinite speak→fail→speak loop.

    ``llm``/``llm_provider``/``llm_model`` are what make the model swap possible; pass
    them from pipeline.py. Omitted (as in the older two-argument call, and in tests
    that only exercise the spoken fallback), the processor keeps its original
    speak-only behaviour rather than failing to construct.
    """

    def __init__(
        self,
        language: str,
        min_gap_seconds: float = 8.0,
        max_fallbacks: int = 4,
        *,
        llm=None,
        llm_provider: str = "",
        llm_model: str = "",
        max_model_switches: int | None = None,
        call_logger=None,
    ) -> None:
        super().__init__()
        self._phrase = fallback_phrase(language)
        self._task = None                       # set by pipeline.py after PipelineTask creation
        self._last_spoken_ts: float = 0.0
        self._min_gap = min_gap_seconds
        self._count = 0
        self._max = max_fallbacks
        self._llm = llm
        self._llm_provider = (llm_provider or "").strip().lower()
        self._llm_model = (llm_model or "").strip()
        # Bounded so a systematically failing chain cannot walk the caller through
        # every model on the account.
        #
        # Derived from the chain rather than pinned at 2: every Groq model has
        # its OWN free-tier token budget, so the chain is the product's daily
        # capacity and a cap below its length throws away models that still have
        # budget. Measured live 2026-08-15 — llama-3.3-70b returned
        # "tokens per day (TPD): Limit 100000, Used 99547" and the caller's turn
        # died two switches later with usable models left untried.
        if max_model_switches is None:
            max_model_switches = max(2, len(llm_failover.GROQ_MODEL_CHAIN) - 1)
        self._switches_left = max_model_switches
        #: Holds the silence watchdog's clock while a switch + re-ask is in
        #: flight. That is a whole extra LLM round trip the caller did nothing to
        #: cause, and it used to run with the clock live — see _try_another_model.
        self._call_logger = call_logger
        #: The spoken-reply token budget this call was built with, so a model
        #: switch can re-derive the target model's own budget from it rather than
        #: inheriting whatever the previous model was set to. Read off the live
        #: service so it stays right without pipeline.py having to pass it.
        self._base_max_tokens = _settings_max_tokens(llm)

    def bind_task(self, task) -> None:
        self._task = task

    def _is_llm_rate_limit(self, err: str) -> bool:
        """Is this ErrorFrame a Groq LLM rate limit, as opposed to a TTS/STT one?

        ErrorFrame carries a message, not its origin, and this pipeline's TTS
        provider can 429 too. Switching the LLM's model because Sarvam throttled the
        VOICE would be a change that cannot possibly help, made at the worst moment.
        So known speech-vendor markers veto the swap, and anything else that reads
        as a rate limit is attributed to the LLM — which is the only rate-limited
        component this can actually do something about.

        Being wrong in the cautious direction costs nothing: the spoken fallback
        below is exactly the old behaviour.
        """
        if not llm_failover.is_rate_limit_error(err):
            return False
        if self._llm is None or self._llm_provider != "groq":
            return False
        low = (err or "").lower()
        speech_markers = ("sarvam", "deepgram", "elevenlabs", "cartesia", "whisper",
                          "tts", "stt", "transcri", "speech")
        return not any(m in low for m in speech_markers)

    async def _try_another_model(self, err: str) -> bool:
        """Move the live LLM onto a model with budget and re-ask. True if retried."""
        if self._switches_left <= 0:
            log.error(
                "[RESILIENCE] model-switch budget spent this call — not switching again.",
            )
            return False
        if self._task is None:
            log.error("[RESILIENCE] no task bound — cannot switch model.")
            return False

        llm_failover.mark_rate_limited(self._llm_model, err)
        alt = llm_failover.next_available_model(self._llm_model)
        if alt is None:
            log.error(
                "[RESILIENCE] every Groq model in the chain is rate limited — "
                "falling back to speech.",
            )
            return False

        # The switch plus the re-ask is a full extra LLM round trip. Hold the
        # silence watchdog's clock across it: the caller is waiting on the
        # agent's own recovery, which is the definition of work in progress, and
        # is exactly what action_in_progress exists to exclude. Cleared by the
        # re-ask's own LLMFullResponseStartFrame in voice_action._set_busy, and
        # backstopped by that processor's busy watchdog if the frame never comes.
        if self._call_logger is not None:
            self._call_logger.action_in_progress = True

        try:
            # The service's OWN Settings type, so the delta validates against the
            # same schema it was built with, and `service=` targets this LLM
            # explicitly rather than relying on it being the only one in the pipeline.
            #
            # The delta carries the reasoning knobs, not just the model id. Sending
            # `model` alone leaves the previous model's max_tokens and (absent)
            # reasoning_effort in place, and every model this chain falls TO is a
            # reasoning model — so a bare-model switch lands on a service configured
            # to return empty completions. That is the 2026-08-15 failure: the switch
            # to gpt-oss-120b succeeded and the caller heard nothing, because the
            # reply had no visible characters for TTS to speak.
            delta = type(self._llm._settings)(
                model=alt, **groq_reasoning_settings(alt, self._base_max_tokens),
            )
            await self._task.queue_frames([
                LLMUpdateSettingsFrame(delta=delta, service=self._llm),
                # Re-runs inference on the context as it stands, i.e. re-answers the
                # question the caller already asked. Safe specifically because a 429
                # is refused before any tokens are generated, so there is no partial
                # reply to duplicate.
                LLMRunFrame(),
            ])
        except Exception as e:  # noqa: BLE001
            # Nothing is coming — release the clock rather than leaving the
            # watchdog switched off for the rest of the call.
            if self._call_logger is not None:
                self._call_logger.action_in_progress = False
            log.error("[RESILIENCE] failed to switch model %s → %s: %s", self._llm_model, alt, e)
            return False

        self._switches_left -= 1
        log.warning(
            "[RESILIENCE] LLM rate limited on %s — switched to %s and re-asking this turn "
            "(%d switch(es) left).",
            self._llm_model, alt, self._switches_left,
        )
        self._llm_model = alt
        return True

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
            await self._handle_error(frame)
        # Always pass frames through — never block the pipeline.
        await self.push_frame(frame, direction)

    async def _handle_error(self, frame: ErrorFrame) -> None:
        """Recover the turn if we can; speak rather than go silent if we cannot."""
        err = str(getattr(frame, "error", None) or frame)

        # Retrying on a model with budget beats apologising: the caller asked a real
        # question and can still get a real answer. Only when no model is left does
        # this fall through to the spoken fallback.
        if self._is_llm_rate_limit(err) and await self._try_another_model(err):
            return

        await self._speak_fallback(frame)

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
