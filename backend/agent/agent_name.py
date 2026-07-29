"""backend/agent/agent_name.py — the dispatch name, in one place.

This name has to agree in three processes that cannot import each other's
dependencies:

  * the agent worker  (backend/agent/pipeline.py → WorkerOptions(agent_name=...))
  * the API           (backend/routers/web_calls.py → RoomAgentDispatch(agent_name=...))
  * the API's pre-warm probe (backend/services/agent_worker.py, which asserts the
    worker reports THIS name before a room is created)

If they ever drift, LiveKit creates the room, dispatches nothing, and the caller
hears silence with no error anywhere — so the constant lives in a module with no
imports at all, which the pipecat-free API process can import safely.
"""
from __future__ import annotations

#: MUST equal WorkerOptions(agent_name=...) in backend/agent/pipeline.py.
AGENT_NAME = "lifodial-inbound-agent"
