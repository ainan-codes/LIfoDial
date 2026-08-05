"""
backend/services/groq_catalog.py — the live list of Groq models this product can
actually run a voice conversation on.

Why this module exists
----------------------
The LLM **provider** is locked to Groq (see backend/services/agent_defaults.py):
nothing about a clinic makes one vendor the right answer, so it is a platform
decision with no dropdown. The **model** is a legitimate per-agent choice — a
clinic doing high call volume may want the cheaper 8b, one doing complex triage may
want a bigger model — so it gets a dropdown.

That dropdown is populated by a LIVE call to Groq's own ``GET
/openai/v1/models``. There is deliberately **no hardcoded model list and no
fallback list anywhere in this module**. A hardcoded list is how the product ended
up offering ``mixtral-8x7b-32768``, ``llama3-8b-8192``, ``llama3-70b-8192`` and
``gemma-7b-it`` long after Groq decommissioned all four — backend/agent/
resilience.py still carries a rewrite rule mapping those four dead names onto a
live one, which is the scar tissue from exactly this. When the fetch fails, this
module RAISES; the caller surfaces an error and the dropdown shows nothing. An
empty dropdown with an error is honest. A stale list that silently 404s mid-call is
not.

Why the list is FILTERED, and why that is not a hardcoded list
-------------------------------------------------------------
Groq's endpoint returns every model on the account, which on 2026-08-04 was 15
entries — and 5 of them cannot hold a conversation at all:

    whisper-large-v3, whisper-large-v3-turbo      input audio  -> transcription
    canopylabs/orpheus-*                          text         -> audio (TTS)
    meta-llama/llama-prompt-guard-2-22m/-86m      512-token classifiers

Offering those in an LLM picker would not be a cosmetic wart. This is a voice
product where a bad model selection does not raise — it produces an agent that
answers with dead air, which is the failure mode this codebase has been bitten by
repeatedly. So the list is filtered.

Critically, the filter reads **Groq's own metadata on each model**, never a list of
names maintained here. Add a model to the account and it appears automatically;
Groq changing a model's modality changes our answer with no code edit. The three
predicates:

1. ``active`` is true — Groq's own flag for "usable right now".
2. ``input_modalities`` contains ``text`` AND ``output_modalities`` contains
   ``text``. This is what excludes Whisper (``-> transcription``) and Orpheus
   (``-> audio``), from Groq's data rather than from our opinion.
3. ``context_window >= MIN_CONTEXT_WINDOW`` — see below.

What is deliberately NOT filtered on
------------------------------------
``supported_features`` containing ``tools``. It would be reasonable to assume a
booking agent needs function calling, and an earlier revision of
``agent_defaults.py`` asserted exactly that ("booking is a tool-calling task").
**That was wrong.** Verified 2026-08-04: there is no ``register_function``, no
``tools=``, and no ``FunctionSchema`` anywhere in backend/agent/. Booking is driven
by ``BookingProcessor``, which regex-matches the caller's transcript and injects a
``[BOOKING_RESULT ...]`` system message; the chat path parses an ``[ACTION:]``
token. The LLM only ever produces text. Filtering on ``tools`` would therefore have
hidden ``groq/compound`` and ``groq/compound-mini`` for a capability the product
does not use.

Reasoning models are also not filtered out, but they are FLAGGED — see
``reasoning`` on each entry. A model that emits visible chain-of-thought will have
that thinking spoken aloud to the caller by TTS, because nothing in the pipeline
strips it. That is a real trade-off for a voice agent, so it is surfaced to whoever
is choosing rather than decided for them.
"""
from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"

#: Minimum context window for a model to be offered.
#:
#: DERIVED, not picked. Measured 2026-08-04 against the real prompt builder
#: (``pipeline._build_system_prompt``) for a clinic with 6 doctors and 8 knowledge
#: base entries: the system prompt alone is **6,110 characters**. Token cost
#: depends heavily on script, because Indic text tokenizes far worse than Latin:
#:
#:     Latin-ish content   ~1,750 tokens
#:     Malayalam content   ~4,070 tokens
#:
#: On top of that sits the rolling conversation history and
#: ``max_response_tokens`` (default 500). So a 4,096-token model cannot hold a
#: Malayalam clinic's system prompt *before the caller says anything*, and the
#: 512-token prompt-guard classifiers are three orders of magnitude short.
#:
#: 8,192 is the smallest power-of-two ceiling above the measured worst case that
#: still leaves room for a conversation. Raise this if the prompt grows.
MIN_CONTEXT_WINDOW = 8192

#: How long a successful fetch is reused. Groq's catalogue changes on the order of
#: weeks, and this only ever populates a dropdown, so a short TTL would just add
#: latency to opening the agent editor. "Refresh Models" bypasses it entirely.
_CACHE_TTL_SECONDS = 15 * 60

_cache: dict[str, Any] = {"models": None, "fetched_at": 0.0}


class GroqModelsUnavailable(RuntimeError):
    """Groq's model list could not be fetched.

    A distinct type so the router can answer with a clear error instead of an
    empty-but-successful list. "No models available" and "we could not ask" look
    identical in a dropdown and must not.
    """


def _is_conversational(model: dict) -> tuple[bool, str]:
    """Can this model hold a spoken conversation? Returns (ok, reason_if_not).

    Every judgement here comes from a field Groq itself returned.
    """
    if not model.get("active", True):
        return False, "marked inactive by Groq"

    inputs = model.get("input_modalities") or []
    outputs = model.get("output_modalities") or []
    if "text" not in inputs:
        return False, f"does not accept text input (accepts {inputs or 'unknown'})"
    if "text" not in outputs:
        return False, f"does not produce text output (produces {outputs or 'unknown'})"

    ctx = model.get("context_window") or model.get("context_length") or 0
    try:
        ctx = int(ctx)
    except (TypeError, ValueError):
        ctx = 0
    if ctx < MIN_CONTEXT_WINDOW:
        return False, (
            f"context window {ctx or 'unknown'} is below the {MIN_CONTEXT_WINDOW} "
            "needed to hold this product's system prompt and a conversation"
        )

    return True, ""


def _shape(model: dict) -> dict:
    """The subset of Groq's payload the UI needs, plus our derived caveat flags."""
    features = model.get("supported_features") or []
    ctx = model.get("context_window") or model.get("context_length") or 0
    return {
        "id": model.get("id"),
        # Groq supplies a display `name` on some entries and not others; the id is
        # always present and is what actually gets sent to the API, so it is the
        # fallback rather than a prettified guess.
        "name": model.get("name") or model.get("id"),
        "owned_by": model.get("owned_by") or "",
        "context_window": int(ctx) if str(ctx).isdigit() else 0,
        "max_completion_tokens": model.get("max_completion_tokens"),
        # Surfaced, NOT filtered on: a reasoning model's visible chain-of-thought
        # gets spoken to the caller, because nothing in the pipeline strips it.
        "reasoning": "reasoning" in features,
        "supports_tools": "tools" in features,
    }


async def fetch_models(api_key: str, *, force: bool = False) -> list[dict]:
    """Groq's live, currently-usable conversational models.

    Raises ``GroqModelsUnavailable`` on any failure — no key, network error, non-200,
    unparseable body, or a response that contains no usable model. Never returns a
    fallback.

    ``force=True`` bypasses the cache; that is what "Refresh Models" sends.
    """
    if not (api_key or "").strip():
        raise GroqModelsUnavailable(
            "No Groq API key is configured, so the model list cannot be fetched. "
            "Set GROQ_API_KEY in the environment."
        )

    if not force:
        cached = _cache.get("models")
        if cached and (time.monotonic() - float(_cache["fetched_at"])) < _CACHE_TTL_SECONDS:
            return cached  # type: ignore[return-value]

    import httpx

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                GROQ_MODELS_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    # Groq's edge answers Cloudflare error 1010 to some default
                    # client User-Agents (python-urllib is one). Send a normal one.
                    "User-Agent": "curl/8.4.0",
                },
            )
    except Exception as exc:  # noqa: BLE001
        raise GroqModelsUnavailable(
            f"Could not reach Groq to list models: {exc}"
        ) from exc

    if r.status_code == 401:
        raise GroqModelsUnavailable(
            "Groq rejected the configured API key (HTTP 401), so the model list "
            "cannot be fetched."
        )
    if r.status_code != 200:
        raise GroqModelsUnavailable(
            f"Groq answered HTTP {r.status_code} when listing models: {r.text[:200]}"
        )

    try:
        raw = r.json().get("data") or []
    except Exception as exc:  # noqa: BLE001
        raise GroqModelsUnavailable(f"Groq returned an unreadable model list: {exc}") from exc

    usable: list[dict] = []
    excluded: list[str] = []
    for m in raw:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        ok, why = _is_conversational(m)
        if ok:
            usable.append(_shape(m))
        else:
            excluded.append(f"{m['id']} ({why})")

    if not usable:
        # Distinct from a fetch failure, and still an error rather than an empty
        # success: an agent cannot run without a model, so silently offering none
        # would be a dead end with no explanation.
        raise GroqModelsUnavailable(
            f"Groq returned {len(raw)} model(s) but none can hold a conversation. "
            f"Excluded: {'; '.join(excluded) or 'none'}."
        )

    # Largest context first, then by id. Deliberately not "recommended first":
    # there is no recommendation to encode without re-introducing an opinion about
    # specific model names, which is the thing this module exists to avoid.
    usable.sort(key=lambda m: (-m["context_window"], m["id"]))

    if excluded:
        log.info(
            "Groq model list: %d usable, %d excluded — %s",
            len(usable), len(excluded), "; ".join(excluded),
        )

    _cache["models"] = usable
    _cache["fetched_at"] = time.monotonic()
    return usable


#: The three answers ``check_model`` can give. "unknown" is not a failure mode to
#: be collapsed into "dead" — see check_model's docstring for why the distinction
#: decides whether a Groq outage makes every agent read-only.
LIVE, DEAD, UNKNOWN = "live", "dead", "unknown"


async def check_model(api_key: str, model: str) -> str:
    """Is ``model`` one Groq is serving right now? Returns LIVE, DEAD or UNKNOWN.

    The tri-state is the whole point. Callers must not collapse UNKNOWN into DEAD:

    * ``LIVE`` — Groq listed it. Safe to write.
    * ``DEAD`` — Groq answered, and this id was not in the reply. A positive
      statement that the model does not exist, so a caller may refuse the write.
    * ``UNKNOWN`` — we could not ask (no key, network error, non-200). This says
      nothing about the model. Refusing on UNKNOWN would turn any Groq outage into
      "no agent on this platform can be edited", which is a worse failure than
      briefly keeping a model whose liveness we cannot confirm.

    The cache is consulted for a HIT ONLY, then a miss escalates to a forced fetch:
    a hit is proof, a miss just means our 15-minute cache has not heard of it yet.
    """
    wanted = (model or "").strip()
    if not wanted:
        return UNKNOWN

    if wanted in cached_ids():
        return LIVE

    try:
        # force=True is required: a plain fetch would re-read the cache we just
        # missed in and report DEAD on the strength of that same stale entry.
        live = {m["id"] for m in await fetch_models(api_key or "", force=True)}
    except GroqModelsUnavailable:
        return UNKNOWN

    return LIVE if wanted in live else DEAD


def cached_ids() -> set[str]:
    """Model ids from the last successful fetch, or an empty set.

    Used by the write path to validate a submitted model without forcing a network
    call on every save. An empty set means "we cannot vouch either way", which the
    caller must treat as a reason to go and ask, not as a reason to reject.
    """
    return {m["id"] for m in (_cache.get("models") or [])}
