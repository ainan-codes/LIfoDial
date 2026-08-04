"""
backend/agent/__main__.py

Entrypoint for the Lifodial Pipecat voice agent worker.

Run via:
    python -m backend.agent start           # production
    python -m backend.agent dev             # local dev (verbose logging)
    python -m backend.agent start --help    # all options

The livekit-agents CLI (from livekit-agents package) handles:
  - Connecting to LiveKit Cloud
  - Receiving job dispatches
  - Calling entrypoint() once per incoming call
  - Graceful shutdown on SIGTERM

Environment variables required (.env):
  LIVEKIT_URL        wss://your-project.livekit.cloud
  LIVEKIT_API_KEY    your-api-key
  LIVEKIT_API_SECRET your-api-secret
  SARVAM_API_KEY     your-sarvam-key
  GEMINI_API_KEY     your-gemini-key
  DATABASE_URL       postgresql+asyncpg://...
"""

import os
import sys


def _configure_logging() -> None:
    """Configure BOTH loguru (pipecat) and the stdlib `logging` module
    (backend.config, backend.db, ...) before anything else is imported.

    This has to run before the `backend.agent.pipeline` / `backend.config`
    imports below, not after: pipecat prints its own banner via loguru at
    import time, and `backend.config`'s `Settings()` validator logs through
    the stdlib `logging` module at import time too (both would otherwise log
    once, unconfigured, before this function ever got a chance to run).

    Silences pipecat's DEBUG firehose unless explicitly asked for: pipecat's
    default sink is stderr at DEBUG, and a 20-second call produced ~130 lines,
    competing with real-time audio for the event loop on a CPU-constrained
    worker. INFO keeps everything this codebase logs deliberately (STT model
    chosen, turn strategy, language switches, call summaries). Set
    AGENT_LOG_LEVEL=DEBUG to get the firehose back while debugging a call.

    Both sinks emit JSON with an explicit "level" field rather than plain
    text: Railway's log viewer determines severity from that field, falling
    back to stream (stderr -> error, regardless of the level text in the
    message) for unstructured lines — which is what loguru's/stdlib logging's
    plain-text defaults are, so every routine INFO line was showing up as an
    error.
    """
    import json as _json
    import logging as _logging

    level = (os.environ.get("AGENT_LOG_LEVEL") or "INFO").upper()

    class _RailwayJsonFormatter(_logging.Formatter):
        def format(self, record: _logging.LogRecord) -> str:
            payload = {
                "level": record.levelname,
                "message": record.getMessage(),
                "logger": record.name,
                "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            }
            if record.exc_info:
                payload["exc_info"] = self.formatException(record.exc_info)
            return _json.dumps(payload, ensure_ascii=False)

    _handler = _logging.StreamHandler(sys.stdout)
    _handler.setFormatter(_RailwayJsonFormatter())
    _logging.basicConfig(level=_logging.INFO, handlers=[_handler], force=True)

    try:
        from loguru import logger as _loguru

        _loguru.remove()

        def _railway_json_sink(message) -> None:
            record = message.record
            payload = {
                "level": record["level"].name,
                "message": record["message"],
                "logger": record["name"],
                "time": record["time"].isoformat(),
            }
            if record["exception"]:
                payload["exc_info"] = str(record["exception"])
            print(_json.dumps(payload, ensure_ascii=False), flush=True)

        _loguru.add(_railway_json_sink, level=level)
    except Exception as exc:  # never let logging config stop the worker booting
        print(f"warning: could not configure pipecat log level: {exc}", file=sys.stderr)


_configure_logging()

from livekit.agents import WorkerOptions, JobExecutorType, cli  # noqa: E402

from backend.agent.pipeline import AGENT_NAME, entrypoint, prewarm, _preflight_or_die  # noqa: E402
from backend.config import settings  # noqa: E402


def _worker_tuning() -> dict:
    """Worker concurrency/isolation settings, and why they are what they are.

    ⚠️ These three defaults were chosen for Render's FREE plan (0.1 CPU). Hosting
    moved to Railway on 2026-07-31, so the PREMISE below is stale even though the
    settings themselves have not been re-derived. They are NOT arbitrary, and two
    of them are actively dangerous to "improve" without also upgrading the plan:

    MEMORY WARNING (added 2026-08-03). Railway killed lifodial-agent-worker for
    running out of memory. `load_threshold = inf` is the most likely contributor:
    it disables livekit-agents' own overload protection (its prod default is 0.7),
    so this worker accepts UNBOUNDED concurrent jobs, and under the THREAD executor
    every one of them lives in this single process. Measured 2026-08-03: ~212 MB
    RSS before any call, +~8 MB per concurrent call for its Silero VAD analyzer
    alone. Note livekit-agents' `job_memory_limit_mb` (default 0 = off) cannot
    protect a THREAD-executor worker, because a thread's memory is not separable
    from the process's. To cap concurrency WITHOUT a code change or redeploy, set
    AGENT_LOAD_THRESHOLD (e.g. 0.75) in the Railway service env.

    job_executor_type = THREAD
        Runs each job as a thread in the worker process instead of livekit-agents'
        default process isolation. A thread shares the GIL with the worker's own
        event loop, which IS a contributor to the audio gaps this worker logs
        (`Event loop stalled …ms` from _monitor_event_loop_lag in pipeline.py —
        stalls above ~1000ms drain LiveKit's audio queue and the caller hears
        silence; stalls of 4s+ have been observed on this plan). PROCESS isolation
        removes that contention and is the better setting on a real CPU, but
        spawning a Python process that imports pipecat + onnxruntime on 0.1 CPU
        risks exceeding initialize_process_timeout and failing the job outright.
        So: THREAD while on the free plan, PROCESS after upgrading.

    load_threshold = inf
        Accept every dispatch regardless of reported load. This looks reckless but
        was deliberate: on Render's 0.1 CPU the load metric sat permanently near
        saturation, so ANY finite threshold made the worker refuse every job and
        the product went completely dark.

        On Railway that trade-off has flipped: the CPU starvation this worked
        around is gone, and what remains is the downside — no ceiling on how many
        concurrent jobs land in one process, which is how the worker OOMs. This is
        now the FIRST setting to revisit, via AGENT_LOAD_THRESHOLD, and it is worth
        confirming against the Railway memory graph before spending money on a
        bigger plan.

    num_idle_processes = 0
        No pre-warmed processes — they would each hold memory this plan doesn't
        have. Costs a little cold-start latency on the first call.

    All three are env-overridable so they can be tuned on the paid plan without a
    code change:
        AGENT_JOB_EXECUTOR=process|thread
        AGENT_LOAD_THRESHOLD=0.75
        AGENT_IDLE_PROCESSES=1
    """
    executor = (os.environ.get("AGENT_JOB_EXECUTOR") or "thread").strip().lower()
    job_executor_type = (
        JobExecutorType.PROCESS if executor == "process" else JobExecutorType.THREAD
    )

    raw_threshold = (os.environ.get("AGENT_LOAD_THRESHOLD") or "").strip()
    try:
        load_threshold = float(raw_threshold) if raw_threshold else float("inf")
    except ValueError:
        print(
            f"warning: AGENT_LOAD_THRESHOLD={raw_threshold!r} is not a number — "
            "falling back to unlimited (accept every dispatch).",
            file=sys.stderr,
        )
        load_threshold = float("inf")

    try:
        idle = int(os.environ.get("AGENT_IDLE_PROCESSES") or 0)
    except ValueError:
        idle = 0

    print(
        f"[worker] executor={job_executor_type.name} "
        f"load_threshold={load_threshold} idle_processes={idle}"
    )
    return {
        "job_executor_type": job_executor_type,
        "load_threshold": load_threshold,
        "num_idle_processes": idle,
    }


if __name__ == "__main__":
    _preflight_or_die()
    port = int(os.environ.get("PORT") or 8081)
    # cli.run_app() below calls livekit-agents' OWN setup_logging(), which does
    # root.addHandler(...) rather than replacing — on top of the root handler
    # _configure_logging() installed above (needed for the import-time window,
    # e.g. backend.config's SUPERADMIN_PASSWORD warning, which fires before
    # cli.run_app ever runs), that produced every worker log line twice. Clear
    # ours right before handing off; livekit-agents' handler already emits the
    # same Railway-compatible JSON shape with a "level" field.
    import logging as _logging

    _logging.getLogger().handlers.clear()
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name=AGENT_NAME,
            ws_url=settings.livekit_url or None,
            api_key=settings.livekit_api_key or None,
            api_secret=settings.livekit_api_secret or None,
            host="0.0.0.0",
            port=port,
            initialize_process_timeout=60.0,
            **_worker_tuning(),
        )
    )
