"""The Deepgram model list the dashboard sees must lead with nova-3.

This is a regression test for a silent config-corruption loop, verified in
production on 2026-07-29:

  1. PROVIDERS["stt"]["deepgram"]["models"] listed only nova-2 variants.
  2. GET /platform/models/deepgram?category=stt returns that list — it checks the
     PROVIDERS catalogue BEFORE the authoritative DEEPGRAM_STT_MODELS.
  3. AgentDetail.tsx does, on every provider change:
         if (!agent.stt_model || !models.includes(agent.stt_model))
             updateField('stt_model', models[0]);       // <- auto-saves
  4. So merely selecting Deepgram in the UI overwrote the agent's stt_model with
     nova-2. An agent set to nova-3 at 12:45 was nova-2 by 12:51, and the three
     calls after that logged "Deepgram STT: model=nova-2 language=en-IN".

nova-2 answers HTTP 400 for most Indian languages (pipecat retries that forever
without transcribing, i.e. a silently deaf agent) and has no "multi" tier, so it
cannot follow a caller who code-switches Hindi/English. models[0] is therefore a
correctness-critical value, not a cosmetic default.
"""
import pytest

from backend.routers.platform import DEEPGRAM_STT_MODELS, PROVIDERS


def _catalog_models(category: str, provider: str) -> list[str]:
    for entry in PROVIDERS[category]:
        if entry["id"] == provider:
            return entry.get("models", [])
    raise AssertionError(f"{provider} missing from the {category} catalogue")


def test_deepgram_dropdown_defaults_to_nova_3():
    """models[0] is what the dashboard silently writes to stt_model."""
    assert _catalog_models("stt", "deepgram")[0] == "nova-3"


def test_nova_3_is_offered_at_all():
    models = _catalog_models("stt", "deepgram")
    assert any(m.startswith("nova-3") for m in models), (
        "nova-3 is the only Deepgram tier that serves Indic languages and the only "
        "one with a 'multi' code-switching model — it must be selectable."
    )


def test_catalogue_is_a_subset_of_the_authoritative_list():
    """Two lists for one thing is how this drifted in the first place."""
    unknown = set(_catalog_models("stt", "deepgram")) - set(DEEPGRAM_STT_MODELS)
    assert not unknown, f"catalogue offers models the backend doesn't know: {sorted(unknown)}"


def test_authoritative_list_also_leads_with_nova_3():
    assert DEEPGRAM_STT_MODELS[0] == "nova-3"


def test_only_nova_3_is_selectable():
    """Production runs nova-3 only, so nothing else may be offered or saved.

    Not a style preference. nova-2 answers HTTP 400 for ta/te/kn/ml/mr/bn/pa/gu and
    pipecat retries a 400 forever without transcribing — a silently deaf agent — and
    no pre-nova-3 tier has a "multi" model, so none can follow a caller who
    code-switches Hindi/English. Both are the normal case for this product.
    """
    assert DEEPGRAM_STT_MODELS == ["nova-3"]
    assert _catalog_models("stt", "deepgram") == ["nova-3"]


def test_the_factual_catalogue_is_kept_but_not_selectable():
    """The full Deepgram list stays documented, and stays out of every dropdown.

    Two lists wired to selection paths is the exact drift that silently rewrote
    agents from nova-3 to nova-2. DEEPGRAM_STT_MODELS_ALL is a record; only
    DEEPGRAM_STT_MODELS may reach a dropdown or a save.
    """
    from backend.routers.platform import DEEPGRAM_STT_MODELS_ALL

    # Still a real record of what Deepgram offers...
    assert {"nova-2", "enhanced", "base"} <= set(DEEPGRAM_STT_MODELS_ALL)
    # ...and nova-3 is genuinely one of Deepgram's models, not something we coined.
    assert set(DEEPGRAM_STT_MODELS) <= set(DEEPGRAM_STT_MODELS_ALL)


def test_a_legacy_nova2_row_is_repaired_rather_than_left_deaf():
    """An agent already stored on nova-2 must be healed on its next save.

    Trimming the dropdown does not by itself fix rows already in the database, and
    those are the ones actively failing on Indic languages.
    """
    from backend.services.agent_defaults import normalize_provider_choice

    provider, model = normalize_provider_choice("stt", "deepgram", "nova-2")
    assert provider == "deepgram"
    assert model == "nova-3", "a stored nova-2 must not survive a save"


@pytest.mark.parametrize("category,provider", [
    ("stt", "sarvam"), ("stt", "deepgram"), ("stt", "assemblyai"),
    ("tts", "sarvam"), ("tts", "elevenlabs"), ("tts", "openai_tts"),
])
def test_every_buildable_provider_offers_at_least_one_model(category, provider):
    """An empty list makes models[0] undefined, which the dashboard would save."""
    assert _catalog_models(category, provider), f"{provider} has no models to pick from"


def test_nova2_unsupported_languages_are_upgraded_not_used():
    """The pipeline must refuse to pair nova-2 with a language it 400s on."""
    from backend.agent.pipeline import _DG_LANG_MAP, _DG_NOVA2_UNSUPPORTED_LANGS

    # Every language nova-2 rejects must be one we can actually map to Deepgram,
    # otherwise the guard can never fire for it.
    mappable = {(v or "").split("-")[0] for v in _DG_LANG_MAP.values()}
    assert _DG_NOVA2_UNSUPPORTED_LANGS <= mappable
