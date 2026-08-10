"""
backend/agent/processors/tag_scrub.py

Last line of defence against a machine tag being SPOKEN to a caller.

The voice pipeline hands the LLM system messages that are addressed to the
model, not the caller:

    [BOOKING_RESULT success=true] The appointment IS saved …
    [AVAILABILITY_NOTE] Dr Rajesh is only actually open at …

and the chat/embed path additionally instructs the model to *emit* a tag of
its own, ``[ACTION: BOOK|…]``. Models echo bracketed tokens they have just
been shown — the chat path leaked exactly that to a patient in production on
2026-08-11 — and on the voice side there is no equivalent of the chat path's
post-processing: whatever the LLM produces goes straight into TTS and is
spoken aloud.

This processor sits between the LLM and TTS and removes any machine tag from
the text on its way to be spoken. Because the LLM streams a reply in small
chunks, a tag can straddle two frames ("…booked. [BOOK" + "ING_RESULT …]"), so
text after an unclosed ``[`` that could still become a machine tag is HELD
until the bracket closes or the response ends. Ordinary bracketed prose is
never held: only a bracket whose contents so far still match a machine-tag
name waits.

Everything else — every non-text frame, and every text frame with no bracket
in it — is passed through untouched, in order.
"""

import logging
import re
from typing import Optional

from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    TextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)

# The machine tag names. A bracket is only ever held/dropped when its contents
# match one of these, so a caller who genuinely hears "[…]" prose is unaffected.
_TAG_NAMES = ("action", "booking_result", "availability_note")

# A COMPLETE machine tag, anywhere in the text.
_COMPLETE_TAG_RE = re.compile(
    r'\[\s*(?:' + "|".join(_TAG_NAMES) + r')\b[^\]]*\]',
    re.IGNORECASE,
)


def _could_start_a_tag(fragment: str) -> bool:
    """True if `fragment` (which starts with '[') could still grow into a
    machine tag — i.e. what follows the bracket so far is a prefix of one of
    the tag names. "[ACT" and "[ boo" qualify; "[see note" does not."""
    body = fragment[1:].lstrip().lower()
    if not body:
        return True
    return any(name.startswith(body) or body.startswith(name) for name in _TAG_NAMES)


def scrub_spoken_text(text: str) -> str:
    """Remove every complete machine tag and tidy the leftover spacing.

    Exposed (and tested) separately from the frame plumbing because it is the
    part that has to be right; the processor around it only decides *when* it
    is safe to run.
    """
    cleaned = _COMPLETE_TAG_RE.sub(" ", text or "")
    return re.sub(r'[ \t]{2,}', ' ', cleaned)


class TagScrubProcessor(FrameProcessor):
    """Transparent processor that strips machine tags out of spoken text.

    Placed between `llm` and `tts` in the pipeline. It never blocks, never
    reorders, and never holds anything past the end of the LLM response.
    """

    def __init__(self) -> None:
        super().__init__()
        # Text withheld because it sits after an unclosed, possibly-a-tag '['.
        self._pending: str = ""
        # The frame object the pending text belongs to, reused when it is
        # finally emitted so its flags (skip_tts, spacing, …) are preserved.
        self._pending_frame: Optional[TextFrame] = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        # REQUIRED first — lets the base class handle StartFrame/CancelFrame and
        # mark itself started (see BookingProcessor for the same note).
        await super().process_frame(frame, direction)

        # A standalone utterance (e.g. the emergency message BookingProcessor
        # pushes) is complete by construction, so it is scrubbed and forwarded
        # immediately — never held.
        if isinstance(frame, TTSSpeakFrame):
            frame.text = scrub_spoken_text(frame.text).strip()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TextFrame) and direction == FrameDirection.DOWNSTREAM:
            await self._handle_text_frame(frame, direction)
            return

        # End of the LLM's reply: anything still held will never be completed,
        # so decide on it BEFORE the end frame goes downstream.
        if isinstance(frame, LLMFullResponseEndFrame):
            await self._flush(direction)

        await self.push_frame(frame, direction)

    async def _handle_text_frame(self, frame: TextFrame, direction: FrameDirection) -> None:
        combined = self._pending + (frame.text or "")
        self._pending = ""
        self._pending_frame = None

        emit, pending = self._split(scrub_spoken_text(combined))

        if pending:
            self._pending = pending
            self._pending_frame = frame

        if not emit.strip():
            # Nothing speakable in this chunk (it was all tag, or all held).
            return

        frame.text = emit
        await self.push_frame(frame, direction)

    @staticmethod
    def _split(text: str) -> tuple[str, str]:
        """Split `text` (already free of complete tags) into (safe, held).

        `held` is the tail starting at the last unclosed '[' that could still
        become a machine tag; "" when there is no such bracket.
        """
        idx = text.rfind("[")
        if idx == -1 or "]" in text[idx:]:
            return text, ""
        tail = text[idx:]
        if not _could_start_a_tag(tail):
            return text, ""
        return text[:idx], tail

    async def _flush(self, direction: FrameDirection) -> None:
        """Resolve held text at the end of a response.

        An unterminated machine tag is dropped (there is no valid caller-facing
        sentence that starts "[ACTION" and never closes); anything else is
        spoken, because withholding real words would be worse than a stray
        bracket.
        """
        pending, frame = self._pending, self._pending_frame
        self._pending = ""
        self._pending_frame = None
        if not pending or frame is None:
            return

        body = pending[1:].lstrip().lower()
        if any(body.startswith(name) for name in _TAG_NAMES):
            logger.warning(
                "Dropped an unterminated machine tag before TTS: %r", pending[:80],
            )
            return

        frame.text = pending
        await self.push_frame(frame, direction)
