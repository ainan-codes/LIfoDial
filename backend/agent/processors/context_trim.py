"""
backend/agent/processors/context_trim.py

Keeps the LLM context from growing without limit for the length of a call.

Why this exists
---------------
Every turn re-sends the whole conversation. Nothing on the voice path trimmed it,
so a call's token cost per turn grew with the call, and the last turn of a long
call was the most expensive one — exactly when the caller is closest to booking.

That is not an abstract cost on this product. Groq's free tier bills a
tokens-per-day budget PER MODEL, and on 2026-08-15 the primary returned:

    429 ... on tokens per day (TPD): Limit 100000, Used 99547, Requested 4808

4,808 tokens for one turn against a 100,000/day ceiling is roughly twenty turns
for the entire account, across every clinic, for the whole day. Callers were
getting rate-limit failures because the agent was re-reading its own
conversation on every sentence.

What is kept, and why the distinction matters
---------------------------------------------
System messages are ALL kept, however old. They are not conversation — they are
the authoritative record this pipeline injects into the context on purpose:

    [BOOKING_RESULT ...]      what actually happened in the database
    [AVAILABILITY_REFRESH]    the doctor's real open slots
    [AVAILABILITY_NOTE]       why a requested time was refused
    the tag-repair instruction

Dropping one is not "forgetting some chat". Booking rule 7 requires a later
"is it done?" to be answered from the injected outcome rather than from the
model's memory of its own words, so trimming a [BOOKING_RESULT] would reinstate
the fabricated-confirmation bug this codebase has fought repeatedly — and would
do it in the least visible way possible, on long calls only.

They are also cheap: a handful of short lines per call, against the many turns
of dialogue that actually drive the growth.

Only user/assistant turns are trimmed, oldest first, and the most recent
KEEP_TURNS of them always survive — comfortably more than any booking exchange
needs, since the booking state itself lives in BookingProcessor and the injected
system messages, not in the transcript.
"""

import logging

from pipecat.frames.frames import Frame, LLMContextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

logger = logging.getLogger(__name__)

#: How many of the most recent user/assistant messages to keep.
#:
#: 24 is ~12 exchanges. Measured against real call_records, a booking runs 6-10
#: exchanges end to end, so this keeps the whole of a normal booking plus room
#: for the caller to change their mind, while capping what a rambling call can
#: cost. Raising it costs tokens on EVERY subsequent turn, not just the long ones.
KEEP_TURNS = 24


def trim_messages(messages: list, keep_turns: int = KEEP_TURNS) -> list:
    """Return `messages` with old dialogue dropped and every system line kept.

    Pure and order-preserving, so it can be tested without a pipeline.
    """
    if not messages:
        return messages

    def _role(m) -> str:
        if isinstance(m, dict):
            return str(m.get("role") or "")
        return str(getattr(m, "role", "") or "")

    dialogue_idx = [i for i, m in enumerate(messages) if _role(m) != "system"]
    if len(dialogue_idx) <= keep_turns:
        return messages

    keep = set(dialogue_idx[-keep_turns:])
    return [m for i, m in enumerate(messages) if _role(m) == "system" or i in keep]


class ContextTrimProcessor(FrameProcessor):
    """Trims the shared LLMContext in place, just before the LLM reads it.

    Placed immediately before `llm` so it sees the context exactly as the model
    would — after BookingProcessor has injected this turn's system messages, and
    after the user aggregator has appended the caller's words.

    Never raises: an exception here would take out the caller's turn, and a turn
    that costs too many tokens is strictly better than a turn that does not
    happen.
    """

    def __init__(self, keep_turns: int = KEEP_TURNS) -> None:
        super().__init__()
        self._keep_turns = keep_turns
        self._warned = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMContextFrame):
            try:
                context = getattr(frame, "context", None)
                if context is not None:
                    messages = context.get_messages()
                    trimmed = trim_messages(messages, self._keep_turns)
                    if len(trimmed) < len(messages):
                        context.set_messages(trimmed)
                        if not self._warned:
                            logger.info(
                                "ContextTrimProcessor: trimming the LLM context to the "
                                "last %d dialogue turns (system messages all kept).",
                                self._keep_turns,
                            )
                            self._warned = True
            except Exception as exc:  # noqa: BLE001
                logger.error("ContextTrimProcessor: could not trim the context: %s", exc)

        await self.push_frame(frame, direction)
