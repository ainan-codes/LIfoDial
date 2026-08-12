"""
backend/services/booking_trace.py

One correlation id per booking attempt, on BOTH channels, so an
arm→execute→confirm failure can be diagnosed from logs alone — without a human
having to record a call and paste the transcript back to us.

Why this exists
---------------
Four separate booking failures have now been diagnosed by reading a
hand-captured transcript, because the logs could not answer the only question
that matters: *how far did this attempt get?* The stages below are that answer.
Every line shares a ``trace_id``, so::

    grep 'trace_id=8f2c1a9b4d07' worker.log

reconstructs the whole attempt in order, across the voice FSM, the chat
[ACTION:] parser, and the shared DB writer in services/his.py.

Contract
--------
* Emit ``INTENT`` as soon as a channel decides the caller wants an appointment
  action, and carry the same ``trace_id`` through to ``REPLIED``.
* A stage that is never reached is the finding. An attempt that logs ``ARMED``
  and ``CONFIRMED`` but no ``EXECUTED`` means the write was never attempted;
  one that logs ``EXECUTED ok=false`` means it was attempted and refused.
* ``trace()`` must never raise. It is called on the audio hot path, and a
  logging bug must not be able to end a call.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

logger = logging.getLogger("booking_trace")

# ── Stages ────────────────────────────────────────────────────────────────────
# Ordered. Reading a trace means reading how far down this list it got.
INTENT = "intent_detected"        # channel decided this is a book/cancel/reschedule
ARMED = "slot_armed"              # details collected + availability verified; awaiting yes/no
CONFIRMED = "user_confirmed"      # caller/patient said yes
EXECUTING = "execute_started"     # about to call the shared DB writer
EXECUTED = "db_result"            # the write returned — ok=true/false + reason
REPLIED = "response_sent"         # the outcome was actually delivered to the user
DROPPED = "turn_dropped"          # a turn died before it could reach the user

VOICE = "voice"
CHAT = "chat"


def new_trace_id() -> str:
    """A short id that is greppable and cheap to say out loud on a support call."""
    return uuid.uuid4().hex[:12]


def _fmt(value: Any) -> str:
    text = str(value)
    # Keep one attempt on one line, and keep `key=value` splittable.
    text = text.replace("\n", " ").replace("\r", " ")
    return text.replace(" ", "_") if " " in text else text


def trace(
    trace_id: Optional[str],
    channel: str,
    stage: str,
    **fields: Any,
) -> None:
    """Emit one structured line for a stage of a booking attempt.

    Unknown-but-useful context goes in ``fields``; ``None`` values are dropped
    so a line only carries what was actually known at that stage.
    """
    try:
        extra = " ".join(
            f"{key}={_fmt(val)}" for key, val in fields.items() if val is not None
        )
        logger.info(
            "[BOOKING_TRACE] trace_id=%s channel=%s stage=%s%s",
            trace_id or "-",
            channel,
            stage,
            (" " + extra) if extra else "",
        )
    except Exception:  # pragma: no cover - logging must never break a call
        pass
