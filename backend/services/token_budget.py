"""
backend/services/token_budget.py — language-aware LLM response token budget.

WHY THIS EXISTS
    The receptionist's replies were cut off mid-word in Malayalam and Kannada
    ("രാവ" instead of "രാവിലെ") while Hindi and English were fine. The cause is not
    TTS chunking and not barge-in: it is ``max_tokens`` on the LLM call. Llama
    3.3's tokenizer spends far more tokens on Dravidian and Bengali/Gujarati/
    Gurmukhi/Odia scripts than on Latin or Devanagari, so one fixed cap is a
    generous budget for Hindi and a guillotine for Malayalam.

    Confirmed, not assumed. Live measurements on 2026-08-06 against
    llama-3.3-70b-versatile, tokenising ONE sentence with identical meaning in
    each language (via the API's own ``prompt_tokens``):

        English    38 tokens   1.00x        Malayalam  289 tokens   7.61x
        Hindi      86 tokens   2.26x        Gujarati   292 tokens   7.68x
        Marathi   104 tokens   2.74x        Telugu     293 tokens   7.71x
        Bengali   232 tokens   6.11x        Punjabi    302 tokens   7.95x
                                           Tamil      306 tokens   8.05x
                                           Kannada    344 tokens   9.05x
                                           Odia       457 tokens  12.03x

    And a real completion at the live cap of 150, same prompt, same model:

        English    finish_reason=stop     84/150 tokens
        Hindi      finish_reason=stop    102/150 tokens
        Malayalam  finish_reason=length  150/150   <- cut mid-word
        Kannada    finish_reason=length  150/150   <- cut mid-word

    So the configured number was never "150 tokens of reply"; it was "150 tokens
    of reply IF you happen to be speaking English".

WHAT IT DOES
    Treats the operator's configured value as a budget in ENGLISH-EQUIVALENT
    tokens and converts it into the target language's own currency. Every language
    then gets the same amount of *speech* for the same setting, which is what the
    setting always implied.

WHY NOT JUST RAISE THE GLOBAL CAP
    That would hand English and Hindi a budget they never needed, and the point of
    the cap is to keep a phone receptionist terse. Scaling per language leaves
    Latin/Devanagari untouched — English's multiplier is exactly 1.0 — so the
    sub-800ms turn target for Hindi/English is unaffected. Raising a cap does not
    make a model generate more anyway: English and Hindi already stop naturally at
    ~half the cap, so their behaviour is unchanged by construction.

WHY IT LIVES IN services/ AND NOT IN THE PIPELINE
    Both the voice path (backend/agent/resilience.py::build_llm) and the widget
    chat path (backend/routers/agent_test.py) apply it. The bug was in a shared
    config value, so the fix has to be shared too — that is also the answer to
    "does this affect the Gemini fallback": build_llm passes the same max_tokens to
    every provider, so a small cap truncates on Gemini exactly as on Groq. The
    ratios below are Llama's; Gemini's tokenizer is kinder to Indic scripts, so for
    it these budgets are simply generous rather than wrong.
"""
from __future__ import annotations

import math

#: Measured cost of identical content, relative to English, under
#: llama-3.3-70b-versatile (see the module docstring for the raw numbers).
#: Keyed by the language part of a BCP-47 tag, so "ml-IN" and a bare "ml" agree.
MEASURED_TOKEN_RATIO: dict[str, float] = {
    "en": 1.00,
    "hi": 2.26,
    "mr": 2.74,
    "bn": 6.11,
    "ml": 7.61,
    "gu": 7.68,
    "te": 7.71,
    "pa": 7.95,
    "ta": 8.05,
    "kn": 9.05,
    "or": 12.03,   # Odia. Sarvam spells it od-IN; ISO is or. Both map here.
    "od": 12.03,
}

#: Applied on top of the measured ratio. The ratio came from one sentence; a
#: different sentence, a different name, or a digit spelled out in words all move
#: it. 1.25 keeps a reply that is 25% wordier than the sample from being cut.
SAFETY_MARGIN = 1.25

#: A language nobody measured (e.g. Arabic, which the templates offer) gets the
#: worst measured Indic ratio rather than 1.0 — erring toward a complete sentence.
#: Truncation mid-word is a visibly broken product; a cap that is too generous is
#: invisible, because the model stops on its own.
UNKNOWN_LANGUAGE_RATIO = 8.0

#: Hard ceiling, so a corrupt config or a future 50x ratio cannot ask a provider
#: for an absurd completion. ~2.5k tokens of Malayalam is far longer than any
#: receptionist turn; llama-3.3-70b's 128k context accommodates it comfortably.
MAX_BUDGET_TOKENS = 2500

#: Floor, so a tiny configured value still leaves room for a whole sentence.
MIN_BUDGET_TOKENS = 80


def language_ratio(language: str | None) -> float:
    """Measured token cost of `language` relative to English."""
    code = (language or "").strip().lower().replace("_", "-")
    if not code:
        return MEASURED_TOKEN_RATIO["en"]
    primary = code.split("-")[0]
    return MEASURED_TOKEN_RATIO.get(primary, UNKNOWN_LANGUAGE_RATIO)


def response_token_budget(configured: int | None, language: str | None) -> int:
    """Convert an English-equivalent token budget into `language`'s own currency.

    ``configured`` is the operator's ``max_response_tokens``. English returns it
    unchanged (ratio 1.0), so nothing about the English/Hindi turn changes beyond
    Hindi gaining headroom it does not currently use.
    """
    try:
        base = int(configured or 0)
    except (TypeError, ValueError):
        base = 0
    if base <= 0:
        base = 150  # same fallback the callers used before this existed

    scaled = math.ceil(base * language_ratio(language) * SAFETY_MARGIN)
    return max(MIN_BUDGET_TOKENS, min(MAX_BUDGET_TOKENS, scaled))
