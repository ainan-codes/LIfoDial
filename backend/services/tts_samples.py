"""
backend/services/tts_samples.py — what a voice actually SAYS in a preview.

Why this exists
---------------
"Play Sample" is supposed to answer one question: *what does this voice sound
like speaking <language>?* It could not answer that, because the language code
and the text being spoken came from different places and nothing kept them
consistent:

  * The Voice Library sent the voice's English catalogue blurb as the text — so
    filtering to Kannada and pressing play asked Sarvam to read the literal
    string "Soft Hindi female (Recommended)" with kn-IN phonetics. Real audio,
    completely useless as a Kannada sample.
  * The agent's Voice Configuration sent `agent.first_message` — the agent's own
    greeting, which has nothing to do with the Language dropdown next to the
    button. Switching that dropdown to Malayalam changed the language code and
    left the text alone.
  * Both backend preview endpoints defaulted to a hardcoded English sentence.
  * A fourth list of sample sentences lived in the agent-creation wizard
    (4 languages), and a fifth in backend/routers/providers.py (11 languages,
    but romanised — "Namaskara! Naanu nimage hege sahaya maadali?" — on an
    endpoint no frontend calls).

So: one table, here, and the language alone decides the text. Callers that pass
an explicit `text` still win (the STT-provider announcements and the chat
playback in AgentDetail rely on that); callers that pass none now get a sentence
in the right language instead of English.

About the sentences
-------------------
Each is written in the language's own script — not romanised, which is what the
previous set was and which produces an accent impression rather than a language
one. Each is something a clinic receptionist would plausibly open with, and each
is kept short so a preview stays ~2-3 seconds.

Grammatical gender: Hindi, Marathi, Gujarati and Punjabi mark the speaker's
gender on the verb ("मैं मदद कर सकती हूँ" is female, "सकता हूँ" is male), so a
single "how can I help you" sentence would be wrong for roughly half the voices.
Those four use an "I am here to help you" construction instead, whose copula is
gender-neutral, so one sentence is correct for every voice. Dravidian languages,
Bengali and Odia do not mark gender on the verb here, so they use the more
natural "how can I help you?" directly.
"""
from __future__ import annotations

#: language code -> the sentence a preview speaks, in that language's script.
TTS_SAMPLE_TEXT: dict[str, str] = {
    # ── English ───────────────────────────────────────────────────────────────
    "en-IN": "Hello! Thank you for calling. How may I help you today?",
    "en-US": "Hello! Thank you for calling. How may I help you today?",
    "en-GB": "Hello! Thank you for calling. How may I help you today?",

    # ── Gender-neutral construction (verb would otherwise mark the speaker) ───
    # "Hello! I am here to help you."
    "hi-IN": "नमस्ते! मैं आपकी सहायता के लिए यहाँ हूँ।",
    "mr-IN": "नमस्कार! मी तुमच्या मदतीसाठी येथे आहे.",
    "gu-IN": "નમસ્તે! હું તમારી મદદ માટે અહીં છું.",
    "pa-IN": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ! ਮੈਂ ਤੁਹਾਡੀ ਮਦਦ ਲਈ ਇੱਥੇ ਹਾਂ।",

    # ── "Hello! How can I help you?" (no gender marking on the verb) ──────────
    "ta-IN": "வணக்கம்! நான் உங்களுக்கு எப்படி உதவ முடியும்?",
    "te-IN": "నమస్కారం! నేను మీకు ఎలా సహాయం చేయగలను?",
    "kn-IN": "ನಮಸ್ಕಾರ! ನಾನು ನಿಮಗೆ ಹೇಗೆ ಸಹಾಯ ಮಾಡಬಹುದು?",
    "ml-IN": "നമസ്കാരം! ഞാൻ നിങ്ങളെ എങ്ങനെ സഹായിക്കാം?",
    "bn-IN": "নমস্কার! আমি আপনাকে কীভাবে সাহায্য করতে পারি?",
    "od-IN": "ନମସ୍କାର! ମୁଁ ଆପଣଙ୍କୁ କିପରି ସାହାଯ୍ୟ କରିପାରିବି?",

    # ── Arabic ────────────────────────────────────────────────────────────────
    "ar-SA": "مرحباً! كيف يمكنني مساعدتك اليوم؟",
    "ar-AE": "مرحباً! كيف يمكنني مساعدتك اليوم؟",
}

#: Used when the language is unknown, absent, or not a real language code at all
#: (ElevenLabs reports free-text accents like "american"; the STT picker offers
#: "auto-detect"). English is the honest choice — better a sample in a language
#: we can name than a guess.
DEFAULT_SAMPLE_TEXT: str = TTS_SAMPLE_TEXT["en-IN"]


def sample_text_for(language: str | None) -> str:
    """The sentence to synthesize for ``language``.

    Falls back to English rather than raising: a preview that speaks the wrong
    language is a bad sample, but a preview that 500s is a broken button.
    """
    if not language:
        return DEFAULT_SAMPLE_TEXT
    code = str(language).strip()
    if code in TTS_SAMPLE_TEXT:
        return TTS_SAMPLE_TEXT[code]
    # `or-IN` is the same language as Sarvam TTS's `od-IN`; accept both spellings
    # for the same reason sarvam_catalog.normalize_language does.
    if code == "or-IN":
        return TTS_SAMPLE_TEXT["od-IN"]
    # Bare or regional English variants ("en", "en-AU") should still speak English
    # rather than falling through to a generic default by accident.
    if code.split("-")[0].lower() == "en":
        return TTS_SAMPLE_TEXT["en-IN"]
    return DEFAULT_SAMPLE_TEXT
