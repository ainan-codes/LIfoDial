"""
backend/services/gemini_catalog.py — the live list of Gemini models this product
can actually run a voice conversation on.

The Groq twin of this module (``groq_catalog.py``) filters on the vendor's own
per-model metadata and never probes. **That approach does not work for Gemini**,
and the reason is the whole design of this file.

Google's ``ListModels`` metadata cannot answer either question that matters
------------------------------------------------------------------------------
Every field Google returns is: ``name``, ``displayName``, ``version``,
``description``, ``inputTokenLimit``, ``outputTokenLimit``,
``supportedGenerationMethods``, ``temperature``, ``topP``, ``topK``,
``maxTemperature``, ``thinking``. Note what is absent: there is no modality field
of any kind. Verified against the live API on 2026-08-13:

1. **It cannot tell a text model from an image or speech model.**
   ``gemini-3-pro-image`` (which returns pictures) and ``gemini-flash-latest``
   (which returns words) have structurally identical entries — both list
   ``generateContent``, both have a large ``inputTokenLimit``, both set
   ``thinking``. ``gemini-2.5-flash-preview-tts`` likewise advertises
   ``generateContent``. Groq's ``input_modalities``/``output_modalities``, which
   is what lets the Groq module exclude Whisper and Orpheus from data rather than
   opinion, has no Gemini equivalent.

2. **It cannot tell a live model from a retired one.** ``gemini-2.5-flash`` and
   ``gemini-2.0-flash`` are both still LISTED, and both return
   ``404 "This model is no longer available to new users"`` when actually called.
   Those two ids were this repo's Gemini defaults until 2026-08-13, and because
   they list cleanly, nothing that merely enumerates models would ever have
   noticed. Gemini is first in ``resilience.PROVIDER_ORDER``, so the failover
   Groq rate limits depend on was pointing at a 404.

So this module PROBES
---------------------
Each candidate gets one real ``generateContent`` call, and is offered only if it
answers 200 **with a text part**. That single test settles both questions at once:
a retired model fails it (404), and an image or speech model fails it (the
response carries ``inlineData``, not ``text``).

This is still not a hardcoded list — no model name appears anywhere in this file,
exactly as in the Groq module. It is the same principle (ask the vendor, never
assert from memory) applied to a vendor whose metadata is too thin to ask
cheaply. The cost of the truth here is N HTTP calls instead of one.

The probe is bounded and cached: candidates are filtered on metadata FIRST (so
obvious non-starters never cost a call), probes run concurrently with a small cap,
each asks for a handful of tokens, and a successful sweep is reused for 15
minutes. Opening the agent editor does not re-probe.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

log = logging.getLogger(__name__)

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

#: Same threshold and same reasoning as groq_catalog.MIN_CONTEXT_WINDOW: the
#: system prompt alone is ~12,000 characters, and Indic scripts tokenize far
#: worse than Latin. Gemini's context windows are enormous, so in practice this
#: only ever excludes the small speech-oriented entries.
MIN_CONTEXT_WINDOW = 8192

#: Reused for 15 minutes, like the Groq catalogue. Longer would be defensible
#: given the probe cost, but a model being retired mid-day is exactly the failure
#: this module exists to catch, and 15 minutes bounds how long we would keep
#: offering one.
_CACHE_TTL_SECONDS = 15 * 60

#: Concurrent probes. Enough to keep the sweep to a few seconds for ~35
#: candidates, low enough not to look like abuse or trip a rate limit.
_PROBE_CONCURRENCY = 8

#: Big enough that a THINKING model still emits a visible token.
#:
#: This was 4, and that broke the sweep in a way worth recording: Gemini's
#: thinking models spend the output budget on hidden reasoning first, so
#: ``gemini-flash-latest`` answered 200 with ``finishReason: MAX_TOKENS`` and an
#: EMPTY parts list. Requiring a text part therefore rejected exactly the good
#: models and kept the odd non-thinking one — the filter was inverted.
#:
#: ``thinkingConfig: {thinkingBudget: 0}`` looks like the tidier fix and is not
#: available: the current Gemini 3.x models answer 400 to it.
_PROBE_MAX_TOKENS = 64

_cache: dict[str, Any] = {"models": None, "fetched_at": 0.0}


class GeminiModelsUnavailable(RuntimeError):
    """Gemini's model list could not be fetched.

    Distinct type for the same reason as the Groq twin: "no models available" and
    "we could not ask" look identical in a dropdown and must not.
    """


def _plausible(model: dict) -> tuple[bool, str]:
    """Cheap metadata pre-filter, to avoid probing obvious non-starters.

    Deliberately permissive. Google's metadata cannot prove a model is usable
    (see the module docstring), so this only rules OUT — the probe is what rules
    in.
    """
    methods = model.get("supportedGenerationMethods") or []
    if "generateContent" not in methods:
        return False, "does not support generateContent"

    try:
        ctx = int(model.get("inputTokenLimit") or 0)
    except (TypeError, ValueError):
        ctx = 0
    if ctx < MIN_CONTEXT_WINDOW:
        return False, (
            f"input token limit {ctx or 'unknown'} is below the "
            f"{MIN_CONTEXT_WINDOW} needed to hold this product's system prompt"
        )
    return True, ""


def _shape(model: dict) -> dict:
    """The subset the UI needs, in the SAME shape groq_catalog returns.

    Identical keys on purpose: the dropdown, the config-options endpoint and the
    frontend all consume one shape regardless of which vendor produced it.
    """
    name = (model.get("name") or "").replace("models/", "")
    try:
        ctx = int(model.get("inputTokenLimit") or 0)
    except (TypeError, ValueError):
        ctx = 0
    return {
        "id": name,
        "name": model.get("displayName") or name,
        "owned_by": "google",
        "context_window": ctx,
        "max_completion_tokens": model.get("outputTokenLimit"),
        # Google's own flag. Surfaced, NOT filtered on, for the same reason as
        # Groq's: a reasoning model's visible thinking would be spoken aloud by
        # TTS, and that trade-off belongs to whoever is choosing.
        "reasoning": bool(model.get("thinking")),
        # Gemini supports function calling broadly and Google does not enumerate
        # it per model. Nothing in backend/agent/ registers an LLM tool anyway
        # (booking is regex + injected system messages), so this is unused.
        "supports_tools": True,
    }


async def _list_raw(api_key: str) -> list[dict]:
    """Every model Google lists for this key, following pagination."""
    import httpx

    out: list[dict] = []
    token: str | None = None
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            params = {"key": api_key, "pageSize": 200}
            if token:
                params["pageToken"] = token
            try:
                r = await client.get(f"{GEMINI_BASE}/models", params=params)
            except Exception as exc:  # noqa: BLE001
                raise GeminiModelsUnavailable(
                    f"Could not reach Google to list Gemini models: {exc}"
                ) from exc

            if r.status_code in (401, 403):
                raise GeminiModelsUnavailable(
                    f"Google rejected the configured Gemini API key (HTTP "
                    f"{r.status_code}), so the model list cannot be fetched."
                )
            if r.status_code != 200:
                raise GeminiModelsUnavailable(
                    f"Google answered HTTP {r.status_code} when listing models: "
                    f"{r.text[:200]}"
                )
            try:
                body = r.json()
            except Exception as exc:  # noqa: BLE001
                raise GeminiModelsUnavailable(
                    f"Google returned an unreadable model list: {exc}"
                ) from exc

            out.extend(body.get("models") or [])
            token = body.get("nextPageToken")
            if not token:
                return out


async def _probe(client, api_key: str, model_id: str) -> tuple[bool, str]:
    """One real call. Usable only if it answers 200 and produces text.

    ``responseModalities: ["TEXT"]`` does the heavy lifting — it makes each model
    reject itself with a reason of Google's own writing rather than ours.
    Measured across the live catalogue on 2026-08-13:

        gemini-flash-latest            200, text          -> offered
        gemini-2.5-flash-preview-tts   400 "response modalities (TEXT) is not
                                            supported"    -> excluded
        deep-research-preview-*        400 "only supports Interactions API"
        gemini-2.5-flash (retired)     404 "no longer available to new users"
        lyria-3-pro-preview (music)    500
        gemini-3-pro-image             200 but no text part

    That is four distinct classes of unusable model excluded without this file
    ever naming one.

    Honest limitation: a model that CAN emit text but is meant for something else
    (``gemini-2.5-flash-image``, the robotics previews) answers 200 with text and
    is offered. It would genuinely hold a conversation, so this is a question of
    taste rather than breakage — and the alternative is a maintained name list,
    which is what this module exists to avoid.
    """
    try:
        r = await client.post(
            f"{GEMINI_BASE}/models/{model_id}:generateContent",
            params={"key": api_key},
            json={
                "contents": [{"parts": [{"text": "Reply with: OK"}]}],
                "generationConfig": {
                    "maxOutputTokens": _PROBE_MAX_TOKENS,
                    "responseModalities": ["TEXT"],
                },
            },
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"probe failed: {exc}"

    if r.status_code != 200:
        detail = ""
        try:
            detail = (r.json().get("error") or {}).get("message", "")[:120]
        except Exception:  # noqa: BLE001
            detail = r.text[:120]
        return False, f"HTTP {r.status_code}: {detail}"

    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
    except Exception:  # noqa: BLE001
        # A 200 with no candidate is not a usable conversational model. This also
        # covers a response blocked outright by safety settings.
        return False, "returned no usable candidate"

    if not any(isinstance(p, dict) and isinstance(p.get("text"), str) for p in parts):
        # Image and speech models land here: they answer 200, but with
        # inlineData rather than text.
        return False, "produced no text output (not a conversational model)"
    return True, ""


async def fetch_models(api_key: str, *, force: bool = False) -> list[dict]:
    """Gemini's live, currently-usable conversational models.

    Raises ``GeminiModelsUnavailable`` on any failure — no key, network error,
    non-200 listing, unparseable body, or a sweep in which nothing was usable.
    Never returns a fallback list.
    """
    if not (api_key or "").strip():
        raise GeminiModelsUnavailable(
            "No Gemini API key is configured, so the model list cannot be "
            "fetched. Set GEMINI_API_KEY in the environment."
        )

    if not force:
        cached = _cache.get("models")
        if cached and (time.monotonic() - float(_cache["fetched_at"])) < _CACHE_TTL_SECONDS:
            return cached  # type: ignore[return-value]

    import httpx

    raw = await _list_raw(api_key)

    candidates: list[dict] = []
    excluded: list[str] = []
    for m in raw:
        if not isinstance(m, dict) or not m.get("name"):
            continue
        ok, why = _plausible(m)
        (candidates if ok else excluded).append(m if ok else f"{m['name']} ({why})")  # type: ignore[arg-type]

    usable: list[dict] = []
    sem = asyncio.Semaphore(_PROBE_CONCURRENCY)

    async with httpx.AsyncClient(timeout=30) as client:
        async def one(m: dict) -> None:
            shaped = _shape(m)
            async with sem:
                ok, why = await _probe(client, api_key, shaped["id"])
            if ok:
                usable.append(shaped)
            else:
                excluded.append(f"{shaped['id']} ({why})")

        await asyncio.gather(*(one(m) for m in candidates))

    if not usable:
        raise GeminiModelsUnavailable(
            f"Google listed {len(raw)} model(s) but none could hold a "
            f"conversation. Excluded: {'; '.join(excluded[:10]) or 'none'}."
        )

    usable.sort(key=lambda m: (-m["context_window"], m["id"]))

    log.info(
        "Gemini model list: %d usable, %d excluded — %s",
        len(usable), len(excluded), "; ".join(excluded[:10]),
    )

    _cache["models"] = usable
    _cache["fetched_at"] = time.monotonic()
    return usable


#: Same tri-state contract as groq_catalog. UNKNOWN must never be collapsed into
#: DEAD: refusing on UNKNOWN would turn a Google outage into "no agent on this
#: platform can be edited".
LIVE, DEAD, UNKNOWN = "live", "dead", "unknown"


async def check_model(api_key: str, model: str) -> str:
    """Is ``model`` one Google is serving right now? LIVE, DEAD or UNKNOWN."""
    wanted = (model or "").strip()
    if not wanted:
        return UNKNOWN

    if wanted in cached_ids():
        return LIVE

    try:
        # force=True for the same reason as the Groq twin: a plain fetch would
        # re-read the cache we just missed in.
        live = {m["id"] for m in await fetch_models(api_key or "", force=True)}
    except GeminiModelsUnavailable:
        return UNKNOWN

    return LIVE if wanted in live else DEAD


def cached_ids() -> set[str]:
    """Model ids from the last successful sweep, or an empty set."""
    return {m["id"] for m in (_cache.get("models") or [])}
