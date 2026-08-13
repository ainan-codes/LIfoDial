"""
The Gemini catalogue must PROBE, because Google's metadata cannot be trusted.

Its Groq twin filters on vendor metadata alone and never makes a generation call.
That is impossible here, and these tests pin the two reasons — both measured
against the live API on 2026-08-13:

1. **Listed does not mean live.** ``gemini-2.5-flash`` and ``gemini-2.0-flash``
   appear in ListModels with completely normal-looking entries and return
   404 "no longer available to new users" when actually called. Those two ids
   were this repo's Gemini defaults, and Gemini is first in
   ``resilience.PROVIDER_ORDER`` — so the failover that Groq rate limits depend
   on was pointing at a 404, and no amount of enumerating models would have shown
   it.

2. **Metadata carries no modality.** Google returns no field distinguishing a
   text model from an image or speech one. ``gemini-3-pro-image`` and
   ``gemini-flash-latest`` have structurally identical entries.

Run: python -m pytest backend/tests/test_gemini_catalog.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest

from backend.services import gemini_catalog as gc


def _entry(name, ctx=1048576, methods=("generateContent", "countTokens"), thinking=True):
    return {
        "name": f"models/{name}",
        "displayName": name,
        "inputTokenLimit": ctx,
        "outputTokenLimit": 65536,
        "supportedGenerationMethods": list(methods),
        "thinking": thinking,
    }


@pytest.fixture(autouse=True)
def _clear_cache():
    gc._cache["models"] = None
    gc._cache["fetched_at"] = 0.0
    yield
    gc._cache["models"] = None
    gc._cache["fetched_at"] = 0.0


def _fake(listing, probe_results):
    """Patch the two IO boundaries: the listing, and the per-model probe."""
    async def _list_raw(api_key):
        return listing

    async def _probe(client, api_key, model_id):
        return probe_results.get(model_id, (False, "not configured in this test"))

    return _list_raw, _probe


@pytest.mark.asyncio
async def test_a_listed_but_retired_model_is_excluded(monkeypatch):
    """The bug that started this. Both entries look identical in the listing; only
    the probe separates them."""
    listing = [_entry("gemini-flash-latest"), _entry("gemini-2.5-flash")]
    probes = {
        "gemini-flash-latest": (True, ""),
        "gemini-2.5-flash": (False, "HTTP 404: no longer available to new users"),
    }
    l, p = _fake(listing, probes)
    monkeypatch.setattr(gc, "_list_raw", l)
    monkeypatch.setattr(gc, "_probe", p)

    ids = [m["id"] for m in await gc.fetch_models("k")]
    assert ids == ["gemini-flash-latest"], (
        "a model that lists cleanly but 404s on call was offered"
    )


@pytest.mark.asyncio
async def test_a_model_that_produces_no_text_is_excluded(monkeypatch):
    """Image and speech models answer the listing identically to text models —
    Google exposes no modality field at all — so only the probe can tell."""
    listing = [_entry("gemini-flash-latest"), _entry("gemini-3-pro-image", ctx=131072)]
    probes = {
        "gemini-flash-latest": (True, ""),
        "gemini-3-pro-image": (False, "produced no text output"),
    }
    l, p = _fake(listing, probes)
    monkeypatch.setattr(gc, "_list_raw", l)
    monkeypatch.setattr(gc, "_probe", p)

    ids = [m["id"] for m in await gc.fetch_models("k")]
    assert ids == ["gemini-flash-latest"]


@pytest.mark.asyncio
async def test_a_tiny_context_model_is_never_probed(monkeypatch):
    """The metadata pre-filter exists to bound the probe cost, so a model it rules
    out must cost no generation call at all."""
    probed: list[str] = []
    listing = [_entry("gemini-flash-latest"), _entry("tts-ish", ctx=8191)]

    async def _list_raw(api_key):
        return listing

    async def _probe(client, api_key, model_id):
        probed.append(model_id)
        return True, ""

    monkeypatch.setattr(gc, "_list_raw", _list_raw)
    monkeypatch.setattr(gc, "_probe", _probe)

    ids = [m["id"] for m in await gc.fetch_models("k")]
    assert ids == ["gemini-flash-latest"]
    assert probed == ["gemini-flash-latest"], f"probed a pre-filtered model: {probed}"


@pytest.mark.asyncio
async def test_a_model_without_generate_content_is_never_probed(monkeypatch):
    probed: list[str] = []
    listing = [_entry("embedder", methods=("embedContent",))]

    async def _list_raw(api_key):
        return listing

    async def _probe(client, api_key, model_id):
        probed.append(model_id)
        return True, ""

    monkeypatch.setattr(gc, "_list_raw", _list_raw)
    monkeypatch.setattr(gc, "_probe", _probe)

    with pytest.raises(gc.GeminiModelsUnavailable):
        await gc.fetch_models("k")
    assert probed == []


@pytest.mark.asyncio
async def test_nothing_usable_raises_rather_than_returning_empty(monkeypatch):
    """"No models" and "we could not ask" look identical in a dropdown. An empty
    successful list would present as the former when it is really the latter."""
    listing = [_entry("gemini-2.5-flash")]
    l, p = _fake(listing, {"gemini-2.5-flash": (False, "HTTP 404")})
    monkeypatch.setattr(gc, "_list_raw", l)
    monkeypatch.setattr(gc, "_probe", p)

    with pytest.raises(gc.GeminiModelsUnavailable):
        await gc.fetch_models("k")


@pytest.mark.asyncio
async def test_no_key_raises_and_never_probes(monkeypatch):
    async def _boom(*a, **k):
        raise AssertionError("must not reach the network without a key")

    monkeypatch.setattr(gc, "_list_raw", _boom)
    with pytest.raises(gc.GeminiModelsUnavailable):
        await gc.fetch_models("")


@pytest.mark.asyncio
async def test_check_model_never_reports_dead_when_it_could_not_ask(monkeypatch):
    """The tri-state, same contract as groq_catalog: collapsing UNKNOWN into DEAD
    would make one Google outage turn every agent on the platform read-only."""
    async def _down(*a, **k):
        raise gc.GeminiModelsUnavailable("simulated outage")

    monkeypatch.setattr(gc, "fetch_models", _down)
    assert await gc.check_model("k", "gemini-flash-latest") == gc.UNKNOWN


@pytest.mark.asyncio
async def test_check_model_reports_dead_only_on_a_real_answer(monkeypatch):
    listing = [_entry("gemini-flash-latest")]
    l, p = _fake(listing, {"gemini-flash-latest": (True, "")})
    monkeypatch.setattr(gc, "_list_raw", l)
    monkeypatch.setattr(gc, "_probe", p)

    assert await gc.check_model("k", "gemini-flash-latest") == gc.LIVE
    assert await gc.check_model("k", "gemini-2.0-flash") == gc.DEAD


@pytest.mark.asyncio
async def test_the_shape_matches_the_groq_catalogue(monkeypatch):
    """One shape for both vendors, so the endpoint and the dropdown do not need to
    know which produced a row."""
    from backend.services import groq_catalog

    l, p = _fake([_entry("gemini-flash-latest")], {"gemini-flash-latest": (True, "")})
    monkeypatch.setattr(gc, "_list_raw", l)
    monkeypatch.setattr(gc, "_probe", p)

    got = (await gc.fetch_models("k"))[0]
    expected = groq_catalog._shape({
        "id": "x", "context_window": 8192, "supported_features": [],
    })
    assert set(got) == set(expected), (
        f"shape drift between the two catalogues: {set(got) ^ set(expected)}"
    )
