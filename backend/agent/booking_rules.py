"""
backend/agent/booking_rules.py

Single source of truth for the appointment-booking **honesty contract** shared
by BOTH agent code paths:

  * the real-time voice pipeline — backend/agent/pipeline.py (via
    backend/agent/processors/booking_processor.py), and
  * the text/chat path — backend/routers/agent_test.py::generate_llm_response,
    which also backs the public embed widget (backend/routers/embed.py).

The contract (audit "FIX 4"): the agent must NEVER tell a user an appointment is
booked / confirmed / rescheduled / cancelled unless a system message beginning
with ``[BOOKING_RESULT success=true]`` has appeared — i.e. the database write
actually succeeded. Both paths AWAIT the real DB write and inject a
``[BOOKING_RESULT ...]`` message into the LLM context BEFORE the model produces
its confirmation.

This lived only inside pipeline.py before, so the chat path never received it
and would fabricate confirmations. Keeping the wording here — imported by both —
is what stops the two implementations from diverging again.
"""

# Tokens both paths emit / match on. Keep these prefixes stable: rule 3 below
# and BookingProcessor._commit_and_inject_result both depend on them.
BOOKING_RESULT_TRUE = "[BOOKING_RESULT success=true]"
BOOKING_RESULT_FALSE = "[BOOKING_RESULT success=false]"

BOOKING_RULES_BLOCK = (
    "\n\n--- APPOINTMENT BOOKING RULES (STRICT) ---\n"
    "1. When the user wants an appointment, ask which doctor and what day/time "
    "they want. Never invent or assume a doctor, a time, or availability yourself.\n"
    "2. Once they give a time, repeat the doctor + time back and ask them to confirm.\n"
    "3. NEVER say an appointment is booked, confirmed, rescheduled, cancelled, or "
    "scheduled unless a system message starting with [BOOKING_RESULT success=true] "
    "appears. Until then, say it is not yet confirmed.\n"
    "4. If a [BOOKING_RESULT success=false] message appears, do NOT claim success. "
    "Apologize, briefly explain using the reason given, and offer to try again or "
    "connect them to the clinic's staff.\n"
    "--- END BOOKING RULES ---"
)
