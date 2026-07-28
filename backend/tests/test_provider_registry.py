"""
Tests for the provider registry (backend/services/provider_registry.py).

The registry exists because three lists disagreed about what "this provider
works" means: the AI Platform catalog, the saved-keys table, and the if/elif
chains in the call pipeline. That mismatch produced a hard KeyError crash for
unbuildable TTS providers and silent wrong-provider substitution for STT/LLM.

The most valuable test here is test_registry_matches_the_pipeline_source: it reads
pipeline.py and asserts the registry's buildable sets match the branches that
actually exist. If someone adds a provider branch without updating the registry
(or vice versa), that drift is exactly the bug class this module was created to
kill — so it must fail loudly.

Run: python -m pytest backend/tests/test_provider_registry.py -v
"""
import os
import re
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
from fastapi import HTTPException

from backend.services.provider_registry import (
    BUILDABLE_LLM,
    BUILDABLE_STT,
    BUILDABLE_TTS,
    UNSUPPORTED,
    is_buildable,
    unsupported_reason,
    validate_or_raise,
)

PIPELINE = Path(__file__).resolve().parents[1] / "agent" / "pipeline.py"
RESILIENCE = Path(__file__).resolve().parents[1] / "agent" / "resilience.py"


# ── The providers that actually caused outages ────────────────────────────────

@pytest.mark.parametrize("provider", ["playht", "azure_tts", "deepgram_aura", "resemble"])
def test_crash_causing_tts_providers_are_rejected(provider):
    """These fell through to the Sarvam else: and raised KeyError mid-job."""
    assert not is_buildable("tts", provider)
    with pytest.raises(HTTPException) as exc:
        validate_or_raise("tts", provider)
    assert exc.value.status_code == 422


@pytest.mark.parametrize("provider", ["google_stt", "azure_stt"])
def test_silently_substituted_stt_providers_are_rejected(provider):
    """These resolved a key, passed the deaf-agent guard, then became Sarvam."""
    assert not is_buildable("stt", provider)
    with pytest.raises(HTTPException):
        validate_or_raise("stt", provider)


@pytest.mark.parametrize("provider", ["anthropic", "mistral", "cerebras", "ollama"])
def test_llm_providers_that_silently_ran_gemini_are_rejected(provider):
    assert not is_buildable("llm", provider)
    with pytest.raises(HTTPException):
        validate_or_raise("llm", provider)


# ── Working providers must keep working ───────────────────────────────────────

@pytest.mark.parametrize("category,provider", [
    *(("llm", p) for p in ["gemini", "groq", "openai", "deepseek"]),
    *(("stt", p) for p in ["sarvam", "deepgram", "openai", "whisper", "elevenlabs", "assemblyai"]),
    *(("tts", p) for p in ["sarvam", "elevenlabs", "openai_tts", "cartesia"]),
])
def test_supported_providers_are_accepted(category, provider):
    assert is_buildable(category, provider)
    validate_or_raise(category, provider)  # must not raise


def test_custom_openai_compatible_llm_is_allowed_only_with_base_url():
    assert not is_buildable("llm", "my-vllm-box")
    assert is_buildable("llm", "my-vllm-box", has_base_url=True)
    validate_or_raise("llm", "my-vllm-box", has_base_url=True)
    with pytest.raises(HTTPException):
        validate_or_raise("llm", "my-vllm-box", has_base_url=False)


def test_none_and_blank_are_noops():
    """PATCH fields are optional — validation must not fire on absent values."""
    validate_or_raise("tts", None)
    validate_or_raise("tts", "")
    validate_or_raise("tts", "   ")


def test_non_pipeline_categories_are_not_policed():
    """telephony/his/voice_clone have no pipeline branch; don't block them."""
    assert is_buildable("telephony", "vobiz")
    assert is_buildable("his", "oxzygen")


def test_every_unsupported_entry_has_a_real_reason():
    for (category, provider), reason in UNSUPPORTED.items():
        assert reason and len(reason) > 20, f"{category}/{provider} needs a usable reason"
        assert provider not in {
            "llm": BUILDABLE_LLM, "stt": BUILDABLE_STT, "tts": BUILDABLE_TTS,
        }[category], f"{provider} is listed as BOTH buildable and unsupported"
        assert unsupported_reason(category, provider) == reason


# ── Anti-drift: the registry must describe the real code ──────────────────────

def _branch_providers(source: str, var: str) -> set[str]:
    """Collect the string literals compared against `var` in if/elif branches."""
    found = set()
    for m in re.finditer(rf"{var}\s*==\s*[\"']([\w-]+)[\"']", source):
        found.add(m.group(1))
    for m in re.finditer(rf"{var}\s+in\s+\(([^)]*)\)", source):
        found.update(re.findall(r"[\"']([\w-]+)[\"']", m.group(1)))
    return found


def test_registry_matches_the_pipeline_source():
    """BUILDABLE_STT/TTS must equal the branches pipeline.py really implements.

    Sarvam is the `else:` fallback in both chains rather than an explicit branch,
    so it is expected to be absent from the parsed set.
    """
    src = PIPELINE.read_text(encoding="utf-8")

    stt_branches = _branch_providers(src, "stt_provider") | {"sarvam"}
    tts_branches = _branch_providers(src, "tts_provider") | {"sarvam"}

    missing_stt = BUILDABLE_STT - stt_branches
    missing_tts = BUILDABLE_TTS - tts_branches
    assert not missing_stt, (
        f"registry claims STT support with no pipeline branch: {sorted(missing_stt)}"
    )
    assert not missing_tts, (
        f"registry claims TTS support with no pipeline branch: {sorted(missing_tts)}"
    )

    # And the reverse: a branch nobody registered would be selectable-but-blocked.
    unregistered_stt = stt_branches - BUILDABLE_STT - {"sarvam"}
    unregistered_tts = tts_branches - BUILDABLE_TTS - {"sarvam"}
    assert not unregistered_stt, f"pipeline builds unregistered STT: {sorted(unregistered_stt)}"
    assert not unregistered_tts, f"pipeline builds unregistered TTS: {sorted(unregistered_tts)}"


def test_registry_matches_resilience_llm_order():
    """BUILDABLE_LLM must equal resilience.PROVIDER_ORDER, the failover pool."""
    from backend.agent.resilience import PROVIDER_DEFAULT_MODEL, PROVIDER_ORDER

    assert set(PROVIDER_ORDER) == set(BUILDABLE_LLM), (
        f"registry {sorted(BUILDABLE_LLM)} != PROVIDER_ORDER {sorted(PROVIDER_ORDER)}"
    )
    # Every pool member needs a default model or fallback picks None.
    for p in PROVIDER_ORDER:
        assert PROVIDER_DEFAULT_MODEL.get(p), f"{p} has no default model"


def test_fallback_targets_are_themselves_buildable():
    """A fallback that can't be built would just move the crash."""
    from backend.services.provider_registry import FALLBACK_BY_CATEGORY

    for category, provider in FALLBACK_BY_CATEGORY.items():
        assert is_buildable(category, provider), f"{category} fallback {provider} is not buildable"
