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
    _DEFAULT_WORKING_HOURS,
    _build_system_prompt,
    _clinic_facts_block,
)

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
    assert _DEFAULT_WORKING_HOURS not in block, "fell back to the hardcoded default"


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


def test_missing_hours_falls_back_to_the_documented_default():
    for value in ("", "   ", None):
        block = _clinic_facts_block({**TENANT, "working_hours": value})
        assert _DEFAULT_WORKING_HOURS in block


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
