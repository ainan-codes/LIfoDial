"""Setup-time LLM provider selection cache (backend/agent/resilience.py).

The probe is an HTTP GET the caller waits through before hearing the greeting, so
its result is cached per configured model. The cache must not cost the resilience
property it was protecting: a stale entry has to expire.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_llm_selection_cache.db")

import pytest

from backend.agent import resilience


@pytest.fixture(autouse=True)
def _clean_cache():
    resilience.reset_llm_selection_cache()
    yield
    resilience.reset_llm_selection_cache()


@pytest.fixture
def probe_counter(monkeypatch):
    calls = []

    async def fake_probe(provider, key):
        calls.append(provider)
        return provider == "groq"          # only groq is reachable

    monkeypatch.setattr(resilience, "_probe", fake_probe)
    monkeypatch.setattr(resilience, "_key_for", lambda p: "k" if p == "groq" else "")
    return calls


@pytest.mark.asyncio
async def test_second_call_skips_the_probe(probe_counter):
    cfg = {"llm_model": "gemini-2.5-flash"}
    first = await resilience.select_llm_provider(cfg)
    probes_after_first = len(probe_counter)
    second = await resilience.select_llm_provider(cfg)

    assert first == second
    assert probes_after_first > 0, "the first call must actually probe"
    assert len(probe_counter) == probes_after_first, "the second call must not probe"


@pytest.mark.asyncio
async def test_expired_entry_reprobes(probe_counter, monkeypatch):
    cfg = {"llm_model": "gemini-2.5-flash"}
    await resilience.select_llm_provider(cfg)
    probes = len(probe_counter)

    # Age the entry past its TTL.
    ts, value = resilience._selection_cache[cfg["llm_model"]]
    resilience._selection_cache[cfg["llm_model"]] = (
        ts - resilience._SELECTION_TTL_SECS - 1, value,
    )
    await resilience.select_llm_provider(cfg)
    assert len(probe_counter) > probes, "a stale entry must be re-probed"


@pytest.mark.asyncio
async def test_cache_is_keyed_per_configured_model(probe_counter):
    await resilience.select_llm_provider({"llm_model": "gemini-2.5-flash"})
    probes = len(probe_counter)
    await resilience.select_llm_provider({"llm_model": "gpt-4o-mini"})

    assert len(probe_counter) > probes, "a different configured model must be probed"
    assert len(resilience._selection_cache) == 2


@pytest.mark.asyncio
async def test_fallback_result_is_what_gets_cached(probe_counter):
    """Configured gemini, only groq reachable → the cached entry is groq."""
    provider, key, model = await resilience.select_llm_provider({"llm_model": "gemini-2.5-flash"})
    assert provider == "groq"
    assert model == resilience.PROVIDER_DEFAULT_MODEL["groq"]
    assert resilience._selection_cache["gemini-2.5-flash"][1] == (provider, key, model)


@pytest.mark.asyncio
async def test_no_reachable_provider_is_not_cached(monkeypatch):
    async def all_dead(provider, key):
        return False

    monkeypatch.setattr(resilience, "_probe", all_dead)
    with pytest.raises(RuntimeError):
        await resilience.select_llm_provider({"llm_model": "gemini-2.5-flash"})
    assert resilience._selection_cache == {}, "a total failure must not be cached"
