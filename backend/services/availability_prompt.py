"""
backend/services/availability_prompt.py

THE clinic's real doctor roster and real open slots, rendered as a prompt block.

One implementation, deliberately: this used to live inside the chat/embed router
(agent_test.py::_real_availability_block) and the voice pipeline had its own,
weaker version (pipeline.py::_clinic_facts_block — roster names only, no slots,
no "today is"). Two implementations of "what does this clinic actually have" is
how the channels drifted: on 2026-08-11 the same question about the same clinic
got "we have Dr Salman available for cardiology" in chat and "no doctors are
available, no information has been given to me" by voice.

Both channels now call real_availability_block(). Chat calls it per turn. Voice
calls it once at call setup (off the audio hot path, so turn latency is
unaffected) and refreshes it mid-call when the caller brings up a day the block
does not already cover.

Everything here is best-effort: any failure returns a block that admits the
lookup failed and forbids claiming anything about the roster — never an empty
one, and never a claim that the clinic has no doctors. The hard guarantee that
an unavailable slot cannot be booked lives in his.execute_booking_action's
pre-write gate, not here.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
import time

logger = logging.getLogger(__name__)

# Relative-day words and weekday names live in services/dayref.py — ONE map, used
# by dates_mentioned() below, by his.parse_slot_datetime (which does the actual
# resolving) and by the voice FSM. A local copy used to live here covering only
# English + Devanagari, which is how a Malayalam caller's "നാളെ" resolved to today.

# How much real availability data goes into one prompt. Bounded because each
# extra doctor/day is real DB work inside the patient's reply latency, and a
# 40-line slot dump also buries the instruction that follows it.
#
# The slot cap is deliberately high enough to cover a normal full clinic day
# (24 x 30 minutes = 12 hours): a truncated list reads to the model as the
# WHOLE day, and it then tells the patient a genuinely free time is
# unavailable. Measured live 2026-08-11 with a cap of 8 — Dr Rajesh consults
# 09:00-17:00 and the agent refused a 3 PM reschedule that was open.
MAX_DOCTORS = 3
MAX_DAYS = 2
MAX_SLOTS_SHOWN = 24

# Short-lived cache of the computed digest, keyed by (tenant, doctors, dates).
# Every turn of a conversation asks about the same doctor and day, and a DB
# session costs a full Supabase handshake (~1.7s under NullPool), so without
# this the block would add that to each reply. 30s is deliberately short: a
# slot someone else took in the meantime can only ever make the agent OFFER a
# stale time, never book one — the pre-write gate in
# his.execute_booking_action is always computed fresh.
CACHE_TTL = 30.0
_cache: dict[tuple, tuple[float, dict]] = {}


# Emitted when the roster could not be READ (DB down, mapper failure, timeout).
# Distinct from the empty-roster block below on purpose. "This clinic has no
# doctors" is a true, useful statement for a brand-new clinic; saying it because
# a query failed is a lie told confidently, and it is what the voice channel did
# to a clinic with three doctors on it.
_LOOKUP_FAILED_BLOCK = (
    "\n\n--- DOCTOR LIST UNAVAILABLE ---\n"
    "This clinic's doctor list and appointment schedule could NOT be read right "
    "now (a temporary system problem on our side).\n"
    "You therefore do NOT know who works here, what specialities exist, or what "
    "times are free — so say nothing at all about them. Do NOT say the clinic has "
    "no doctors, do NOT say nobody is available, and do NOT name or invent a "
    "doctor, a speciality or a time.\n"
    "Instead, tell the caller plainly that you cannot look up the doctor list at "
    "the moment, take their name, phone number and what they need, and say the "
    "clinic's staff will call them back to confirm. Keep it to one or two "
    "sentences and stay warm about it.\n"
    "--- END DOCTOR LIST UNAVAILABLE ---\n"
)

_EMPTY_ROSTER_BLOCK = (
    "\n\n--- DOCTORS AT THIS CLINIC ---\n"
    "No doctors have been added to this clinic yet, so you CANNOT book, "
    "reschedule or confirm an appointment with a named doctor. Offer to take "
    "the patient's details for the clinic to call back, and never invent a "
    "doctor's name or an available time.\n"
    "--- END DOCTORS AT THIS CLINIC ---\n"
)


def lookup_failed_block() -> str:
    """The block to use when the clinic's data could not be read at all."""
    return _LOOKUP_FAILED_BLOCK


async def _cached_digest(tenant_id: str, doctor_ids: list[str], dates: list):
    from backend.services.availability import availability_digest

    key = (tenant_id, tuple(sorted(doctor_ids)), tuple(dates))
    hit = _cache.get(key)
    now = time.time()
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]

    digest = await availability_digest(tenant_id, doctor_ids, dates)
    _cache[key] = (now, digest)
    if len(_cache) > 256:
        for stale in [k for k, (ts, _) in _cache.items() if now - ts >= CACHE_TTL]:
            _cache.pop(stale, None)
    return digest


def dates_mentioned(text: str) -> list:
    """IST dates the patient's words refer to, in the order found.

    Thin wrapper over services/dayref.dates_in_text, which owns the relative-day
    vocabulary for every language this product speaks. This function used to own
    a smaller copy of that vocabulary (English + Devanagari only), while
    booking_processor.py owned another — and neither was what
    his.parse_slot_datetime actually parsed. One map now serves all three.
    """
    from backend.services.dayref import dates_in_text
    from backend.services.timeutil import ist_now

    return dates_in_text(text, ist_now().date())


async def caller_appointments_block(tenant_id: str, phone: str, name: str = "") -> str:
    """What THIS caller already has booked, as a prompt block — or "" if nothing.

    Why: on a live cancel call (2026-08-12) the agent asked the caller which
    doctor, which date and which time their appointment was, then asked them to
    spell their own name four times, and cancelled nothing in 280 seconds. Every
    fact it was asking for was already in the database, keyed by the number the
    caller was calling from.

    Handing the agent the real rows turns cancel/reschedule from an interrogation
    into a confirmation, and it removes the room to invent: it cannot describe an
    appointment that is not in this list, because the list is all there is.
    """
    try:
        from backend.services.his import caller_appointments

        rows = await caller_appointments(tenant_id, phone, name)
    except Exception as exc:
        logger.error("caller_appointments_block failed: %s", exc, exc_info=True)
        return ""

    if not rows:
        return ""

    lines = "\n".join(
        f"  {i}. {r['patient_name'] or 'name not recorded'} — Dr {r['doctor_name']}, "
        f"{r['day']} {r['date']} at {r['time']} (appointment id {r['appointment_id']})"
        for i, r in enumerate(rows, 1)
    )
    only_one = len(rows) == 1
    return (
        "\n\n--- THIS CALLER'S EXISTING APPOINTMENTS (REAL, from the database) ---\n"
        f"{lines}\n"
        "This is the COMPLETE list for this caller. Use it:\n"
        "* To cancel or move an appointment you ALREADY have every detail — the doctor, "
        "the date and the time are above. Do NOT ask the caller for them, and do NOT ask "
        "them to spell their name; you know it.\n"
        + ("* There is exactly one, so that is the one they mean. Read it back in ONE short "
           "sentence and ask them to confirm, then act.\n" if only_one else
           "* There is more than one, so ask WHICH of these they mean — by doctor and time, "
           "in one short question.\n")
        + "* Never describe, confirm or act on an appointment that is not in this list.\n"
        "--- END THIS CALLER'S EXISTING APPOINTMENTS ---"
    )


async def real_availability_block(tenant_id: str, recent_text: str = "") -> str:
    """The clinic's REAL doctor roster and REAL open slots, as a prompt block.

    ``recent_text`` is whatever the patient has said lately (this turn plus a
    little history). It only narrows WHICH doctors and days get their slots
    listed — the full roster is always listed, so "which doctors do you have?"
    is answerable no matter what words were used to ask it.

    Returns the lookup-failed block on any error, never "" and never a claim
    that the clinic has no doctors.
    """
    try:
        from backend.services.his import get_doctors
        from backend.services.timeutil import format_ist_clock, ist_now, to_ist

        doctors = await get_doctors(tenant_id)
        if not doctors:
            return _EMPTY_ROSTER_BLOCK

        # Which days? Exactly the ones being discussed; today + tomorrow only
        # when nothing specific was said, so a patient who asks "what's free?"
        # still gets real numbers. Listing extra days would only spend tokens
        # and dilute the day that is actually under discussion.
        today = ist_now().date()
        dates = dates_mentioned(recent_text)[:MAX_DAYS] or [
            today, today + _dt.timedelta(days=1),
        ]

        # Which doctors? The ones named in the conversation, else the roster.
        low = (recent_text or "").lower()

        def _mentioned(doc: dict) -> bool:
            words = [w for w in (doc.get("name") or "").lower().split() if len(w) > 2]
            words += [w for w in (doc.get("specialization") or "").lower().split() if len(w) > 2]
            return any(w in low for w in words)

        named = [d for d in doctors if _mentioned(d)]
        chosen = (named or doctors)[:MAX_DOCTORS]
        bookable = [d for d in chosen if d.get("is_available", True)]

        digest = await _cached_digest(tenant_id, [d["id"] for d in bookable], dates)

        lines = [f"Today is {today.strftime('%A, %d/%m/%Y')} (IST). All times are IST."]
        lines.append("Doctors at this clinic:")
        for d in doctors:
            label = f"  - {d['name']} ({d.get('specialization') or 'Specialist'})"
            if not d.get("is_available", True):
                reason = f" — {d['leave_reason']}" if d.get("leave_reason") else ""
                label += f" — ON LEAVE{reason}, cannot be booked"
            lines.append(label)

        lines.append("Real open appointment slots (already excludes booked times):")
        for d in bookable:
            for day in dates:
                slots = digest.get((str(d["id"]), day)) or []
                when = day.strftime("%A %d/%m/%Y")
                if not slots:
                    lines.append(
                        f"  - {d['name']}, {when}: NO open slots — do not offer any time that day."
                    )
                    continue
                shown = [format_ist_clock(to_ist(s)) for s in slots[:MAX_SLOTS_SHOWN]]
                extra = len(slots) - len(shown)
                lines.append(
                    f"  - {d['name']}, {when}: " + ", ".join(shown)
                    + (f", and {extra} further slots after that (this list was cut short — "
                       "do NOT tell the patient a later time is unavailable)" if extra > 0 else "")
                )

        return (
            "\n\n--- REAL DOCTOR AVAILABILITY (live from this clinic's schedule) ---\n"
            + "\n".join(lines)
            + "\n--- END REAL DOCTOR AVAILABILITY ---\n"
            "This section is the ONLY source of truth for who works here and what is "
            "free. When the patient asks which doctors you have, or what is available, "
            "answer from the list above IMMEDIATELY — never say you will check and get "
            "back to them, because you cannot send a later message. Only offer times "
            "listed above for that doctor and that day, and if they ask for a listed day "
            "at a time that is NOT listed, say plainly that it is taken and offer the "
            "listed times instead. For a day not covered above, just ask them to confirm "
            "the day — the system checks every time against the real schedule before "
            "anything is saved, so never guess and never invent a time.\n"
        )
    except Exception as e:
        logger.warning("Could not build the real availability block: %s", e, exc_info=True)
        return _LOOKUP_FAILED_BLOCK
