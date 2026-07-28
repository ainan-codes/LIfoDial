"""
Tests for clinic-admin redaction of agent payloads
(backend/routers/agents.py::redact_agent_for_clinic).

Why: _agent_to_dict() serialises EVERY AgentConfig column, and GET /agents/mine
plus GET /agents/{id} are both CurrentUser-level (not superadmin). That meant a
clinic-admin token received the platform's shared LiveKit and SIP credentials in
plain text, readable straight from the browser's network tab. Hiding the fields
in the React UI does not fix that — they must not leave the server.

Run: python -m pytest backend/tests/test_agent_redaction.py -v
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from backend.routers.agents import _CONFIDENTIAL_AGENT_FIELDS, redact_agent_for_clinic

SECRETS = {
    "livekit_url": "wss://lifodial.livekit.cloud",
    "livekit_api_key": "APIsecretkey123",
    "livekit_api_secret": "shhhhh-very-secret",
    "sip_account_sid": "AC0123456789",
    "sip_auth_token": "sip-auth-token-xyz",
    "sip_domain": "lifodial.sip.twilio.com",
}

CLINIC_OWNED = {
    # The clinic's own integrations and non-secret provider names — these MUST
    # survive, or we break features the clinic legitimately controls.
    "google_sheets_webhook_url": "https://script.google.com/macros/s/clinic-own/exec",
    "webhook_url": "https://clinic.example.com/hook",
    "sip_provider": "vobiz",
    "telephony_option": "byo",
    # Ordinary agent behaviour config.
    "agent_name": "Receptionist",
    "first_message": "Namaste!",
    "llm_model": "gemini-2.5-flash",
    "tts_voice": "meera",
}


def _payload() -> dict:
    return {"id": "agent-1", **SECRETS, **CLINIC_OWNED}


def test_all_platform_secrets_are_removed():
    out = redact_agent_for_clinic(_payload())
    leaked_keys = [k for k in _CONFIDENTIAL_AGENT_FIELDS if k in out]
    assert not leaked_keys, f"confidential keys survived redaction: {leaked_keys}"


def test_no_secret_VALUE_appears_anywhere_in_the_payload():
    """A key could leak by being copied into some other field — check values too."""
    out = redact_agent_for_clinic(_payload())
    blob = " ".join(str(v) for v in out.values())
    for name, secret in SECRETS.items():
        assert secret not in blob, f"value of {name} leaked into the payload"


def test_clinic_owned_fields_are_preserved():
    out = redact_agent_for_clinic(_payload())
    for key, expected in CLINIC_OWNED.items():
        assert out[key] == expected, f"{key} must remain visible to the clinic"


def test_configured_flags_replace_the_secrets():
    """The UI still needs to know telephony IS set up, without the credential."""
    out = redact_agent_for_clinic(_payload())
    assert out["livekit_api_key_configured"] is True
    assert out["sip_auth_token_configured"] is True

    blanks = redact_agent_for_clinic({**_payload(), "livekit_api_key": "", "sip_auth_token": None})
    assert blanks["livekit_api_key_configured"] is False
    assert blanks["sip_auth_token_configured"] is False


def test_does_not_mutate_the_input():
    """The caller's dict is the superadmin view — redaction must not corrupt it."""
    original = _payload()
    redact_agent_for_clinic(original)
    assert original["livekit_api_secret"] == SECRETS["livekit_api_secret"]


def test_missing_fields_do_not_invent_flags():
    """A payload without a given secret shouldn't gain a misleading _configured key."""
    out = redact_agent_for_clinic({"id": "a", "agent_name": "R"})
    assert "livekit_api_key_configured" not in out
    assert out == {"id": "a", "agent_name": "R"}
