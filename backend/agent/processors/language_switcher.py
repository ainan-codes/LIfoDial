"""Mid-conversation language switching for the LiveKit voice pipeline.

The pipeline picks one STT/TTS language when the call starts and, before this
processor existed, kept it for the whole call: a caller who greeted the agent in
English and then switched to Hindi got Hindi speech transcribed by an
English-pinned STT and answered by an English-pinned TTS voice.

``LanguageSwitchProcessor`` sits immediately after the STT service, watches the
final ``TranscriptionFrame``s flowing past, and when the caller's language
changes it retunes the live services in place using pipecat's own settings
frames:

  * ``TTSUpdateSettingsFrame`` pushed DOWNSTREAM  → the TTS service (which sits
    after this processor) starts synthesising in the new language.
  * ``STTUpdateSettingsFrame`` pushed UPSTREAM    → the STT service (which sits
    before it) re-tunes. For Deepgram this reconnects the streaming socket, so
    it is opt-in via ``switch_stt`` and skipped entirely for models that are
    already multilingual (nova-3 ``multi``, Sarvam ``saaras``).

Both frames carry ``service=`` so only the intended service reacts to them.

There are TWO independent detectors, because callers change language in two
different ways and only one of them was handled:

1. ``detect_language_from_text`` — the caller simply STARTS SPEAKING another
   language. Unicode-script based, which is what actually distinguishes the
   languages this product serves (Devanagari vs Tamil vs Telugu vs …). It cannot
   tell Hindi from Marathi (both Devanagari) or romanised Hindi from English —
   those are handled by the STT model's own language detection, not here.

2. ``detect_language_request`` — the caller ASKS, in their current language, to be
   answered in another one: *"Aap English mein baat kar sakte ho kya?"*

   Detector 1 is structurally blind to this, and not by accident: that sentence IS
   Hindi, so script detection correctly reports "still Hindi, no change". The
   request is about the language of the NEXT REPLY, which is a fact about meaning,
   not about characters. A real call transcript in this project shows a caller
   asking exactly that and the agent carrying on in Hindi as if nothing had been
   said.

   The prompt made it worse than an oversight. The rule shipped to the LLM was
   "Always reply in the SAME language the caller used in their most recent
   message" — which, read literally, *instructs* the model to ignore the request
   and answer a Hindi question in Hindi. The model was obeying. See
   ``_build_system_prompt`` in backend/agent/pipeline.py, which now carries an
   explicit carve-out for a request.

Nothing in here may break a call: every switch is wrapped so a bad settings
delta degrades to "keep the current language" instead of killing the pipeline.
"""

from __future__ import annotations

import logging
from typing import Callable

from pipecat.frames.frames import (
    Frame,
    STTUpdateSettingsFrame,
    TranscriptionFrame,
    TTSUpdateSettingsFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

log = logging.getLogger(__name__)

# Unicode block → BCP-47 code used throughout this project.
_SCRIPT_RANGES: list[tuple[int, int, str]] = [
    (0x0900, 0x097F, "hi-IN"),   # Devanagari — Hindi/Marathi (indistinguishable here)
    (0x0980, 0x09FF, "bn-IN"),   # Bengali
    (0x0A00, 0x0A7F, "pa-IN"),   # Gurmukhi
    (0x0A80, 0x0AFF, "gu-IN"),   # Gujarati
    (0x0B00, 0x0B7F, "or-IN"),   # Odia
    (0x0B80, 0x0BFF, "ta-IN"),   # Tamil
    (0x0C00, 0x0C7F, "te-IN"),   # Telugu
    (0x0C80, 0x0CFF, "kn-IN"),   # Kannada
    (0x0D00, 0x0D7F, "ml-IN"),   # Malayalam
]


def detect_language_from_text(
    text: str,
    min_chars: int = 8,
    dominance: float = 0.6,
    indic_share: float = 0.25,
) -> str:
    """Best-effort BCP-47 language code for ``text``, or "" when undecidable.

    ``min_chars`` guards against switching on "ok"/"haan" — short utterances are
    too weak a signal to retune two services on.

    Indic script wins on a *minority* share (``indic_share``), Latin needs a
    majority (``dominance``). That asymmetry is deliberate: real callers here
    speak Hinglish — "मुझे Dr. Sharma के साथ appointment चाहिए" is a Hindi
    sentence with English nouns in it, and by raw character count the Latin half
    can win. Nobody says the mirror image (an English sentence with Devanagari
    nouns), so any meaningful Indic content means the caller is on that language.
    """
    if not text:
        return ""

    counts: dict[str, int] = {}
    total = 0
    for ch in text:
        if not ch.isalpha():
            continue
        total += 1
        cp = ord(ch)
        for lo, hi, code in _SCRIPT_RANGES:
            if lo <= cp <= hi:
                counts[code] = counts.get(code, 0) + 1
                break
        else:
            if cp < 0x0250:  # Latin
                counts["en-IN"] = counts.get("en-IN", 0) + 1

    if total < min_chars or not counts:
        return ""

    indic = {code: n for code, n in counts.items() if code != "en-IN"}
    if indic:
        code, hits = max(indic.items(), key=lambda kv: kv[1])
        if hits / total >= indic_share:
            return code

    latin = counts.get("en-IN", 0)
    return "en-IN" if latin / total >= dominance else ""


# ── Explicit "please speak X" requests ────────────────────────────────────────
# Two vocabularies, and a match needs ONE FROM EACH: a language NAME and a
# request CUE. Requiring both is what separates "can you speak English?" from
# "sorry, my English is not good" — the second names a language but asks for
# nothing, and switching on it would be worse than not switching at all.
#
# Names are listed in Latin AND in every script a caller might see them
# transcribed in, because the transcript's script follows whatever language the
# caller is currently speaking: a Malayalam caller asking for English gets
# "ഇംഗ്ലീഷ്", a Hindi caller gets "इंग्लिश" or romanised "English", and Deepgram's
# Hindi model returns Devanagari while its multilingual model may return Latin.
# Anything missing here degrades to "no request detected", which is the old
# behaviour, never a wrong switch.
_LANGUAGE_NAMES: dict[str, tuple[str, ...]] = {
    "en-IN": (
        "english", "englis", "inglish", "angrezi", "angreji", "ingles",
        "इंग्लिश", "इंग्लिस", "अंग्रेजी", "अंग्रेज़ी", "इंग्रजी",
        "ഇംഗ്ലീഷ്", "ஆங்கிலம்", "இங்கிலீஷ்", "ఇంగ్లీష్", "ఆంగ్ల",
        "ಇಂಗ್ಲಿಷ್", "ಆಂಗ್ಲ", "ইংরেজি", "અંગ્રેજી", "ਅੰਗਰੇਜ਼ੀ", "ଇଂରାଜୀ",
    ),
    "hi-IN": (
        "hindi", "हिंदी", "हिन्दी", "ഹിന്ദി", "இந்தி", "హిందీ",
        "ಹಿಂದಿ", "হিন্দি", "હિન્દી", "ਹਿੰਦੀ", "ହିନ୍ଦୀ",
    ),
    "ml-IN": ("malayalam", "മലയാളം", "मलयालम", "മലയാളത്ത"),
    "ta-IN": ("tamil", "தமிழ்", "तमिल", "തമിഴ്", "ತಮಿಳು"),
    "te-IN": ("telugu", "తెలుగు", "तेलुगु", "തെലുങ്ക്", "ತೆಲುಗು"),
    "kn-IN": ("kannada", "ಕನ್ನಡ", "कन्नड", "കന്നഡ"),
    "mr-IN": ("marathi", "मराठी", "മറാത്തി"),
    "bn-IN": ("bengali", "bangla", "বাংলা", "बंगाली", "ബംഗാളി"),
    "gu-IN": ("gujarati", "ગુજરાતી", "गुजराती"),
    "pa-IN": ("punjabi", "panjabi", "ਪੰਜਾਬੀ", "पंजाबी"),
    "od-IN": ("odia", "oriya", "ଓଡ଼ିଆ", "ଓଡିଆ", "ओड़िया", "उड़िया"),
}

#: Words that make an utterance a REQUEST rather than a mention. Deliberately
#: broad and multilingual — a false negative just leaves the old behaviour, while
#: the name requirement above is what keeps false positives away.
_REQUEST_CUES: tuple[str, ...] = (
    # English / romanised
    "speak", "spoke", "talk", "say", "switch", "change", "reply", "answer",
    "continue", "prefer", "understand", "know", "can you", "could you",
    "please", "in ",
    # Hindi / Urdu, Devanagari and romanised
    "बात", "बोल", "बोलिए", "बोलो", "कर सकते", "कर सकती", "समझ", "जानते",
    "baat", "bol", "boliye", "bolo", "kar sakte", "kar sakti", "samajh",
    "mein", "me ", "karo", "kijiye", "kariye",
    # Malayalam
    "സംസാരി", "പറയ", "പറയാമോ", "അറിയാമോ", "മാറ്റ",
    # Tamil
    "பேச", "பேசு", "சொல்ல", "தெரியும", "மாற்ற",
    # Telugu
    "మాట్లాడ", "చెప్ప", "తెలుసా", "మార్చ",
    # Kannada
    "ಮಾತನಾಡ", "ಹೇಳ", "ಗೊತ್ತ", "ಬದಲಾಯಿ",
    # Bengali / Gujarati / Punjabi / Odia
    "বল", "কথা", "বোঝ", "બોલ", "વાત", "ਬੋਲ", "ਗੱਲ", "କହ", "କଥା",
)


def detect_language_request(text: str, supported: set[str] | None = None) -> str:
    """BCP-47 code the caller ASKED to be answered in, or "" if they did not.

    Independent of ``detect_language_from_text``: that answers "what language is
    this sentence?", this answers "what language did this sentence ask for?".
    Those are different questions with different answers, and conflating them is
    the bug — *"Aap English mein baat kar sakte ho kya?"* is a Hindi sentence
    requesting English.

    ``supported`` restricts the answer to languages this deployment can actually
    run. A caller asking for a language the platform cannot serve gets "" here, so
    the pipeline keeps working in the current language rather than retuning onto a
    provider that would answer HTTP 400 mid-call. The LLM still sees the request in
    the transcript and can decline it in words, which is the honest outcome.
    """
    if not text:
        return ""
    low = text.lower()

    if not any(cue in low for cue in _REQUEST_CUES):
        return ""

    # Longest alias first, so "angrezi" is not shadowed by a shorter substring of
    # another entry, and a two-word name beats a one-word one.
    best: tuple[int, str] = (0, "")
    for code, aliases in _LANGUAGE_NAMES.items():
        if supported is not None and code not in supported:
            continue
        for alias in aliases:
            if alias in low and len(alias) > best[0]:
                best = (len(alias), code)
    return best[1]


class LanguageSwitchProcessor(FrameProcessor):
    """Retunes STT/TTS language mid-call when the caller changes language.

    Args:
        tts: The live TTS service instance (sits downstream of this processor).
        stt: The live STT service instance (sits upstream of it).
        initial_language: BCP-47 code the pipeline was built with.
        stt_language_map: Maps a BCP-47 code to the STT provider's own code.
            Return "" to decline the switch for that language.
        switch_stt: Whether to retune STT at all. Leave False when the STT model
            is already multilingual — a reconnect costs ~200-400ms of deaf time
            and buys nothing.
        min_chars: Minimum alphabetic characters before a turn can trigger a switch.
        confirm_turns: Consecutive turns in the new language required to switch.
            1 = switch on the first clear signal (most responsive).
        on_switch: Optional callback ``(new_language) -> None`` for side effects
            (e.g. retargeting the never-silence fallback phrase). Must not raise.
        supported_languages: Codes this deployment can actually run. An explicit
            request for anything outside this set is ignored at the service level
            rather than retuning onto a provider that would 400 mid-call.
    """

    def __init__(
        self,
        *,
        tts,
        stt=None,
        initial_language: str,
        stt_language_map: Callable[[str], str] | None = None,
        switch_stt: bool = False,
        min_chars: int = 8,
        confirm_turns: int = 1,
        on_switch: Callable[[str], None] | None = None,
        supported_languages: set[str] | None = None,
    ) -> None:
        super().__init__()
        self._tts = tts
        self._stt = stt
        self._current = initial_language
        self._stt_language_map = stt_language_map or (lambda code: code)
        self._switch_stt = switch_stt
        self._min_chars = min_chars
        self._confirm_turns = max(1, confirm_turns)
        self._on_switch = on_switch
        self._supported = supported_languages
        self._pending: str = ""
        self._streak = 0
        self.switch_count = 0  # observability: surfaced in call logs
        self.requested_count = 0  # how many switches came from an explicit ask

    @property
    def current_language(self) -> str:
        return self._current

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        # REQUIRED first (pipecat 1.5): handles system frames + marks started.
        await super().process_frame(frame, direction)

        # Only final transcriptions are considered. Interim frames flip between
        # scripts as Deepgram revises its hypothesis, which would thrash the
        # services; a final frame is a committed utterance.
        if isinstance(frame, TranscriptionFrame) and (frame.text or "").strip():
            try:
                await self._maybe_switch(frame.text)
            except Exception:
                # A switch failing must never cost the caller their call.
                log.exception("Language switch failed — staying on %s", self._current)

        # Transparent: everything continues on its way (the user aggregator and
        # the transcript publisher downstream both depend on these frames).
        await self.push_frame(frame, direction)

    async def _maybe_switch(self, text: str) -> None:
        # An explicit ASK is checked first and bypasses the confirmation streak
        # entirely. A caller who says "can you speak English?" has given a
        # deliberate, unambiguous instruction — making them repeat it to satisfy a
        # heuristic streak counter is the same "the agent ignored me" experience the
        # request handling exists to fix.
        #
        # It also has to come first because the two detectors DISAGREE on exactly
        # this input by design: the request sentence is in the caller's CURRENT
        # language, so script detection would return "no change" and return early.
        requested = detect_language_request(text, self._supported)
        if requested and requested != self._current:
            log.info(
                "Caller explicitly requested %s (current %s) — switching now: %r",
                requested, self._current, text[:120],
            )
            self.requested_count += 1
            await self._apply(requested)
            self._pending, self._streak = "", 0
            return

        detected = detect_language_from_text(text, min_chars=self._min_chars)
        if not detected or detected == self._current:
            self._pending, self._streak = "", 0
            return

        if detected == self._pending:
            self._streak += 1
        else:
            self._pending, self._streak = detected, 1

        if self._streak < self._confirm_turns:
            log.debug(
                "Language change to %s pending (%d/%d turns)",
                detected, self._streak, self._confirm_turns,
            )
            return

        await self._apply(detected)
        self._pending, self._streak = "", 0

    async def _apply(self, language: str) -> None:
        previous, self._current = self._current, language
        self.switch_count += 1
        log.info("Caller switched language %s → %s — retuning services", previous, language)

        if self._on_switch:
            try:
                self._on_switch(language)
            except Exception:
                log.exception("on_switch callback failed for %s", language)

        # TTS first: it is what the caller actually hears, and it sits downstream.
        tts_settings_cls = getattr(type(self._tts), "Settings", None)
        if tts_settings_cls is not None:
            await self.push_frame(
                TTSUpdateSettingsFrame(
                    delta=tts_settings_cls(language=language), service=self._tts
                ),
                FrameDirection.DOWNSTREAM,
            )
        else:
            log.warning(
                "TTS service %s exposes no Settings class — cannot switch its language",
                type(self._tts).__name__,
            )

        if not (self._switch_stt and self._stt is not None):
            return

        stt_code = self._stt_language_map(language)
        stt_settings_cls = getattr(type(self._stt), "Settings", None)
        if not stt_code or stt_settings_cls is None:
            log.info(
                "Not retuning STT for %s (provider code=%r) — leaving it as-is",
                language, stt_code,
            )
            return

        # UPSTREAM: the STT service sits before this processor in the pipeline.
        await self.push_frame(
            STTUpdateSettingsFrame(
                delta=stt_settings_cls(language=stt_code), service=self._stt
            ),
            FrameDirection.UPSTREAM,
        )
