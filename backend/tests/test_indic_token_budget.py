"""
Malayalam/Kannada replies were cut off mid-word; Hindi/English were not.

Root cause, confirmed against the live model on 2026-08-06 before any code
changed (numbers in backend/services/token_budget.py): llama-3.3-70b spends ~7.6x
more tokens on Malayalam and ~9x more on Kannada than on English for the SAME
sentence, so the flat max_tokens=150 that leaves Hindi finishing at 102 tokens
stopped Malayalam at exactly 150 with finish_reason="length".

These tests pin the shape of the fix, not the vendor's tokenizer:
  * English is untouched (the latency non-negotiable)
  * Dravidian/Bengali-script languages get materially more room than Hindi
  * the budget is bounded, so no config can ask for an absurd completion
  * both the voice path and the widget path use it — the bug was in shared config

Run: python -m pytest backend/tests/test_indic_token_budget.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-token-budget")
os.environ.setdefault("ENVIRONMENT", "development")

import pytest

from backend.services.token_budget import (
    MAX_BUDGET_TOKENS,
    MEASURED_TOKEN_RATIO,
    UNKNOWN_LANGUAGE_RATIO,
    language_ratio,
    response_token_budget,
)

#: The cap both live agents were configured with when the bug was reported.
LIVE_CONFIGURED = 150


def test_english_budget_is_exactly_what_was_configured_times_margin():
    """English must not gain a bigger budget than before in any meaningful way —
    raising it for languages that already fit is how you add latency to the
    languages that currently work."""
    assert language_ratio("en-IN") == 1.0
    # Only the safety margin applies, nothing language-specific.
    assert response_token_budget(LIVE_CONFIGURED, "en-IN") == pytest.approx(
        LIVE_CONFIGURED * 1.25, rel=0.01
    )


def test_malayalam_and_kannada_clear_the_length_that_truncated_them():
    """The measured completion that got cut needed 289 (ml) / 344 (kn) tokens.
    The budget must exceed that with room to spare, or the fix does not fix it."""
    ml = response_token_budget(LIVE_CONFIGURED, "ml-IN")
    kn = response_token_budget(LIVE_CONFIGURED, "kn-IN")
    assert ml > 289, f"Malayalam budget {ml} does not clear the 289-token reply that was cut"
    assert kn > 344, f"Kannada budget {kn} does not clear the 344-token reply that was cut"
    # And comfortably more than the 150 that truncated them.
    assert ml > 3 * LIVE_CONFIGURED and kn > 3 * LIVE_CONFIGURED


def test_every_language_gets_the_same_budget_in_its_own_currency():
    """That is the whole idea: the configured number means "this much speech",
    not "this many Latin-script tokens"."""
    en = response_token_budget(LIVE_CONFIGURED, "en-IN")
    for code, ratio in MEASURED_TOKEN_RATIO.items():
        got = response_token_budget(LIVE_CONFIGURED, f"{code}-IN")
        if got >= MAX_BUDGET_TOKENS:
            continue  # clamped; checked separately
        assert got == pytest.approx(en * ratio, rel=0.02), (
            f"{code}: {got} is not {ratio}x the English budget"
        )


def test_ordering_follows_the_measurements():
    """Sanity on the table itself: scripts that measured more expensive must get
    more room, and Hindi must not be treated like Malayalam."""
    b = {c: response_token_budget(LIVE_CONFIGURED, c)
         for c in ("en-IN", "hi-IN", "mr-IN", "bn-IN", "ml-IN", "ta-IN", "kn-IN", "od-IN")}
    assert b["en-IN"] < b["hi-IN"] < b["mr-IN"] < b["bn-IN"] < b["ml-IN"] < b["ta-IN"] < b["kn-IN"]
    assert b["od-IN"] >= b["kn-IN"]
    # Hindi worked fine at 150 and must not be inflated to Dravidian levels.
    assert b["hi-IN"] < b["ml-IN"] / 2


def test_the_budget_is_bounded_and_floored():
    assert response_token_budget(100_000, "kn-IN") == MAX_BUDGET_TOKENS
    assert response_token_budget(1, "en-IN") >= 80
    # Garbage in must not produce a zero budget (a 0 cap means an empty reply).
    for junk in (None, 0, -5, "", "abc"):
        assert response_token_budget(junk, "ml-IN") > 289


def test_unmeasured_languages_err_towards_completeness():
    """A language nobody measured (Arabic is offered by the templates) must not
    silently fall back to the English ratio and get truncated."""
    assert language_ratio("ar-SA") == UNKNOWN_LANGUAGE_RATIO
    assert response_token_budget(LIVE_CONFIGURED, "ar-SA") > 289


def test_language_tag_forms_are_tolerated():
    """Codes arrive as 'ml-IN', 'ml', 'ml_IN' and occasionally uppercase."""
    for form in ("ml-IN", "ml", "ml_IN", "ML-IN", " ml-in "):
        assert language_ratio(form) == MEASURED_TOKEN_RATIO["ml"], form
    # Odia is 'od-IN' at Sarvam and 'or' in ISO; both must resolve.
    assert language_ratio("od-IN") == language_ratio("or-IN")
    # No language at all behaves as English rather than crashing.
    assert language_ratio(None) == 1.0


def test_the_voice_path_uses_it_for_every_provider():
    """build_llm is the single point where max_tokens reaches Gemini, Groq, OpenAI
    and DeepSeek — which is why the cap truncated on the Gemini fallback too. The
    scaling has to be there, not in one provider's branch."""
    import inspect

    from backend.agent import resilience

    src = inspect.getsource(resilience.build_llm)
    assert "response_token_budget" in src, "build_llm no longer scales the budget"
    # CALLED once (the import mentions the name too), and above the provider
    # branches — so every provider inherits the same scaled cap.
    assert src.count("response_token_budget(") == 1
    assert src.index("response_token_budget(") < src.index('if provider == "gemini"')


def test_the_widget_chat_path_uses_it_too():
    import inspect

    from backend.routers import agent_test

    src = inspect.getsource(agent_test)
    assert "response_token_budget" in src, (
        "the in-browser/widget chat path still applies a flat cap, so it will "
        "truncate Malayalam exactly as the voice path did"
    )


@pytest.mark.asyncio
async def test_build_llm_hands_the_scaled_cap_to_the_service():
    """End to end through the real build path: a Malayalam agent config must reach
    the provider with the scaled cap, not the configured 150."""
    from backend.agent.resilience import build_llm

    cfg = {"llm_temperature": 0.3, "max_response_tokens": LIVE_CONFIGURED, "language": "ml-IN"}
    svc = await build_llm("groq", "sk-test-not-used", "llama-3.3-70b-versatile", "sys", cfg)
    settings = getattr(svc, "_settings", None) or getattr(svc, "settings", None)
    max_tokens = None
    if settings is not None:
        max_tokens = getattr(settings, "max_tokens", None)
        if max_tokens is None and isinstance(settings, dict):
            max_tokens = settings.get("max_tokens")
    if max_tokens is None:
        pytest.skip("pipecat does not expose settings on the service instance")
    assert int(max_tokens) > 289, f"service built with {max_tokens}, which still truncates Malayalam"
