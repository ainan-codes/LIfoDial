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

# Emitted only by the voice path (BookingProcessor._build_availability_note)
# when a caller-requested slot isn't actually open — lists the doctor's REAL
# slots for that day so the LLM offers real alternatives instead of
# inventing a nearby time. Per-request (depends on the day asked about),
# unlike the static per-call doctor roster in pipeline.py.
AVAILABILITY_NOTE = "[AVAILABILITY_NOTE]"

def voice_action_tag_block(today_str: str) -> str:
    """The VOICE path's ``[ACTION: …]`` tag instructions.

    The chat path has told the model to emit this tag since the beginning, and
    that is the only reason chat bookings reach the database. The voice path
    never did: it relied entirely on the keyword state machine in
    processors/booking_processor.py, which cannot fire on a normal call (the
    AGENT picks the doctor, and callers rarely say a bare "yes" in a turn of
    their own). The result, measured in production on 2026-08-12, was that no
    voice call had ever created an appointment row while every caller was told
    theirs was booked. See processors/voice_action.py.

    Two differences from the chat wording, both load-bearing:

      * The tag must come FIRST, before any words. On voice the reply is streamed
        into TTS as it is generated, so a tag at the END would arrive after the
        caller had already heard a confirmation that had not happened yet. Tag
        first means nothing is spoken until the outcome is known.
      * The reply that carries the tag says NOTHING to the caller. It is
        discarded: the system performs the action and then asks for a fresh reply
        with the real result. Anything written next to the tag is thrown away, so
        writing prose there only wastes the caller's time.

    ``today_str`` anchors relative days ("tomorrow") to a real date, exactly as
    the chat block does — the tag's Date field must never contain a day word.
    """
    return (
        "\n\n--- HOW TO ACTUALLY BOOK, RESCHEDULE OR CANCEL (STRICT) ---\n"
        "You cannot change the appointment book by talking about it. The ONLY way anything is saved "
        "is a machine tag, which the system reads and acts on. Speech alone changes nothing.\n"
        f"Today is {today_str}. Convert every relative day the caller says ('today', 'tomorrow', "
        "'day after tomorrow', a weekday name) into a real DD/MM/YYYY date. Never put a day word in "
        "the tag.\n"
        "When — and only when — the caller has asked you to book, move or cancel an appointment AND "
        "you have every field below, your ENTIRE reply must be exactly ONE of these tags and NOTHING "
        "else. No greeting, no confirmation, no words at all around it:\n"
        "  [ACTION: BOOK|Name|Phone|DD/MM/YYYY|Time|Doctor|Notes]\n"
        "  [ACTION: RESCHEDULE|Name|Phone|DD/MM/YYYY|Time|Doctor|Notes]\n"
        "  [ACTION: CANCEL|Name|Phone|DD/MM/YYYY|Time|Doctor|Notes]\n"
        "The system then carries the action out and immediately asks you for the caller-facing reply, "
        "telling you exactly what happened. That is when — and the only time — you confirm anything.\n"
        "Fields: Name and Phone are the caller's own (ask for the name if you do not have it; use "
        "'N/A' for the phone only if they will not give one — the number they are calling from is used "
        "instead). Doctor is the doctor's name. Notes is the symptom or reason, or 'N/A'. Use 'N/A' "
        "for a field that does not apply to the change.\n"
        "NEVER write 'N/A' as the Time of a BOOK or RESCHEDULE. If you do not have a real time yet, "
        "ask for it and emit no tag.\n"
        "NEVER emit a tag when the caller is only ASKING something — 'what times are free?', 'is the "
        "doctor in tomorrow?'. Answer those from the availability information above, with no tag.\n"
        "--- END ---"
    )


BOOKING_RULES_BLOCK = (
    "\n\n--- APPOINTMENT BOOKING RULES (STRICT) ---\n"
    "1. When the user wants a NEW appointment, ask which doctor and what day/time "
    "they want. Never invent or assume a doctor, a time, or availability yourself.\n"
    "2. Once they give a time, repeat the doctor + time back and ask them to confirm.\n"
    "3. NEVER say an appointment is booked, confirmed, rescheduled, cancelled, or "
    "scheduled unless a system message starting with [BOOKING_RESULT success=true] "
    "appears. Until then, say it is not yet confirmed.\n"
    "4. If a [BOOKING_RESULT success=false] message appears, do NOT claim success. "
    "Apologize, briefly explain using the reason given, and offer to try again or "
    "connect them to the clinic's staff.\n"
    "5. If the caller wants to CANCEL or RESCHEDULE an EXISTING appointment, ask for "
    "the name it was booked under. For a reschedule, also ask what new day/time they "
    "want. Repeat those details back and ask them to confirm before you proceed — "
    "never assume you already know them.\n"
    "6. If a system message starting with [AVAILABILITY_NOTE] appears, only offer the "
    "specific times it lists — never invent a nearby time.\n"
    "7. If you have ALREADY told them in this same conversation that an appointment was "
    "booked, rescheduled or cancelled (because the system confirmed it to you), and they "
    "ask whether it is done, just say yes and repeat the doctor and time. Do NOT check "
    "availability again and do NOT start the request over — the time they now hold will "
    "read as 'taken' precisely because it is theirs.\n"
    "--- END BOOKING RULES ---"
)
