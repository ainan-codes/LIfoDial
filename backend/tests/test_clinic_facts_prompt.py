"""
Tests that the clinic's OWN editable facts — its working hours and its doctors —
actually reach the live agent's system prompt.

Why this matters: clinic admins can edit exactly two things about their clinic
(timings via Settings → Clinic Profile, doctors via /doctors). Before this,
neither reliably reached the agent:

  * Working hours were read with getattr(Tenant, "working_hours", "9 AM – 7 PM,
    Mon–Sat") — but Tenant has no such column, so EVERY clinic's agent believed
    it opened 9–7 Mon–Sat regardless of what was configured.
  * Hours and the doctor roster were interpolated only into TEMPLATE prompts, so
    any clinic with a custom system_prompt (precedence #1) got neither.

Run: python -m pytest backend/tests/test_clinic_facts_prompt.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest

from backend.agent.pipeline import (
    _UNKNOWN_WORKING_HOURS,
    _build_system_prompt,
    _clinic_facts_block,
)

#: The hours the agent used to invent for a clinic that never configured any.
#: There is no longer a constant for it — this literal exists so the tests can
#: assert it never comes back.
FABRICATED_HOURS = "9 AM – 7 PM, Mon–Sat"

HOURS = "10:00 AM - 6:00 PM"

TENANT = {
    "clinic_name": "KMCT Clinic",
    "location": "Kozhikode",
    "working_hours": HOURS,
    "doctors": [
        {"name": "Dr. Anjali Menon", "specialization": "Cardiologist", "is_available": True},
        {"name": "Dr. Rahul Nair", "specialization": "Pediatrician", "is_available": False,
         "leave_reason": "back Monday"},
    ],
    "knowledge_base": [],
}


# ── The block itself ──────────────────────────────────────────────────────────

def test_block_states_the_configured_hours():
    block = _clinic_facts_block(TENANT)
    assert HOURS in block
    assert FABRICATED_HOURS not in block, "fell back to the hardcoded default"


def test_block_lists_available_doctors_and_omits_absent_ones():
    block = _clinic_facts_block(TENANT)
    assert "Dr. Anjali Menon" in block
    assert "Cardiologist" in block
    # The on-leave doctor must not appear in the bookable roster; the separate
    # _doctor_availability_block carries the explicit warning about them.
    bookable = block.split("Doctors available to book:")[1]
    assert "Dr. Rahul Nair" not in bookable


def test_block_forbids_booking_outside_hours():
    block = _clinic_facts_block(TENANT).lower()
    assert "inside the working hours" in block
    assert "closed" in block


def test_empty_roster_forbids_inventing_a_doctor():
    """A brand-new clinic has no doctors — the model must not make one up."""
    block = _clinic_facts_block({**TENANT, "doctors": []})
    assert "no doctors have been added" in block.lower()
    assert "never invent" in block.lower()


def test_all_doctors_on_leave_is_stated_explicitly():
    only_absent = [{**TENANT["doctors"][1]}]
    block = _clinic_facts_block({**TENANT, "doctors": only_absent})
    assert "every doctor on staff is on leave" in block.lower()


def test_missing_hours_are_never_invented():
    """A clinic that never set its hours has none — and the agent must say
    nothing about them rather than quote a plausible default as fact.

    This used to assert the opposite: an unconfigured clinic got
    "Working hours: 9 AM - 7 PM, Mon-Sat" stated as fact, immediately followed by
    the block's own instruction to refuse anything outside those hours and to
    "say the clinic is closed then". So the agent quoted invented opening times
    AND turned callers away on the strength of them. Reported 2026-08-15 as the
    agent giving out wrong time slots.

    What the caller can actually be told about is the REAL DOCTOR AVAILABILITY
    block, which is computed from the clinic's real DoctorAvailability rows by
    the same engine that gates the write.
    """
    for value in ("", "   ", None):
        block = _clinic_facts_block({**TENANT, "working_hours": value})
        assert FABRICATED_HOURS not in block, "invented opening hours"
        assert "Working hours:" not in block, (
            "stated an hours line for a clinic that has no hours on file"
        )
        low = block.lower()
        assert "never state" in low and "opening" in low, (
            "the model was not told it does not know this clinic's hours, so it "
            "will fill the silence with a plausible answer"
        )


def test_missing_hours_do_not_make_the_agent_declare_the_clinic_closed():
    """The closed-at-that-hour instruction is only honest when hours are known."""
    block = _clinic_facts_block({**TENANT, "working_hours": ""}).lower()
    assert "inside the working hours" not in block
    assert "say the clinic is closed then" not in block


def test_a_template_gets_an_honest_placeholder_when_hours_are_unknown():
    """Templates interpolate {working_hours} into a sentence and so cannot omit
    the line the facts block can. The substituted text must still be true."""
    assert "do not state" in _UNKNOWN_WORKING_HOURS.lower()
    assert "9 am" not in _UNKNOWN_WORKING_HOURS.lower()


# ── Reaching EVERY prompt path ────────────────────────────────────────────────

@pytest.mark.parametrize("agent_config,label", [
    ({"system_prompt": "You are Asha. Be very brief."}, "custom prompt"),
    ({"template": "clinic_receptionist", "tts_language": "en-IN"}, "template prompt"),
    ({"template": "__does_not_exist__"}, "hardcoded fallback"),
])
def test_hours_and_doctors_reach_every_prompt_path(agent_config, label):
    prompt = _build_system_prompt(agent_config, TENANT)
    assert HOURS in prompt, f"working hours missing from the {label}"
    assert "Dr. Anjali Menon" in prompt, f"doctor roster missing from the {label}"


def test_custom_prompt_is_still_honoured():
    """The block is appended, not substituted — the clinic's words must survive."""
    prompt = _build_system_prompt({"system_prompt": "You are Asha. Be very brief."}, TENANT)
    assert "You are Asha. Be very brief." in prompt
