# -*- coding: utf-8 -*-
"""The LLM context must stop growing, WITHOUT dropping what the system injected.

Why this file exists
--------------------
Every turn re-sends the whole conversation, and nothing on the voice path
trimmed it. So the token cost of a turn grew with the call, and Groq's free tier
bills a tokens-per-day budget per model. Live on 2026-08-15:

    429 ... on tokens per day (TPD): Limit 100000, Used 99547, Requested 4808

The trimming itself is easy. The part worth testing is what must NOT be trimmed:
the [BOOKING_RESULT ...] and availability lines this pipeline injects as system
messages are the authoritative record of what really happened, and booking rule 7
requires a later "is it done?" to be answered from them rather than from the
model's memory of its own words. Trimming one would bring back the fabricated
confirmation bug, on long calls only, where it is hardest to see.

Run: python -m pytest backend/tests/test_context_trim.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from backend.agent.processors.context_trim import KEEP_TURNS, trim_messages


def _dialogue(n: int) -> list:
    out = []
    for i in range(n):
        out.append({"role": "user", "content": f"u{i}"})
        out.append({"role": "assistant", "content": f"a{i}"})
    return out


def test_a_short_call_is_left_completely_alone():
    msgs = [{"role": "system", "content": "prompt"}] + _dialogue(3)
    assert trim_messages(msgs) == msgs


def test_a_long_call_is_capped():
    msgs = [{"role": "system", "content": "prompt"}] + _dialogue(60)
    trimmed = trim_messages(msgs)
    dialogue = [m for m in trimmed if m["role"] != "system"]
    assert len(dialogue) == KEEP_TURNS
    assert len(trimmed) < len(msgs)


def test_the_most_recent_turns_are_the_ones_kept():
    msgs = _dialogue(40)
    trimmed = trim_messages(msgs, keep_turns=4)
    assert [m["content"] for m in trimmed] == ["u38", "a38", "u39", "a39"]


def test_every_system_message_survives_however_old():
    """The whole point. These are injected records, not conversation."""
    msgs = (
        [{"role": "system", "content": "the system prompt"}]
        + _dialogue(2)
        + [{"role": "system", "content": "[BOOKING_RESULT success=true] booked, id 42"}]
        + _dialogue(40)
        + [{"role": "system", "content": "[AVAILABILITY_REFRESH] Dr Salman: 3 PM"}]
        + _dialogue(5)
    )
    trimmed = trim_messages(msgs, keep_turns=6)

    systems = [m["content"] for m in trimmed if m["role"] == "system"]
    assert "the system prompt" in systems
    assert any("BOOKING_RESULT" in s for s in systems), (
        "the real booking outcome was trimmed away — a later 'is it done?' would "
        "be answered from the model's memory instead of from what was written"
    )
    assert any("AVAILABILITY_REFRESH" in s for s in systems)


def test_ordering_is_preserved():
    msgs = (
        [{"role": "system", "content": "s0"}]
        + _dialogue(30)
        + [{"role": "system", "content": "s1"}]
    )
    trimmed = trim_messages(msgs, keep_turns=4)
    assert [m["content"] for m in trimmed] == ["s0", "u28", "a28", "u29", "a29", "s1"]


def test_a_context_of_only_system_messages_is_untouched():
    msgs = [{"role": "system", "content": f"s{i}"} for i in range(50)]
    assert trim_messages(msgs, keep_turns=2) == msgs


def test_empty_is_safe():
    assert trim_messages([]) == []


def test_objects_without_a_dict_role_are_handled():
    """pipecat can carry message objects, not just dicts — this runs on a live
    call and must never be the thing that raises."""

    class _Msg:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    msgs = [_Msg("system", "s")] + [_Msg("user", f"u{i}") for i in range(30)]
    trimmed = trim_messages(msgs, keep_turns=5)
    assert len(trimmed) == 6
    assert trimmed[0].role == "system"
