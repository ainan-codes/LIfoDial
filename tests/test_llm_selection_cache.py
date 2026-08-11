"""Setup-time LLM provider selection cache (backend/agent/resilience.py).

The probe is an HTTP GET the caller waits through before hearing the greeting, so
its result is cached per configured model. The cache must not cost the resilience
property it was protecting: a stale entry has to expire.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_llm_selection_cache.db")

import pytest

from backend.agent import resilience
from backend.services import llm_failover


@pytest.fixture(autouse=True)
def _clean_cache():
    resilience.reset_llm_selection_cache()
    # Cooldowns are process-wide and outlive a test, and select_llm_provider now
    # consults them — so without this a rate limit recorded by one test silently
    # moves another test's expected model.
    llm_failover.reset_cooldowns()
    yield
    resilience.reset_llm_selection_cache()
    llm_failover.reset_cooldowns()


#: The cache is keyed by configured PROVIDER + model ("::model" when no provider is
#: configured), not by model alone — a bare model key made a gemini-configured agent
#: and a groq-configured one share one entry.
def _cache_key(model: str, provider: str = "") -> str:
    return f"{provider}::{model}"


@pytest.fixture
def probe_counter(monkeypatch):
    calls = []

    async def fake_probe(provider, key, base_url=None):
        calls.append(provider)
        return provider == "groq"          # only groq is reachable

    monkeypatch.setattr(resilience, "_probe", fake_probe)
    # `_resolve_key`, not `_key_for`: the resolver was renamed when BYOK provider
    # keys moved to a DB-first lookup (51bf293), and it is async now.
    async def fake_key(provider):
        return "k" if provider == "groq" else ""

    monkeypatch.setattr(resilience, "_resolve_key", fake_key)
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
    key = _cache_key(cfg["llm_model"])
    ts, value = resilience._selection_cache[key]
    resilience._selection_cache[key] = (
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
    assert resilience._selection_cache[_cache_key("gemini-2.5-flash")][1] == (provider, key, model)


# ── Rate-limited models are skipped at SETUP, and that is NOT cached ───────────


@pytest.mark.asyncio
async def test_a_rate_limited_model_is_skipped_at_call_setup(probe_counter):
    """The probe cannot see an exhausted token budget — GET /v1/models costs no
    tokens and answers 200 either way — so a call would start on a model that 429s
    on the caller's first question. A recorded cooldown is what prevents that."""
    cfg = {"llm_provider": "groq", "llm_model": "llama-3.3-70b-versatile"}

    provider, _key, model = await resilience.select_llm_provider(cfg)
    assert model == "llama-3.3-70b-versatile", "nothing is benched yet"

    llm_failover.mark_rate_limited("llama-3.3-70b-versatile", retry_after=600)
    provider, _key, model = await resilience.select_llm_provider(cfg)
    assert provider == "groq"
    assert model == "openai/gpt-oss-120b", "must start on a model that still has budget"


@pytest.mark.asyncio
async def test_the_cooldown_swap_is_not_baked_into_the_cache(probe_counter):
    """The probe result is cacheable for minutes; "does this model have budget right
    now?" is not. Caching the swap would pin the call to the fallback model for the
    cache's full TTL after the primary's budget had already refilled."""
    cfg = {"llm_provider": "groq", "llm_model": "llama-3.3-70b-versatile"}

    llm_failover.mark_rate_limited("llama-3.3-70b-versatile", retry_after=600)
    _p, _k, model = await resilience.select_llm_provider(cfg)
    assert model == "openai/gpt-oss-120b"

    # The CONFIGURED model is what the cache holds, so the moment the budget
    # refills the agent is back on the model its clinic chose.
    cached = resilience._selection_cache[_cache_key("llama-3.3-70b-versatile", "groq")][1]
    assert cached[2] == "llama-3.3-70b-versatile"

    llm_failover.reset_cooldowns()
    _p, _k, model = await resilience.select_llm_provider(cfg)
    assert model == "llama-3.3-70b-versatile", "recovery must not wait for the cache to expire"


@pytest.mark.asyncio
async def test_no_reachable_provider_is_not_cached(monkeypatch):
    async def all_dead(provider, key):
        return False

    monkeypatch.setattr(resilience, "_probe", all_dead)
    with pytest.raises(RuntimeError):
        await resilience.select_llm_provider({"llm_model": "gemini-2.5-flash"})
    assert resilience._selection_cache == {}, "a total failure must not be cached"
