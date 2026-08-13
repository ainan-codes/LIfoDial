"""
The keyword FSM in BookingProcessor must never write a placeholder phone number.

Measured live 2026-08-13 (call 3163364c-a2d3-47fa-b042-b3ec97af1eaf, Hindi,
Indiana Hospital Mangalore). The caller said:

    "एक काम कर लो, कल मुझे दोपहर के दो बजे आना है डॉक्टर सलमान के पास।
     मेरा नाम ऐनान है और मेरा नंबर है 9148768120।"

The number was transcribed correctly, extracted correctly, and stored correctly
in call_meta["stated_phone"] by _note_what_the_caller_said. The appointment row
was still written with patient_phone='unknown'. Two more rows on 2026-08-12 are
identical. In the dashboard they render as the phone "unk", which is how this
was reported.

Nothing was wrong with the capture. booking_state["patient_phone"] is seeded
once in __init__ from call_meta["caller_phone"] — the literal "unknown" on a
browser call, which has no caller ID — and the commit paths read that seed
instead of what the caller had just said.

Why it matters beyond tidiness: the number is half the key
his.find_active_appointment matches on, so a row stored this way can never be
cancelled or rescheduled by the person who booked it, and the clinic has nothing
to ring back on.

Run: python -m pytest backend/tests/test_fsm_stores_the_real_phone.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest

from backend.agent.processors.booking_processor import BookingProcessor

TENANT_ID = "11111111-1111-1111-1111-111111111111"
PHONE = "9148768120"
SPOKEN = (
    "एक काम कर लो, कल मुझे दोपहर के दो बजे आना है डॉक्टर सलमान के पास। "
    f"मेरा नाम ऐनान है और मेरा नंबर है {PHONE}।"
)


def _proc(**call_meta) -> BookingProcessor:
    return BookingProcessor(
        tenant={"id": TENANT_ID, "doctors": []},
        agent_config={},
        call_meta=call_meta,
    )


def test_a_browser_call_starts_with_no_usable_number():
    """The precondition. A web call has no caller ID, so the seed is the literal
    string "unknown" — which is exactly what was reaching the database."""
    proc = _proc(caller_phone="unknown")
    assert proc.booking_state["patient_phone"] == "unknown"
    assert proc._resolve_patient_phone() == ""


def test_the_number_the_caller_spoke_wins_over_the_placeholder_seed():
    proc = _proc(caller_phone="unknown")
    proc._note_what_the_caller_said(SPOKEN)
    assert proc._call_meta["stated_phone"] == PHONE, "capture regressed"
    assert proc._resolve_patient_phone() == PHONE, (
        "the commit path would still have stored the placeholder"
    )


def test_the_number_the_caller_spoke_wins_over_real_caller_id():
    """A caller reading out a number is choosing the one they want to be reached
    on — it may not be the handset they are calling from. Same precedence as
    VoiceActionProcessor._execute, so the two voice write paths cannot disagree
    about the same call."""
    proc = _proc(caller_phone="9999900000")
    proc._note_what_the_caller_said(SPOKEN)
    assert proc._resolve_patient_phone() == PHONE


def test_caller_id_is_used_when_the_caller_said_no_number():
    """A real phone call. Caller ID is better evidence than nothing, and is what
    the FSM has always stored for PSTN callers — that must not regress."""
    proc = _proc(caller_phone="9845012345")
    proc._note_what_the_caller_said("कल दोपहर दो बजे आना है")
    assert proc._resolve_patient_phone() == "9845012345"


@pytest.mark.parametrize("seed", ["unknown", "N/A", "none", "-", "", None])
def test_every_placeholder_shape_resolves_to_nothing(seed):
    """"unknown" is the one we saw, but the resolver defers to
    action_tag.is_placeholder so the whole family is covered — a new placeholder
    spelling must not quietly become a stored phone number."""
    proc = _proc(caller_phone=seed)
    assert proc._resolve_patient_phone() == ""


@pytest.mark.asyncio
async def test_no_number_asks_the_caller_instead_of_writing_a_row():
    """The behaviour that replaces the bad write. The caller must be ASKED, not
    told they are booked — and the arming must survive so their next "yes"
    retries this booking rather than restarting it.

    This mirrors the [ACTION:] path, which already refuses on exactly this
    condition via action_tag.missing_identity_fields.
    """
    class _Ctx:
        def __init__(self):
            self.messages = []

        def add_message(self, m):
            self.messages.append(m)

    class _Frame:
        def __init__(self, ctx):
            self.context = ctx

    proc = _proc(caller_phone="unknown")
    proc.booking_state.update({
        "pending_doctor_id": "22222222-2222-2222-2222-222222222222",
        "pending_doctor_name": "Dr Salman",
        "pending_slot": "Tomorrow 2 PM",
        "pending_slot_day_str": "Tomorrow",
        "pending_slot_time_str": "2 PM",
        "patient_name": "ऐनान",
        "awaiting_confirm": True,
    })
    ctx = _Ctx()
    await proc._do_commit_and_inject_result(_Frame(ctx))

    assert len(ctx.messages) == 1, ctx.messages
    said = ctx.messages[0]["content"]
    assert "[BOOKING_RESULT success=false]" in said, (
        "the model was not told the booking failed, so it will claim success"
    )
    assert "phone number" in said.lower()
    assert proc.booking_state["confirmed"] is False
    assert proc.booking_state["awaiting_confirm"] is True, (
        "the caller would have to give every detail again after supplying a number"
    )
