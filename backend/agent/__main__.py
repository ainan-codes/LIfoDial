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

from livekit.agents import WorkerOptions, cli

from backend.agent.pipeline import AGENT_NAME, entrypoint, prewarm, _preflight_or_die
from backend.config import settings

if __name__ == "__main__":
    # Fail loudly if the worker can't register with LiveKit (audit FIX 1.4).
    _preflight_or_die()
    # Bind the built-in HTTP health server to Render's $PORT so this can run as a
    # Render web service (incl. free tier — no background_worker type there).
    port = int(os.environ.get("PORT") or 8081)
    # The livekit-agents CLI normally reads LIVEKIT_URL/API_KEY/API_SECRET from OS
    # environment variables. Pass them explicitly from the app settings (which load
    # the project .env) so `python -m backend.agent start` works without a separate
    # manual export step — the worker uses the same creds as the backend.
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
        )
    )
