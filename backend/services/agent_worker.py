"""
backend/services/agent_worker.py — keeps the Pipecat agent worker reachable.

WHY THIS EXISTS
───────────────
If the worker is not registered with LiveKit, `RoomAgentDispatch(agent_name=...)`
has nothing to dispatch to: the room gets created, the browser joins, and the
caller hears silence. That is exactly the [DISPATCH-ALARM] in
backend/routers/web_calls.py.

⚠️ HOSTING MOVED TO RAILWAY (2026-07-31). Everything below was written for Render
and its numbers are host-specific. The service is now `lifodial-agent-worker` on
Railway; there is no `.onrender.com` URL and no Render dashboard to check. See
`RENDER-ERA, NOW STALE` at the bottom of this docstring for what no longer holds.

WHAT ACTUALLY TAKES THE WORKER DOWN (confirmed 2026-08-03)
Railway's own crash email showed `lifodial-agent-worker` was killed for running
OUT OF MEMORY, and three real calls hit [DISPATCH-ALARM] because the process was
simply dead at that moment. So an unregistered worker is NOT automatically a
sleep/cold-start problem any more, and it is NOT an agent_name mismatch:

    check, in this order:  (1) is the process alive?  (2) its memory graph
                           (3) only then, dispatch/registration config

Measured baseline (2026-08-03): importing livekit.agents + pipecat + SQLAlchemy
and loading ONE Silero VAD model costs ~212 MB RSS before a single call is taken.
Per CONCURRENT call adds ~8 MB for its VAD analyzer alone. The worker also runs
with `load_threshold=inf` (see backend/agent/__main__.py), which disables
livekit-agents' built-in overload protection, so it accepts unbounded concurrent
jobs into ONE process under the THREAD executor. That combination — not a leak —
is the most likely OOM mechanism. A per-call Silero analyzer was measured NOT to
leak: 25 sequential build/drop cycles retained 1.8 MB.

TWO DEFENCES, both in this module:

  1. `ensure_worker_awake()` — called on the request path in web_calls.py BEFORE
     the room is created. Blocks until the worker answers HTTP, so we never
     dispatch into a room the worker can't join. Warm calls cost ~60-100ms
     backend-to-worker (both services sit in Railway sin1).

  2. `keep_warm_loop()` — started from main.py's lifespan. Pings the worker so a
     probe on the call path is a no-op in the common case.

     ⚠️ Its ORIGINAL purpose (defeating Render's 15-min free-tier spin-down) no
     longer applies on Railway. What it now does is keep the worker permanently
     resident, which on usage-billed Railway means it never stops accruing cost
     OR releasing memory. Whether to keep it is a hosting decision, not a
     correctness one — deliberately left running rather than changed silently.

Everything here is best-effort and fully guarded: if the worker URL is unset or
unreachable, calls proceed exactly as they did before rather than failing.

RENDER-ERA, NOW STALE — do not act on these without re-measuring on Railway:
  * "~15 minutes without an HTTP request spins the instance down" — Render's idle
    policy. Railway does not do this.
  * "54.4s cold start" / "~55s" — measured against a spun-down Render free
    instance. Railway has no equivalent spin-down, so a slow first call needs a
    fresh explanation rather than this one.
  * "730 instance-hours inside Render's 750h free allowance" — Render billing.
  * `_PROBE_TIMEOUT_SECONDS`, `WARM_TIMEOUT_SECONDS` and `_WARM_CACHE_SECONDS`
    below were all sized against those Render numbers and are almost certainly
    far larger than Railway needs. Left as-is (generous timeouts are safe; the
    failure mode of shrinking them wrongly is a dropped call).
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
WARM_TIMEOUT_SECONDS = 150.0

# One probe ATTEMPT's timeout, and it must be LONGER than a cold start, not
# shorter. Render holds the connection open while a free instance boots and
# answers when it is ready — measured directly on 2026-07-29:
#
#     curl https://lifodial-agent.onrender.com/worker  ->  HTTP 200 in 53.6s
#
# This was 15s, which abandoned the connection a third of the way through every
# boot and then immediately reconnected. The result, from a real call attempt:
#
#     Agent worker did NOT come up within 90s (41 probes, last state=down)
#
# 41 fast failures instead of one patient success. A short timeout here doesn't
# make the check more responsive, it makes it structurally unable to observe a
# cold start — the one situation the whole function exists for.
_PROBE_TIMEOUT_SECONDS = 75.0

# Gap between probe attempts. Only reached when an attempt genuinely fails
# (connection refused / boot took longer than the attempt timeout), so this is a
# backoff between full attempts, not a poll interval.
_PROBE_RETRY_GAP_SECONDS = 3.0

# Once the worker has answered, trust it for this long before probing again.
# Well under Render's ~15-min (900s) idle window, so a cached "warm" verdict can
# never outlive the instance it describes.
_WARM_CACHE_SECONDS = 300.0

# Interval for the background keep-warm pinger. The binding constraint is NOT the
# host's idle window — it is _WARM_CACHE_SECONDS above, and this must stay BELOW
# it. Each tick calls mark_warm(), which vouches for the worker for only 300s, so
# at the previous 600s the cache sat STALE for half of every cycle: a call landing
# in that half re-probed and paid a full network round trip even though the pinger
# had the worker demonstrably awake — keep-warm was cancelling the cost it exists
# to cancel only about half the time.
#
# 240s keeps a valid cache at all times with one whole tick of slack for a missed
# or slow ping, and is still far below any plausible idle window.
#
# Size the win honestly. From the BACKEND this probe is ~60-100ms: both services
# sit in Railway sin1, and 2026-07-31 measurements show a 0.1s boot probe and 63ms
# steady-state pings. An earlier revision of this comment claimed ~733ms — that was
# measured from a laptop in India to Singapore and is NOT the path production
# takes. So this fix is worth ~0.1s on a first call, not ~0.7s. The stale-cache bug
# was real and worth fixing, but it is nowhere near the dominant term in call
# latency: that is the ~1.25s Supabase connection handshake (see backend/db.py —
# and note DB_POOL_SIZE>0 cannot help a THREAD-executor worker, because
# livekit-agents gives every job a fresh event loop) and the ~4.4s greeting path.
KEEP_WARM_INTERVAL_SECONDS = 240.0

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


async def _probe_once(timeout: float) -> str:
    """One GET against the worker's own /worker endpoint.

    Returns one of:
      "ready"      — the WORKER process answered, and it reports our agent_name.
      "responding" — something answered, but it wasn't the worker (Render's edge
                     serving a boot/404/502 page while the instance starts).
      "down"       — nothing answered at all (connect error / timeout).

    WHY THIS IS NOT "any HTTP response == awake" (it used to be, and that is the
    bug this function exists to fix). Measured in production on 2026-07-29:

        09:48:54  worker "draining / shutting down"      (free-tier spin-down)
        10:25:23  API: "Pre-warming agent worker..."     → probe SUCCEEDED
        10:25:52  [DISPATCH-ALARM] agent never joined room
        11:18:55  API: "Agent worker awake after 0.2s"   → probe SUCCEEDED
        11:19:23  [DISPATCH-ALARM] agent never joined room
        11:32:52  worker actually boots, registers 11:33:04

    A spun-down instance cannot answer in 0.2s. Render's router was replying
    from the edge, the old probe counted that as awake, `mark_warm()` cached the
    lie for 5 minutes (so the user's immediate retry did not even re-probe), and
    every room created in that window got a caller and no agent. That is the
    whole "sometimes it connects, sometimes it doesn't".

    livekit-agents serves /worker from the worker process itself, and the body
    carries `agent_name` — which is exactly the fact we need (the process is up
    AND it is our worker, not some other service on that URL). Anything we can't
    parse is treated as "not the worker yet".
    """
    import httpx

    from backend.agent.agent_name import AGENT_NAME

    url = f"{worker_base_url()}/worker"
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
    except Exception as exc:
        log.debug("Agent worker probe %s failed: %s", url, exc)
        return "down"

    if resp.status_code != 200:
        log.debug("Agent worker probe %s -> HTTP %s (not the worker)", url, resp.status_code)
        return "responding"

    try:
        reported = (resp.json() or {}).get("agent_name")
    except Exception:
        return "responding"

    if reported == AGENT_NAME:
        return "ready"

    # A 200 with a *different* agent_name is a real misconfiguration, not a cold
    # start — retrying will never fix it, so say so loudly instead of burning the
    # caller's whole warm budget on it.
    log.error(
        "Agent worker at %s reports agent_name=%r but this API dispatches %r. "
        "Dispatched calls will NEVER be picked up until these match "
        "(backend/agent/agent_name.py).",
        url, reported, AGENT_NAME,
    )
    return "responding"


async def _probe(timeout: float) -> bool:
    """Back-compat single-shot boolean probe: True only when the worker is ready."""
    return (await _probe_once(timeout)) == "ready"


async def ensure_worker_awake(timeout: float = WARM_TIMEOUT_SECONDS) -> bool:
    """Poll until the agent worker is genuinely up, or `timeout` elapses.

    "Genuinely up" means the worker process itself answered /worker with our
    agent_name — see _probe_once for why a mere HTTP response is not enough.

    Returns True only in that case. False means the room should NOT be created:
    the worker cannot accept the dispatch, so the caller would join a room no
    agent ever enters (backend/routers/web_calls.py turns that into a clear 503
    rather than 25 seconds of silence).
    """
    if not is_configured():
        return False
    if _is_cached_warm():
        # Say so out loud. This is the path keep-warm exists to create, and it used
        # to be the only outcome that left NO trace in the log — so "keep-warm is
        # working" was indistinguishable from "keep-warm never ran" at exactly the
        # moment you want to tell them apart: during a live call.
        log.info(
            "Agent worker pre-warm SKIPPED — keep-warm cache still valid for %.0fs, "
            "no probe needed (saves ~0.1s of call setup).",
            _warm_until - time.monotonic(),
        )
        return True

    async with _get_lock():
        # Another waiter may have warmed it while we queued on the lock.
        if _is_cached_warm():
            return True

        started = time.monotonic()
        deadline = started + timeout
        log.info("Pre-warming agent worker (cold start can take ~55s)...")

        attempts = 0
        last_state = "down"
        while True:
            attempts += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            last_state = await _probe_once(min(_PROBE_TIMEOUT_SECONDS, remaining))
            if last_state == "ready":
                mark_warm()
                log.info(
                    "Agent worker awake and registered after %.1fs (%d probe%s)",
                    time.monotonic() - started, attempts, "" if attempts == 1 else "s",
                )
                return True
            if deadline - time.monotonic() <= 0:
                break
            await asyncio.sleep(_PROBE_RETRY_GAP_SECONDS)

        log.error(
            "Agent worker did NOT come up within %.0fs (%d probes, last state=%s). NOT "
            "creating a room — a dispatched call would find no agent and the caller "
            "would hear silence. Check lifodial-agent-worker on RAILWAY: its deploy "
            "logs and its MEMORY graph. Confirmed 2026-08-03: this alarm fires when "
            "the worker process is simply dead, and the cause on that occasion was an "
            "out-of-memory crash — not a dispatch/registration fault. Do not debug "
            "agent_name matching or LiveKit config until the worker is confirmed UP.",
            time.monotonic() - started, attempts, last_state,
        )
        return False


#: Retained so a detached warm-up task can't be garbage collected mid-flight.
_background_warms: set = set()


def state() -> dict:
    """Cheap, non-blocking view of whether the worker is believed awake.

    Used by the dashboard to show "warming up…" before the user commits to a
    call, instead of discovering it during one.
    """
    return {
        "configured": is_configured(),
        "warm": _is_cached_warm(),
        "warming": _warm_lock is not None and _warm_lock.locked(),
    }


def start_background_warm() -> bool:
    """Kick off a warm-up WITHOUT blocking the caller. True if one is running.

    This is the fix for the actual user-visible problem. Render sleeps a free
    service after 15 minutes idle, and the keepalive that was supposed to prevent
    that is a GitHub Actions `*/5` cron — which GitHub does NOT honour on a free
    runner: the real run times on 2026-07-29 were 00:09, 03:33, 06:15, 09:10,
    11:32, 13:16, i.e. gaps of one to three HOURS against a 15-minute window. So
    the worker is asleep essentially whenever someone goes to use it, and the
    ~55s boot lands entirely on whoever pressed the button.

    Calling this when the Test Agent panel OPENS moves that boot off the critical
    path: by the time the admin has read the screen and pressed Start, the worker
    is already registered. Idempotent — a second call while one is in flight is a
    no-op, because ensure_worker_awake serialises on _warm_lock and returns
    immediately when the warm cache is still valid.
    """
    if not is_configured() or _is_cached_warm():
        return False

    async def _run() -> None:
        try:
            await ensure_worker_awake()
        except Exception as exc:
            log.warning("Background agent-worker warm failed (non-fatal): %s", exc)

    task = asyncio.ensure_future(_run())
    _background_warms.add(task)
    task.add_done_callback(_background_warms.discard)
    return True


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

    # See Settings.agent_worker_keep_warm for why this was ever off.
    #
    # The message below deliberately does NOT promise a ~55s cold start any more.
    # That number was Render's free-tier spin-down and it is host-specific, not a
    # property of this code. Measured on Railway 2026-07-31, with
    # sleepApplication=false on both services: after 18min of zero HTTP contact the
    # worker answered /worker in 733ms with active_jobs=0 and no restart — i.e. it
    # never slept and there was no cold start to pay. The old wording sent debugging
    # after a 55s delay that does not exist on this host, so state the mechanism and
    # let whoever reads it measure their own host rather than quoting a stale figure.
    if not settings.agent_worker_keep_warm:
        log.info(
            "Agent-worker keep-warm is DISABLED (AGENT_WORKER_KEEP_WARM unset or false). "
            "Calls still pre-warm the worker on demand, so rooms won't be agent-less. "
            "The cost of leaving it off is one extra probe round trip on the first call "
            "after %.0fs idle, PLUS a full cold start if — and only if — this host sleeps "
            "idle services. Set AGENT_WORKER_KEEP_WARM=true to hold the worker awake.",
            _WARM_CACHE_SECONDS,
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
            _ping_t0 = time.monotonic()
            state = await _probe_once(_PROBE_TIMEOUT_SECONDS)
            if state == "ready":
                mark_warm()
                # Logged, not silent. This doubles as a continuous latency probe of
                # the worker, and it is the line that proves the loop is alive —
                # without it the only evidence of a healthy pinger is the absence of
                # warnings, which is not evidence.
                log.info(
                    "Agent-worker keep-warm ping OK in %.0fms — worker awake, cache "
                    "renewed for %.0fs (next ping in %.0fs).",
                    (time.monotonic() - _ping_t0) * 1000,
                    _WARM_CACHE_SECONDS, KEEP_WARM_INTERVAL_SECONDS,
                )
            else:
                log.warning(
                    "Agent-worker keep-warm ping did not reach the worker (state=%s) — "
                    "it may have spun down or be crash-looping.", state,
                )
        except asyncio.CancelledError:
            log.info("Agent-worker keep-warm stopped.")
            raise
        except Exception as exc:
            log.warning("Agent-worker keep-warm tick errored (non-fatal): %s", exc)
