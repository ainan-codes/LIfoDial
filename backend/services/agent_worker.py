"""
backend/services/agent_worker.py — keeps the Pipecat agent worker reachable.

WHY THIS EXISTS
───────────────
`lifodial-agent` runs on Render's FREE plan, which spins the instance down after
~15 minutes without an HTTP request. A spun-down worker is DEREGISTERED from
LiveKit, so `RoomAgentDispatch(agent_name=...)` has nothing to dispatch to: the
room gets created, the browser joins, and the caller hears silence. That is
exactly the [DISPATCH-ALARM] in backend/routers/web_calls.py.

The numbers, measured against production on 2026-07-25:

    GET https://lifodial-agent.onrender.com/  ->  HTTP 200 in 54.4s   (cold)
    GET https://lifodial-agent.onrender.com/worker -> HTTP 200 (warm, ~0.2s)

54s cold start vs. a 15s dispatch deadline means the first call after an idle
period fails 100% of the time. Nothing is misconfigured — the worker simply
isn't awake yet. (`agent_name` matched and worker_load was 0.025 when probed.)

TWO DEFENCES, both in this module:

  1. `ensure_worker_awake()` — called on the request path in web_calls.py BEFORE
     the room is created. Blocks until the worker answers HTTP, so we never
     dispatch into a room the worker can't join. Warm calls cost ~0.2s.

  2. `keep_warm_loop()` — started from main.py's lifespan on the ALWAYS-ON
     `lifodial-api` service (plan: starter). Pings the worker inside Render's
     15-min idle window so it never spins down in the first place, making
     defence #1 a no-op in the common case.

Note on hours: keeping ONE free service always-on costs ~730 instance-hours per
month, inside Render's 750h free allowance. Only `lifodial-agent` is on the free
plan — the API and frontend are `starter` — so this does not overrun it.

Everything here is best-effort and fully guarded: if the worker URL is unset or
unreachable, calls proceed exactly as they did before rather than failing.
"""
from __future__ import annotations

import asyncio
import logging
import time

from backend.config import settings

log = logging.getLogger(__name__)

# How long to block a call-setup request while a cold worker boots. The measured
# cold start is ~55s; 90s leaves headroom for a slow build/boot without hanging
# the browser forever. Must stay BELOW the frontend's TIMEOUT_MS in
# TestVoiceCallLK.tsx or the browser gives up before the worker is ready.
WARM_TIMEOUT_SECONDS = 90.0

# A single probe attempt's timeout. Render holds the connection open while the
# instance boots, so one long-lived GET is the reliable way to wait for a cold
# start (repeated short probes just get repeated hangs).
_PROBE_TIMEOUT_SECONDS = 95.0

# Once the worker has answered, trust it for this long before probing again.
# Well under Render's ~15-min (900s) idle window, so a cached "warm" verdict can
# never outlive the instance it describes.
_WARM_CACHE_SECONDS = 300.0

# Interval for the background keep-warm pinger. Must be < Render's ~900s idle
# timeout with margin for a missed tick.
KEEP_WARM_INTERVAL_SECONDS = 600.0

# Monotonic deadline until which the worker is assumed awake.
_warm_until: float = 0.0

# Serialises concurrent cold starts: if three callers arrive while the worker is
# booting, only ONE probe is issued and the others await the same result instead
# of piling 3 x 90s requests onto a booting free instance.
_warm_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """Lazily create the lock bound to the running loop."""
    global _warm_lock
    if _warm_lock is None:
        _warm_lock = asyncio.Lock()
    return _warm_lock


def worker_base_url() -> str:
    """Configured worker base URL with any trailing slash removed, or ""."""
    return (settings.agent_worker_url or "").strip().rstrip("/")


def is_configured() -> bool:
    """True when a worker URL is set, so pre-warm/keep-warm can run at all."""
    return bool(worker_base_url())


def mark_warm() -> None:
    """Record that the worker just answered, so probes are skipped for a while."""
    global _warm_until
    _warm_until = time.monotonic() + _WARM_CACHE_SECONDS


def _is_cached_warm() -> bool:
    return time.monotonic() < _warm_until


async def _probe(timeout: float) -> bool:
    """One GET against the worker's status endpoint. True on any HTTP response.

    ANY status code counts as awake: we only care that the instance is running
    (that's what re-registers it with LiveKit), not what the route returns.
    """
    import httpx

    url = f"{worker_base_url()}/worker"
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
        log.debug("Agent worker probe %s -> HTTP %s", url, resp.status_code)
        return True
    except Exception as exc:
        log.warning("Agent worker probe failed (%s): %s", url, exc)
        return False


async def ensure_worker_awake(timeout: float = WARM_TIMEOUT_SECONDS) -> bool:
    """Block until the agent worker is awake, or `timeout` elapses.

    Returns True if the worker responded (or was recently known warm), False if
    it is unreachable or no URL is configured. Callers should treat False as
    "dispatch anyway, but expect a possible agent-less room" — never as fatal.
    """
    if not is_configured():
        return False
    if _is_cached_warm():
        return True

    async with _get_lock():
        # Another waiter may have warmed it while we queued on the lock.
        if _is_cached_warm():
            return True

        started = time.monotonic()
        log.info("Pre-warming agent worker (cold start can take ~55s)...")
        try:
            ok = await asyncio.wait_for(
                _probe(min(_PROBE_TIMEOUT_SECONDS, timeout)), timeout=timeout
            )
        except asyncio.TimeoutError:
            ok = False

        elapsed = time.monotonic() - started
        if ok:
            mark_warm()
            log.info("Agent worker awake after %.1fs", elapsed)
        else:
            log.error(
                "Agent worker did NOT respond within %.0fs — dispatching anyway, but "
                "the room may have no agent. Check the lifodial-agent service on Render.",
                elapsed,
            )
        return ok


async def keep_warm_loop() -> None:
    """Ping the worker forever so Render never spins it down.

    Runs on the always-on API service. Silent on success; logs only failures, so
    it cannot spam the log every 10 minutes.
    """
    if not is_configured():
        log.info(
            "AGENT_WORKER_URL is not set — agent-worker keep-warm disabled. The free-tier "
            "worker will spin down after ~15min idle and the first call after that will "
            "hit an agent-less room."
        )
        return

    # OFF by default — see Settings.agent_worker_keep_warm. On Render's free plan
    # holding the worker awake 24/7 (~730h) against a 750h ACCOUNT-WIDE allowance
    # would suspend every service on the account. Pre-warm already prevents
    # agent-less rooms, so this is a paid-plan-only optimisation that trades the
    # ~55s first-call delay for instance-hours.
    if not settings.agent_worker_keep_warm:
        log.info(
            "Agent-worker keep-warm is DISABLED (AGENT_WORKER_KEEP_WARM=false). Calls still "
            "pre-warm the worker on demand, so rooms won't be agent-less — but the first "
            "call after ~15min idle will wait ~55s for the free instance to boot. Set "
            "AGENT_WORKER_KEEP_WARM=true once the services are on a paid plan."
        )
        return

    log.info(
        "Agent-worker keep-warm started (every %.0fs -> %s)",
        KEEP_WARM_INTERVAL_SECONDS, worker_base_url(),
    )
    # Wake it once at boot so the very first call of the day isn't the cold one.
    try:
        await ensure_worker_awake()
    except Exception as exc:
        log.warning("Initial agent-worker warm failed (non-fatal): %s", exc)

    while True:
        try:
            await asyncio.sleep(KEEP_WARM_INTERVAL_SECONDS)
            if await _probe(_PROBE_TIMEOUT_SECONDS):
                mark_warm()
            else:
                log.warning("Agent-worker keep-warm ping failed — worker may be down.")
        except asyncio.CancelledError:
            log.info("Agent-worker keep-warm stopped.")
            raise
        except Exception as exc:
            log.warning("Agent-worker keep-warm tick errored (non-fatal): %s", exc)
