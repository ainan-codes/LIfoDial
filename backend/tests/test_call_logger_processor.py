"""
Regression tests for the CallLoggerProcessor init bug (audit FIX 3).

The bug: _latency_samples / _last_tts_start were initialized inside
begin_consent_gate() instead of __init__, so any call whose consent plan was
NOT "require" (i.e. almost every call) raised AttributeError in _on_metrics
and — fatally — in _finalize_call, which meant duration/transcript/latency
were never written to call_records.

These tests exercise both paths (consent and non-consent) WITHOUT a DB:
_finalize_call's DB write is fired via asyncio.create_task on a patched
helper, so we assert the computation completes and the write is dispatched
with the right values.

Run: python -m pytest backend/tests/test_call_logger_processor.py -v
"""

import asyncio
import time
from unittest.mock import patch

import pytest

from backend.agent.processors import call_logger_processor as clp_mod
from backend.agent.processors.call_logger_processor import CallLoggerProcessor


def _make_processor(**overrides) -> CallLoggerProcessor:
    kwargs = dict(
        tenant_id="tenant-1",
        agent_id="agent-1",
        call_meta={"call_record_id": "call-123", "caller_phone": "+911234567890"},
        agent_config={},
    )
    kwargs.update(overrides)
    return CallLoggerProcessor(**kwargs)


class _FakeMetric:
    def __init__(self, name: str, value: float) -> None:
        self.name = name
        self.value = value


class _FakeMetricsFrame:
    def __init__(self, samples: list[float]) -> None:
        self.data = [_FakeMetric("llm_ttfb", v) for v in samples]


def test_latency_attrs_exist_without_consent_gate():
    """Non-consent path: attributes must exist straight out of __init__."""
    proc = _make_processor()
    assert proc._latency_samples == []
    assert proc._last_tts_start is None


def test_on_metrics_collects_samples_without_consent_gate():
    """_on_metrics must not raise (previously AttributeError) and must collect."""
    proc = _make_processor()
    proc._on_metrics(_FakeMetricsFrame([0.5, 0.7]))  # seconds
    assert proc._latency_samples == [500.0, 700.0]  # converted to ms


@pytest.mark.asyncio
async def test_finalize_completes_without_consent_gate():
    """The non-consent finalize path must compute stats and dispatch the DB
    write — this is exactly what the init bug broke."""
    proc = _make_processor()
    proc._call_start_time = time.time() - 42  # simulate a 42s call
    proc._turn_count = 3
    proc._transcript = [{"turn": 1, "role": "user", "text": "hello"}]
    proc._on_metrics(_FakeMetricsFrame([0.4, 0.6]))

    captured: dict = {}

    async def fake_finalize_record(**kwargs):
        captured.update(kwargs)

    async def _noop(*args, **kwargs):
        return None

    with patch.object(clp_mod, "_finalize_call_record", fake_finalize_record), \
         patch.object(clp_mod, "_deduct_call_credits", _noop), \
         patch.object(clp_mod, "_run_post_call_evaluation", _noop):
        await proc._finalize_call()
        # _finalize_call fires the record write via create_task — let it run.
        await asyncio.sleep(0.05)

    assert captured["call_record_id"] == "call-123"
    assert captured["duration_seconds"] >= 42
    assert captured["turn_count"] == 3
    assert captured["avg_latency_ms"] == pytest.approx(500.0)
    assert captured["transcript"][0]["text"] == "hello"


@pytest.mark.asyncio
async def test_finalize_completes_with_consent_gate():
    """Consent path must keep working after the fix (attrs must not be
    re-initialized or lost by begin_consent_gate)."""
    proc = _make_processor()
    proc._on_metrics(_FakeMetricsFrame([0.2]))  # sample BEFORE gate opens
    proc.begin_consent_gate(decline_message="bye", resume_message="hi")
    assert proc._consent_pending is True
    # begin_consent_gate must NOT wipe previously collected samples
    assert proc._latency_samples == [200.0]

    captured: dict = {}

    async def fake_finalize_record(**kwargs):
        captured.update(kwargs)

    async def _noop(*args, **kwargs):
        return None

    with patch.object(clp_mod, "_finalize_call_record", fake_finalize_record), \
         patch.object(clp_mod, "_deduct_call_credits", _noop), \
         patch.object(clp_mod, "_run_post_call_evaluation", _noop):
        await proc._finalize_call()
        await asyncio.sleep(0.05)

    assert captured["call_record_id"] == "call-123"
    assert captured["avg_latency_ms"] == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_finalize_runs_on_cancelframe_hangup():
    """A real caller hangup produces a CancelFrame (task.cancel()), NOT an
    EndFrame. Finalization must run on it — previously it only keyed on
    EndFrame, so hung-up calls stayed 'active' forever."""
    from pipecat.frames.frames import CancelFrame
    from pipecat.processors.frame_processor import FrameDirection

    proc = _make_processor()
    proc._call_start_time = time.time() - 12
    proc._turn_count = 2

    captured: dict = {}

    async def fake_finalize_record(**kwargs):
        captured.update(kwargs)

    async def _noop(*a, **k):
        return None

    with patch.object(clp_mod, "_finalize_call_record", fake_finalize_record), \
         patch.object(clp_mod, "_deduct_call_credits", _noop), \
         patch.object(clp_mod, "_run_post_call_evaluation", _noop), \
         patch.object(proc, "push_frame", new=_noop):
        await proc.process_frame(CancelFrame(), FrameDirection.DOWNSTREAM)
        # finalize runs as a task; the entrypoint awaits it via wait_finalized()
        assert await proc.wait_finalized(timeout=5) is True

    assert captured.get("call_record_id") == "call-123"
    assert captured["duration_seconds"] >= 12
    assert proc._finalized is True


@pytest.mark.asyncio
async def test_finalize_runs_only_once():
    """EndFrame after CancelFrame (or duplicates) must not double-write."""
    from pipecat.frames.frames import CancelFrame, EndFrame
    from pipecat.processors.frame_processor import FrameDirection

    proc = _make_processor()
    calls = {"n": 0}

    async def fake_finalize_record(**kwargs):
        calls["n"] += 1

    async def _noop(*a, **k):
        return None

    with patch.object(clp_mod, "_finalize_call_record", fake_finalize_record), \
         patch.object(clp_mod, "_deduct_call_credits", _noop), \
         patch.object(clp_mod, "_run_post_call_evaluation", _noop), \
         patch.object(proc, "push_frame", new=_noop):
        await proc.process_frame(CancelFrame(), FrameDirection.DOWNSTREAM)
        await proc.process_frame(EndFrame(), FrameDirection.DOWNSTREAM)
        await proc.wait_finalized(timeout=5)

    assert calls["n"] == 1  # finalized exactly once


@pytest.mark.asyncio
async def test_consent_decline_words_still_gate():
    """Sanity: consent gating behavior unchanged by the init fix."""
    proc = _make_processor()
    proc.begin_consent_gate(decline_message="bye", resume_message=None)

    class _FakeTask:
        def __init__(self):
            self.cancelled = False
        async def queue_frames(self, frames):
            pass
        async def cancel(self):
            self.cancelled = True

    proc.task = _FakeTask()
    await proc._on_user_speech("no, don't record me")
    assert proc._consent_pending is False
    assert proc._ending_call is True
