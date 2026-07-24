"""
backend/redis_client.py — session store with a real Redis backend and an
automatic in-memory fallback.

Behavior:
- If REDIS_URL points at a real redis:// / rediss:// server, the `redis` package
  is installed, AND the connection succeeds, sessions are stored in Redis
  (durable across restarts; shareable across workers/instances) with a TTL, and
  `BACKEND` becomes "redis".
- Otherwise — no/placeholder URL, package missing, or the connection fails — it
  transparently falls back to the previous in-process dict and `BACKEND` stays
  "in-memory". This is the safe default and matches the pre-Redis behavior
  byte-for-byte, so nothing breaks if Redis isn't provisioned. A Redis op that
  fails mid-run degrades to memory rather than dropping the call.

IMPORTANT SCOPE NOTE: this store backs the LEGACY telephony path
(routers/voice.py, routers/ws.py). The live web-call turn (routers/agent_test.py)
still keeps its per-turn state in module-level dicts, so wiring Redis here does
NOT by itself make that path multi-worker-safe — that requires moving the
hot-path state here too (fast-follow). Until then, keep uvicorn at --workers 1.
"""
from __future__ import annotations

import json
import logging

from backend.config import settings

log = logging.getLogger(__name__)

_SESSION_TTL_SECONDS = 60 * 60 * 24  # 24h — call sessions are short-lived
_KEY_PREFIX = "lifodial:session:"

# In-memory fallback store (also the last-ditch net if a Redis op fails).
_sessions: dict = {}

_redis = None            # connected client, or None while in fallback mode
_resolved = False        # have we decided the backend for this process yet?
BACKEND = "in-memory"    # what System Health reports; flips to "redis" on connect


def _mem_key(tenant_id: str, call_id: str) -> str:
    return f"{tenant_id}:{call_id}"


def _redis_key(tenant_id: str, call_id: str) -> str:
    return f"{_KEY_PREFIX}{tenant_id}:{call_id}"


async def _get_client():
    """Lazily connect to Redis; return the client, or None to signal fallback.

    The decision is cached for the process on first call. A placeholder URL
    (anything that isn't a real redis://|rediss:// address), a missing `redis`
    package, or a failed PING all resolve to None (in-memory) — logged once,
    never raised.
    """
    global _redis, _resolved, BACKEND
    if _resolved:
        return _redis
    _resolved = True

    url = (settings.redis_url or "").strip()
    if not url or not url.startswith(("redis://", "rediss://")):
        log.info("[SESSION] no real REDIS_URL configured — using in-memory session store")
        return None
    try:
        import redis.asyncio as aioredis
    except Exception as e:  # package not installed
        log.warning("[SESSION] redis package unavailable (%s) — using in-memory store", e)
        return None
    try:
        client = aioredis.from_url(
            url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2.0,  # never hang startup on an unreachable server
            socket_timeout=2.0,
        )
        await client.ping()
        _redis = client
        BACKEND = "redis"
        log.info("[SESSION] connected to Redis — sessions are durable and shareable")
        return _redis
    except Exception as e:
        log.warning("[SESSION] Redis connect failed (%s) — using in-memory store", e)
        return None


async def ping() -> bool:
    """Liveness probe. Resolves the backend on first call so callers (e.g. the
    System Health card) see an accurate BACKEND afterwards. In-memory is always
    'up'; Redis does a real round-trip."""
    client = await _get_client()
    if client is None:
        return True
    try:
        return bool(await client.ping())
    except Exception:
        return False


async def get_session(tenant_id: str, call_id: str) -> dict | None:
    client = await _get_client()
    if client is None:
        return _sessions.get(_mem_key(tenant_id, call_id))
    try:
        raw = await client.get(_redis_key(tenant_id, call_id))
        return json.loads(raw) if raw else None
    except Exception as e:
        log.warning("[SESSION] get failed (%s) — falling back to memory", e)
        return _sessions.get(_mem_key(tenant_id, call_id))


async def save_session(tenant_id: str, call_id: str, data: dict) -> None:
    client = await _get_client()
    if client is None:
        _sessions[_mem_key(tenant_id, call_id)] = data
        return
    try:
        await client.set(
            _redis_key(tenant_id, call_id),
            json.dumps(data, default=str),
            ex=_SESSION_TTL_SECONDS,
        )
    except Exception as e:
        log.warning("[SESSION] save failed (%s) — kept in memory only", e)
        _sessions[_mem_key(tenant_id, call_id)] = data


async def delete_session(tenant_id: str, call_id: str) -> None:
    _sessions.pop(_mem_key(tenant_id, call_id), None)
    client = await _get_client()
    if client is None:
        return
    try:
        await client.delete(_redis_key(tenant_id, call_id))
    except Exception as e:
        log.warning("[SESSION] delete failed (%s)", e)


def _append_history_inmem(tenant_id: str, call_id: str, role: str, text: str) -> None:
    key = _mem_key(tenant_id, call_id)
    if key not in _sessions:
        return
    _sessions[key].setdefault("context", {})
    history = _sessions[key]["context"].get("history", [])
    history.append({"role": role, "text": text})
    if len(history) > 6:  # keep last 6 turns only
        history = history[-6:]
    _sessions[key]["context"]["history"] = history


async def append_history(tenant_id: str, call_id: str, role: str, text: str) -> None:
    client = await _get_client()
    if client is None:
        _append_history_inmem(tenant_id, call_id, role, text)
        return
    try:
        sess = await get_session(tenant_id, call_id)
        if not sess:
            return
        sess.setdefault("context", {})
        history = sess["context"].get("history", [])
        history.append({"role": role, "text": text})
        if len(history) > 6:
            history = history[-6:]
        sess["context"]["history"] = history
        await save_session(tenant_id, call_id, sess)
    except Exception as e:
        log.warning("[SESSION] append_history failed (%s)", e)
