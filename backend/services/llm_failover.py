"""
backend/services/llm_failover.py — what to do when Groq refuses to serve, in ONE
place, shared by the chat path and the voice path.

Why this module exists
----------------------
The 2026-08-10 production incident (Indiana Hospital Mangalore) was handled in
``backend/routers/agent_test.py``: a 429 there now retries a second Groq model and
then a second provider. The voice path had none of it. ``resilience.py`` picked a
provider at call SETUP by probing ``GET /v1/models`` — which answers 200 for a key
whose token budget is fully spent, because listing models costs no tokens. So every
voice call kept starting on the exhausted model, hit 429 on the caller's first
question, spoke "I'm having a little trouble right now", and did that again on every
following turn. The chat fix could not help: it lives in a different module that the
Pipecat pipeline never calls.

Two facts, both established by probing the production key on 2026-08-11, decide the
whole design.

FACT 1 — the budgets are PER MODEL, so failing over to another model really works
--------------------------------------------------------------------------------
This is the claim worth being most careful about, because Groq's own rate-limit page
says "Rate limits apply at the organization level, not individual users", which is
easy to read as "one shared bucket for the whole account" — and if that were true,
switching model would buy nothing and this module would be theatre. That reading is
wrong: org-level describes who SHARES a bucket (every key and user in the org), not
how many buckets there are. There is one bucket per (org, model).

Measured, three one-token requests on the same key, seconds apart::

    model                      limit-requests  limit-tokens  remaining-requests
    llama-3.3-70b-versatile           1000         12000            995
    openai/gpt-oss-120b               1000          8000            999
    llama-3.1-8b-instant             14400          6000          14399

Different ceilings AND independent remaining counters on one key. A shared bucket
could not do that.

FACT 2 — the account is on the FREE plan, and TPD is what actually binds
-----------------------------------------------------------------------
``x-ratelimit-limit-requests: 1000`` and ``x-ratelimit-limit-tokens: 12000`` for
llama-3.3-70b match Groq's published FREE-tier row exactly (RPM 30 / RPD 1K /
TPM 12K / TPD 100K). Note that no header reports TPD at all — the daily token
budget is invisible until it is gone, which is why the incident arrived as a
surprise. Per-model free-tier TPD, and what each is worth at this app's measured
~1,563 tokens per request:

    llama-3.3-70b-versatile   100K TPD    ~64 requests/day
    openai/gpt-oss-120b       200K TPD   ~128 requests/day
    openai/gpt-oss-20b        200K TPD   ~128 requests/day
    llama-3.1-8b-instant      500K TPD   ~320 requests/day   (NOT chained — see below)

So the chain below raises the platform's daily ceiling from ~64 to ~320 requests
across all clinics. That is a mitigation, not a fix: a booking turn costs 2-3
requests (the reply, then the regeneration after an ``[ACTION:]`` tag), so even the
full chain is on the order of a hundred booking turns per DAY for the entire
platform. The real fix is a paid tier; this module keeps calls alive until then.

Why a COOLDOWN and not just try-and-fail
----------------------------------------
Retrying blind is worse than it looks, in three separate ways:

1. Each rejected request still consumes an RPD slot from the 1,000/day bucket.
2. On voice it costs the CALLER a turn — they asked a question and got an apology.
3. It re-fails identically for as long as the budget is out, which Groq itself says
   is ~15 minutes ("Please try again in 14m46.464s"), not a moment.

So a 429 is recorded, keyed by model, for as long as the provider itself asked us to
wait. The next call — and the next turn — starts on a model that still has budget
instead of rediscovering the same wall. The registry is per worker PROCESS
(deliberately: it is a latency optimisation and a courtesy to the caller, not an
accounting system, and a wrong entry expires on its own).

Why llama-3.1-8b-instant is NOT in the chain despite having the largest budget
-----------------------------------------------------------------------------
It has 5x the daily tokens of the primary, and it is still excluded. Measured
2026-08-10 on this app's hardest task — emitting a well-formed ``[ACTION: ...]`` tag
from a booking conversation — it scored 1/3, and its failure mode was the one that
matters: it told the patient "your appointment is booked" with no tag, i.e. it
confirmed a booking that was never written to the database. gpt-oss-120b and -20b
both scored 3/3, matching the primary. A fallback that invents confirmations is not
a fallback; it is the booking-honesty bug arriving through a side door.

``groq/compound`` and ``groq/compound-mini`` are excluded for a different reason:
they route to llama-3.3-70b internally and return that same exhausted-budget 429.
"""
from __future__ import annotations

import logging
import re
import time

log = logging.getLogger(__name__)


#: Groq models to try, in order, when the one before it will not serve.
#:
#: The FIRST entry is not special — this is a preference order, and any model in the
#: list is a legitimate configured choice. What matters is that every entry is
#: verified to handle this app's ``[ACTION:]`` tag emission (3/3 measured), because a
#: fallback that silently breaks booking is worse than a fallback that does not
#: exist. See the module docstring for the models that were rejected and why.
GROQ_MODEL_CHAIN: tuple[str, ...] = (
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
)

#: Free-tier tokens-per-day per model, verified 2026-08-11. Reported to operators so
#: an exhausted budget reads as a known ceiling rather than a mystery outage; nothing
#: branches on these numbers.
GROQ_FREE_TIER_TPD: dict[str, int] = {
    "llama-3.3-70b-versatile": 100_000,
    "openai/gpt-oss-120b": 200_000,
    "openai/gpt-oss-20b": 200_000,
    "llama-3.1-8b-instant": 500_000,
}

#: A wait this short is a BURST limit (RPM/TPM), so the right move is to wait it out
#: on the model that is already configured rather than drop to a fallback. Grounded
#: in the measured refill rates: TPM 12,000 refills at 200 tokens/sec, so the
#: ``x-ratelimit-reset-tokens`` observed on a live request was 185ms, and a full
#: 1,563-token request is ~8s of refill in the worst case. Anything longer than this
#: is a daily budget, which no amount of waiting inside one call will fix.
BURST_RETRY_MAX_WAIT_SECONDS = 5.0

#: Never sleep longer than this in one retry, even if the hint is smaller than
#: BURST_RETRY_MAX_WAIT. A caller is on the phone; 5s of silence is already a lot.
BURST_RETRY_SLEEP_CAP_SECONDS = 5.0

#: Ceiling on a recorded cooldown. Groq's hint is a refill estimate for the tokens
#: just requested, not a full reset, so it is normally minutes. The cap exists so a
#: mis-parsed or hostile duration cannot bench a working model for a day.
MAX_COOLDOWN_SECONDS = 15 * 60.0

#: Applied when a 429 carries no parseable hint. Long enough that the next turn does
#: not simply re-hit the wall, short enough to re-probe well within one call.
DEFAULT_COOLDOWN_SECONDS = 60.0


# ── Error classification ──────────────────────────────────────────────────────
# \b so a token COUNT never reads as a status code. A bare `"429" in msg` also
# matched "Requested 4291 tokens", i.e. reported an unrelated failure to the patient
# as a rate limit. (Padding alone does not fix it: " 429" is a prefix of " 4291".)
_HTTP_429_RE = re.compile(r"\b429\b")

# Groq spells the wait as "Please try again in 14m46.464s."
_RETRY_HINT_RE = re.compile(
    r"try again in\s+(?:(\d+)\s*h)?(?:(\d+)\s*m)?(?:([\d.]+)\s*s)?", re.IGNORECASE,
)


def is_rate_limit_error(error_msg: str) -> bool:
    """Is this error the provider refusing to serve on quota grounds?"""
    low = (error_msg or "").lower()
    return bool(
        _HTTP_429_RE.search(error_msg or "")
        or "rate limit" in low
        or "rate_limit" in low
        or "too many requests" in low
        or "quota exceeded" in low
        or "insufficient_quota" in low
    )


def retry_after_seconds(error_msg: str) -> float | None:
    """Seconds the provider asked us to wait, if it said so at all."""
    m = _RETRY_HINT_RE.search(error_msg or "")
    if not m or not any(m.groups()):
        return None
    h, mins, secs = m.groups()
    return (int(h or 0) * 3600) + (int(mins or 0) * 60) + float(secs or 0)


#: Phrases that NAME a day/month-scale budget, for the case where the provider gave
#: no wait hint at all.
#:
#: Note what is deliberately NOT here: ``billing``, and ``today``. Verified against a
#: REAL 429 forced from the production key on 2026-08-11, Groq appends this to every
#: rate-limit message, whatever the window::
#:
#:     ... on requests per minute (RPM): Limit 30, Used 30, Requested 1. Please try
#:     again in 2s. Need more tokens? Upgrade to Dev Tier today at
#:     https://console.groq.com/settings/billing
#:
#: So matching "billing" or "today" anywhere in the message classified a TWO-SECOND
#: requests-per-minute burst as an exhausted daily budget. That was wrong twice over:
#: the recoverable retry was skipped, and ``describe_llm_failure`` told the patient
#: the assistant had "reached its usage limit for today" and to call the clinic — for
#: a limit that had already cleared by the time they read it.
#:
#: The lesson is the one the transcript matchers learned: a real provider message is
#: not the tidy string you would write in a test.
LONG_QUOTA_MARKERS: tuple[str, ...] = (
    "per day", "tokens per day", "tpd", "daily limit", "per month",
    "quota exceeded", "insufficient_quota", "credit balance",
)


def names_a_long_budget(error_msg: str) -> bool:
    """Does this message NAME a day/month-scale budget? Used only when there is no
    wait hint to reason from — the hint is always the better evidence."""
    low = (error_msg or "").lower()
    return any(k in low for k in LONG_QUOTA_MARKERS)


#: Groq's ways of saying "that model is not something I will serve". Retrying the
#: SAME request on another model of the SAME provider is the obvious recovery, and it
#: needs no second API key.
_MODEL_GONE_MARKERS: tuple[str, ...] = (
    "model_not_found", "model_decommissioned", "decommissioned",
    "no longer supported", "does not exist", "model not found",
)


def is_model_unavailable_error(error_msg: str) -> bool:
    """Is the MODEL the problem, rather than the key, the quota or the network?

    Kept distinct from a rate limit because the recovery differs in one important
    way: an exhausted budget comes back on its own in minutes, so the model is
    benched temporarily, while a decommissioned model is never coming back and the
    row itself needs repairing (the API does that on the next save — see
    agent_defaults.apply_locked_defaults' ``llm_model_ok``).

    What they share is the immediate move: try another model on the same key. Before
    this existed, a 404 on the only configured provider fell straight through to
    "I'm having trouble processing that" — even though four other Groq models were
    reachable with the very same key. That is not hypothetical: one live agent sat on
    ``gemini-2.5-flash-8b`` with ``llm_provider='groq'`` and answered HTTP 404 on
    every single call.
    """
    low = (error_msg or "").lower()
    if any(k in low for k in _MODEL_GONE_MARKERS):
        return True
    # A bare 404 from a chat-completions call is about the model — that endpoint has
    # no other resource to miss. \b so a token count or an id containing 404 does not
    # read as a status code.
    return bool(re.search(r"\b404\b", error_msg or "") and "model" in low)


def is_burst_limit(error_msg: str) -> bool:
    """True when waiting a few seconds can actually fix this.

    A TPM/RPM burst refills continuously (measured: 200 tokens/sec at TPM 12,000),
    so the provider's own hint is small and sleeping through it keeps the caller on
    the model their clinic configured. A tokens-per-DAY exhaustion quotes minutes and
    must never be slept on — that is the "please wait a moment" lie the 2026-08-10
    incident was reported for.

    The provider's own wait hint DECIDES this whenever it is present, rather than
    being a tie-breaker after keyword matching. Groq computes it from the real refill
    rate, so it is strictly better evidence than anything inferred from the prose —
    and the prose is actively misleading, since every Groq rate-limit message carries
    an "Upgrade to Dev Tier" billing link regardless of which window was hit (see
    LONG_QUOTA_MARKERS).
    """
    if not is_rate_limit_error(error_msg):
        return False

    wait = retry_after_seconds(error_msg)
    if wait is not None:
        return wait <= BURST_RETRY_MAX_WAIT_SECONDS

    # No hint at all: fall back to what the message names, and default to
    # recoverable. The sleep is bounded at BURST_RETRY_SLEEP_CAP_SECONDS, so being
    # wrong costs one short pause before the model failover runs anyway.
    return not names_a_long_budget(error_msg)


def burst_sleep_seconds(error_msg: str) -> float:
    """How long to sleep before retrying the same model on a burst limit."""
    wait = retry_after_seconds(error_msg)
    if wait is None:
        return 1.0
    # +0.25s so the retry lands just AFTER the bucket has refilled rather than on
    # the exact boundary, which reproduces the 429.
    return min(max(wait + 0.25, 0.25), BURST_RETRY_SLEEP_CAP_SECONDS)


# ── Cooldown registry ─────────────────────────────────────────────────────────
# model -> monotonic timestamp at which it is worth trying again.
_cooldowns: dict[str, float] = {}


def mark_rate_limited(model: str, error_msg: str = "", retry_after: float | None = None) -> float:
    """Record that ``model`` is out of budget. Returns the cooldown length applied.

    The provider's own hint is preferred over any guess we could make — it is
    computed from the real refill rate. Clamped to MAX_COOLDOWN_SECONDS.
    """
    m = (model or "").strip()
    if not m:
        return 0.0
    wait = retry_after if retry_after is not None else retry_after_seconds(error_msg)
    if wait is None:
        wait = DEFAULT_COOLDOWN_SECONDS
    wait = min(max(float(wait), 1.0), MAX_COOLDOWN_SECONDS)
    _cooldowns[m] = time.monotonic() + wait
    log.warning(
        "[LLM-FAILOVER] %s is rate limited — benched for %.0fs (free-tier TPD %s). %s",
        m, wait, GROQ_FREE_TIER_TPD.get(m, "unknown"),
        (error_msg or "")[:180],
    )
    return wait


def cooldown_remaining(model: str) -> float:
    """Seconds until ``model`` is worth trying again; 0.0 when it is available."""
    until = _cooldowns.get((model or "").strip())
    if until is None:
        return 0.0
    left = until - time.monotonic()
    if left <= 0:
        _cooldowns.pop((model or "").strip(), None)
        return 0.0
    return left


def is_cooling_down(model: str) -> bool:
    return cooldown_remaining(model) > 0


def reset_cooldowns() -> None:
    """Forget every recorded cooldown (tests, and manual recovery)."""
    _cooldowns.clear()


# ── Choosing a model ──────────────────────────────────────────────────────────


def fallback_models(model: str) -> list[str]:
    """The chain to try after ``model``, in order, excluding ``model`` itself.

    A configured model that is not in the chain at all still gets the full chain as
    fallbacks — that is the point: an operator who picked ``groq/compound`` from the
    live dropdown must still fail over to something when it 429s.
    """
    current = (model or "").strip()
    return [m for m in GROQ_MODEL_CHAIN if m != current]


def next_available_model(model: str) -> str | None:
    """The best model to move to right now, or None when everything is benched.

    None is a real answer and callers must handle it by telling the truth. Silently
    returning the exhausted model would put back the dead-end this module exists to
    remove.
    """
    for candidate in fallback_models(model):
        if not is_cooling_down(candidate):
            return candidate
    return None


def preferred_model(configured: str) -> tuple[str, str | None]:
    """``(model_to_use, why_if_changed)`` for a call/turn that is about to start.

    Consulted BEFORE dispatching, so a model known to be out of budget is skipped
    rather than rediscovered. When the configured model is fine — the overwhelming
    majority of the time — this returns it unchanged with ``None``, so it is a no-op
    on the happy path.

    When everything is benched the CONFIGURED model is returned rather than None:
    by then the cooldowns are stale guesses, the budget may well have refilled, and
    honouring the operator's choice is the better default. The dispatch will fail
    over for real if it has not.
    """
    current = (configured or "").strip()
    if not current or not is_cooling_down(current):
        return current, None

    alt = next_available_model(current)
    if alt is None:
        log.warning(
            "[LLM-FAILOVER] every Groq model in the chain is rate limited — "
            "trying the configured model %s anyway.", current,
        )
        return current, None

    left = cooldown_remaining(current)
    reason = (
        f"{current} is rate limited for another {left:.0f}s, so this turn runs on {alt}"
    )
    log.warning("[LLM-FAILOVER] %s", reason)
    return alt, reason
