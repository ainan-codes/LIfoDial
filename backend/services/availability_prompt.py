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

# Relative-day words the patient may use, mapped to an offset from today (IST).
# Weekday names are handled separately (next occurrence of that weekday).
_RELATIVE_DAYS: dict[str, int] = {
    "today": 0, "tonight": 0, "aaj": 0, "आज": 0,
    "tomorrow": 1, "tmrw": 1, "kal": 1, "कल": 1,
    "day after tomorrow": 2, "parso": 2, "परसों": 2,
}
_WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday",
                  "friday", "saturday", "sunday")

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

    Recognises the same relative-day words and DD/MM/YYYY-style dates the
    booking tag itself uses, so the availability we look up is for the day
    actually being discussed rather than always today. Devanagari forms of the
    relative-day words are included because voice transcripts of a Hindi call
    arrive in Devanagari, not romanised — "कल" never matched "kal".
    """
    from backend.services.his import _DATE_FORMATS
    from backend.services.timeutil import ist_now

    low = (text or "").lower()
    today = ist_now().date()
    found: list = []

    def _add(d):
        if d not in found:
            found.append(d)

    # Longest phrases first so "day after tomorrow" is not read as "tomorrow".
    for word in sorted(_RELATIVE_DAYS, key=len, reverse=True):
        if word.strip() and word.strip() in low:
            _add(today + _dt.timedelta(days=_RELATIVE_DAYS[word]))

    for idx, name in enumerate(_WEEKDAY_NAMES):
        if name in low:
            ahead = (idx - today.weekday()) % 7 or 7
            _add(today + _dt.timedelta(days=ahead))

    for token in re.findall(r'\b\d{1,4}[/-]\d{1,2}[/-]\d{2,4}\b', text or ""):
        for fmt in _DATE_FORMATS:
            try:
                _add(_dt.datetime.strptime(token, fmt).date())
                break
            except ValueError:
                continue

    return found


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
