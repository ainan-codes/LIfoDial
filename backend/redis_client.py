# In-memory session store
# Works identically to Redis for local testing
# Replace with real Redis only when deploying
#
# BACKEND is what System Health reports. This module is CURRENTLY an in-process
# dict — it does NOT connect to Redis, and the `redis_url` setting is unused.
# When a real Redis client is wired here, set BACKEND = "redis" and make ping()
# actually round-trip, and the health card will reflect it automatically.
BACKEND = "in-memory"

_sessions: dict = {}


async def ping() -> bool:
    """Liveness probe for the session store. Trivially true for the in-process
    dict; becomes a real Redis PING once/if Redis is wired here."""
    return True

async def get_session(tenant_id: str, 
                      call_id: str) -> dict | None:
    key = f"{tenant_id}:{call_id}"
    return _sessions.get(key)

async def save_session(tenant_id: str, 
                       call_id: str, 
                       data: dict) -> None:
    key = f"{tenant_id}:{call_id}"
    _sessions[key] = data

async def delete_session(tenant_id: str, 
                         call_id: str) -> None:
    key = f"{tenant_id}:{call_id}"
    _sessions.pop(key, None)

async def append_history(tenant_id: str,
                         call_id: str,
                         role: str, 
                         text: str) -> None:
    key = f"{tenant_id}:{call_id}"
    if key in _sessions:
        if "context" not in _sessions[key]:
            _sessions[key]["context"] = {}
        history = _sessions[key].get(
            "context", {}
        ).get("history", [])
        history.append({"role": role, "text": text})
        # Keep last 6 turns only
        if len(history) > 6:
            history = history[-6:]
        _sessions[key]["context"]["history"] = history

print("[OK] Session store ready (in-memory)")
