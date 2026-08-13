"""
The voice agent must never SPEAK a numbered list.

Measured on a live Hindi call 2026-08-13: the agent's intake turn was
"1. आपका पूरा नाम क्या है? 2. ... 3. ... 4. ...", read aloud by TTS. Four
questions in one breath, spoken as an enumerated list.

The model was following the shape of its own instructions. Every block appended
to the voice system prompt is an ordered numbered list — booking_rules.py's
rules 1-8, and the per-template "BOOKING FLOW" steps in prompt_templates.py —
and nothing told the model that its output is spoken rather than rendered.

Note the templates already said "Ask only ONE question at a time". The
instruction was there and lost to the surrounding format, which is why the fix
names the channel explicitly instead of just repeating the rule.

The rule has to survive all three prompt-precedence paths, because a clinic that
writes its own system_prompt must not thereby get a list-reading agent.

Run: python -m pytest backend/tests/test_voice_speaks_prose_not_lists.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest

from backend.agent.pipeline import _build_system_prompt

TENANT = {
    "clinic_name": "KMCT Clinic",
    "location": "Kozhikode",
    "working_hours": "10:00 AM - 6:00 PM",
    "doctors": [
        {"name": "Dr. Salman", "specialization": "Physician", "is_available": True},
    ],
    "knowledge_base": [],
}

PROMPT_PATHS = [
    ({"system_prompt": "You are Asha. Be very brief."}, "custom prompt"),
    ({"template": "clinic_receptionist", "language": "hi-IN"}, "template prompt"),
    ({"template": "__does_not_exist__"}, "hardcoded fallback"),
]


@pytest.mark.parametrize("agent_config,label", PROMPT_PATHS)
def test_every_voice_prompt_forbids_lists(agent_config, label):
    prompt = _build_system_prompt(agent_config, TENANT).lower()
    assert "never use a numbered or bulleted list" in prompt, (
        f"the {label} does not forbid spoken lists"
    )


@pytest.mark.parametrize("agent_config,label", PROMPT_PATHS)
def test_every_voice_prompt_says_the_output_is_spoken(agent_config, label):
    """The load-bearing half. "Don't use lists" alone had already been tried, in
    effect, via "ask only ONE question at a time" — it lost to the format of the
    surrounding rules. Naming the channel is what gives the model a reason."""
    prompt = _build_system_prompt(agent_config, TENANT).lower()
    assert "read to the caller by a text-to-speech voice" in prompt, (
        f"the {label} never tells the model it is being spoken aloud"
    )


@pytest.mark.parametrize("agent_config,label", PROMPT_PATHS)
def test_every_voice_prompt_asks_one_thing_at_a_time(agent_config, label):
    prompt = _build_system_prompt(agent_config, TENANT).lower()
    assert "ask for one thing at a time" in prompt, (
        f"the {label} permits asking several questions in one turn"
    )


@pytest.mark.parametrize("agent_config,label", PROMPT_PATHS)
def test_the_style_rule_never_overrides_the_action_tag(agent_config, label):
    """A tag-only reply is not prose and is not two sentences. If the style rule
    ever reads as a licence to wrap the tag in conversational words, the tag
    stops being parseable and nothing reaches the database — which is the
    failure this project spent 2026-08-12 fixing. Both must coexist."""
    prompt = _build_system_prompt(agent_config, TENANT)
    assert "[ACTION: BOOK|Name|Phone|Date|Time|Doctor|Notes]" in prompt, (
        f"the {label} lost the action-tag instructions"
    )
    lowered = prompt.lower()
    assert "never overrides the booking rules above" in lowered, (
        f"the {label}'s style rule does not defer to the booking rules"
    )


def test_the_rule_is_voice_only_and_not_imposed_on_chat():
    """The chat channel and the embed widget render Markdown, where a numbered
    list is genuinely useful. The rule lives in pipeline.py (voice) rather than
    booking_rules.py (shared with backend/routers/agent_test.py), so importing
    the shared module must not drag it along."""
    from backend.agent import booking_rules

    shared = booking_rules.BOOKING_RULES_BLOCK + booking_rules.voice_action_tag_block(
        "Thursday, 13/08/2026"
    )
    assert "text-to-speech" not in shared.lower(), (
        "the voice-only style rule leaked into the block the chat path shares"
    )


def test_the_custom_prompt_still_survives():
    prompt = _build_system_prompt({"system_prompt": "You are Asha. Be very brief."}, TENANT)
    assert "You are Asha. Be very brief." in prompt
