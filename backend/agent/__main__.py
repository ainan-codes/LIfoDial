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

from livekit.agents import WorkerOptions, JobExecutorType, cli

from backend.agent.pipeline import AGENT_NAME, entrypoint, prewarm, _preflight_or_die
from backend.config import settings


def _configure_pipecat_logging() -> None:
    """Silence pipecat's DEBUG firehose unless explicitly asked for.

    Pipecat logs through loguru, whose default sink is stderr at DEBUG: every
    frame link, every metrics sample, every websocket message. A 20-second call
    produced ~130 lines. On the free-tier worker (0.1 CPU) that formatting and
    stderr I/O competes with real-time audio for the event loop, and it buries
    the lines that matter in the Render log viewer.

    INFO keeps everything this codebase logs deliberately (STT model chosen, turn
    strategy, language switches, call summaries). Set AGENT_LOG_LEVEL=DEBUG to get
    the firehose back while debugging a specific call.
    """
    level = (os.environ.get("AGENT_LOG_LEVEL") or "INFO").upper()
    try:
        from loguru import logger as _loguru

        _loguru.remove()
        # Split by stream: Railway (unlike Render) tags every stderr line as
        # "error" severity in its log viewer regardless of the level text in
        # the message, so sending everything to stderr made routine INFO
        # lines (STT/TTS init, turn logs) show up as errors. Only WARNING+
        # goes to stderr now; INFO/DEBUG goes to stdout.
        _loguru.add(
            sys.stdout,
            level=level,
            filter=lambda record: record["level"].no < _loguru.level("WARNING").no,
        )
        _loguru.add(sys.stderr, level="WARNING")
    except Exception as exc:  # never let logging config stop the worker booting
        print(f"warning: could not configure pipecat log level: {exc}", file=sys.stderr)


def _worker_tuning() -> dict:
    """Worker concurrency/isolation settings, and why they are what they are.

    These three defaults exist because this worker runs on Render's FREE plan
    (0.1 CPU, see render.yaml). They are NOT arbitrary, and two of them are
    actively dangerous to "improve" without also upgrading the plan:

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
        is deliberate: on 0.1 CPU the load metric sits permanently near saturation,
        so ANY finite threshold makes the worker refuse every job and the product
        goes completely dark. Set a real value (e.g. 0.75) only once the plan has
        headroom for the metric to mean something.

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
        f"load_threshold={load_threshold} idle_processes={idle}",
        file=sys.stderr,
    )
    return {
        "job_executor_type": job_executor_type,
        "load_threshold": load_threshold,
        "num_idle_processes": idle,
    }


if __name__ == "__main__":
    _configure_pipecat_logging()
    _preflight_or_die()
    port = int(os.environ.get("PORT") or 8081)
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
