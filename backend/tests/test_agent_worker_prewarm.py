"""The agent-worker pre-warm probe must not report a spun-down worker as awake.

This is a regression test for the worst failure this product has shipped: on
2026-07-29 three consecutive test calls (10:25, 10:26, 11:18 UTC) created LiveKit
rooms, the browser joined, and no agent ever came — because `_probe` counted ANY
HTTP response as "worker awake" and then cached that verdict for five minutes.
Render's edge answers for a spun-down free service in ~0.2s; the worker itself
did not actually boot until 11:32.

The contract now: only the worker's own /worker response, carrying OUR
agent_name, counts as awake.
"""
import asyncio

import pytest

from backend.agent.agent_name import AGENT_NAME
from backend.services import agent_worker


class _Resp:
    def __init__(self, status_code, payload=None, raises=False):
        self.status_code = status_code
        self._payload = payload
        self._raises = raises

    def json(self):
        if self._raises:
            raise ValueError("not json")
        return self._payload


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Point the module at a URL and clear the warm cache between tests."""
    monkeypatch.setattr(agent_worker.settings, "agent_worker_url", "https://worker.test", raising=False)
    agent_worker._warm_until = 0.0
    agent_worker._warm_lock = None
    monkeypatch.setattr(agent_worker, "_PROBE_RETRY_GAP_SECONDS", 0.0)
    yield
    agent_worker._warm_until = 0.0
    agent_worker._warm_lock = None


def _stub_get(monkeypatch, responses):
    """Make every httpx GET return the next entry of `responses`.

    An entry may be a _Resp or an Exception instance (raised, i.e. connection
    refused). The last entry repeats once exhausted.
    """
    calls = {"n": 0}
    import httpx

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            i = min(calls["n"], len(responses) - 1)
            calls["n"] += 1
            item = responses[i]
            if isinstance(item, Exception):
                raise item
            return item

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    return calls


@pytest.mark.asyncio
async def test_edge_page_is_not_awake(monkeypatch):
    """A 200 that isn't the worker's JSON must never count as ready."""
    _stub_get(monkeypatch, [_Resp(200, {"status": "starting"})])
    assert await agent_worker._probe_once(1.0) == "responding"


@pytest.mark.asyncio
async def test_non_200_is_not_awake(monkeypatch):
    _stub_get(monkeypatch, [_Resp(502)])
    assert await agent_worker._probe_once(1.0) == "responding"


@pytest.mark.asyncio
async def test_unparseable_body_is_not_awake(monkeypatch):
    _stub_get(monkeypatch, [_Resp(200, raises=True)])
    assert await agent_worker._probe_once(1.0) == "responding"


@pytest.mark.asyncio
async def test_connection_error_is_down(monkeypatch):
    _stub_get(monkeypatch, [RuntimeError("connection refused")])
    assert await agent_worker._probe_once(1.0) == "down"


@pytest.mark.asyncio
async def test_worker_json_with_our_agent_name_is_ready(monkeypatch):
    _stub_get(monkeypatch, [_Resp(200, {"agent_name": AGENT_NAME, "worker_load": 0.02})])
    assert await agent_worker._probe_once(1.0) == "ready"


@pytest.mark.asyncio
async def test_mismatched_agent_name_is_not_ready(monkeypatch):
    """A worker answering under a different name will never take our dispatches."""
    _stub_get(monkeypatch, [_Resp(200, {"agent_name": "some-other-agent"})])
    assert await agent_worker._probe_once(1.0) == "responding"


@pytest.mark.asyncio
async def test_ensure_awake_is_false_and_uncached_when_only_the_edge_answers(monkeypatch):
    """The exact production failure: edge answers, worker is down.

    ensure_worker_awake must return False (so web_calls refuses to create a room)
    AND must not mark the worker warm — a cached lie made the user's immediate
    retry fail too, without even re-probing.
    """
    _stub_get(monkeypatch, [_Resp(200, {"status": "starting"})])
    assert await agent_worker.ensure_worker_awake(timeout=0.3) is False
    assert agent_worker._is_cached_warm() is False


@pytest.mark.asyncio
async def test_ensure_awake_retries_until_the_worker_boots(monkeypatch):
    """A cold start answers from the edge first and from the worker afterwards."""
    _stub_get(monkeypatch, [
        _Resp(502),
        _Resp(200, {"status": "starting"}),
        _Resp(200, {"agent_name": AGENT_NAME}),
    ])
    assert await agent_worker.ensure_worker_awake(timeout=10.0) is True
    assert agent_worker._is_cached_warm() is True


@pytest.mark.asyncio
async def test_warm_cache_short_circuits_the_probe(monkeypatch):
    calls = _stub_get(monkeypatch, [_Resp(200, {"agent_name": AGENT_NAME})])
    assert await agent_worker.ensure_worker_awake(timeout=5.0) is True
    before = calls["n"]
    assert await agent_worker.ensure_worker_awake(timeout=5.0) is True
    assert calls["n"] == before, "a warm worker must not be re-probed"


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_cold_start(monkeypatch):
    """Three callers arriving during a boot must not stack three warm budgets."""
    calls = _stub_get(monkeypatch, [_Resp(200, {"agent_name": AGENT_NAME})])
    results = await asyncio.gather(*(
        agent_worker.ensure_worker_awake(timeout=5.0) for _ in range(3)
    ))
    assert results == [True, True, True]
    assert calls["n"] == 1
