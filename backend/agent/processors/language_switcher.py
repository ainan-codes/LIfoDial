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

Detection is Unicode-script based, which is what actually distinguishes the
languages this product serves (Devanagari vs Tamil vs Telugu vs …). It cannot
tell Hindi from Marathi (both Devanagari) or romanised Hindi from English —
those are handled by the STT model's own language detection, not here.

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
        self._pending: str = ""
        self._streak = 0
        self.switch_count = 0  # observability: surfaced in call logs

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
