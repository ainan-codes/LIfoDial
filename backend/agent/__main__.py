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
        _loguru.add(sys.stderr, level=level)
    except Exception as exc:  # never let logging config stop the worker booting
        print(f"warning: could not configure pipecat log level: {exc}", file=sys.stderr)


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
            job_executor_type=JobExecutorType.THREAD,
            initialize_process_timeout=60.0,
            num_idle_processes=0,
            load_threshold=float("inf"),
        )
    )
