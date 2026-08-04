# backend/models/agent_config.py

import uuid
from sqlalchemy import (
    Column, String, Float, Integer,
    Boolean, JSON, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from datetime import datetime, timezone
from backend.db import Base


class AgentConfig(Base):
    __tablename__ = "agent_configs"

    # ── Primary Key ──────────────────────────────
    id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4())
    )
    # NOTE: intentionally NOT unique — a tenant/clinic can have multiple agents.
    tenant_id = Column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    tenant = relationship("Tenant", back_populates="agent_configs")

    # ── Identity ─────────────────────────────────
    agent_name = Column(String(100), default="Receptionist")
    template = Column(String(50), default="clinic_receptionist")
    first_message = Column(Text, nullable=False, default="")
    first_message_mode = Column(String(50), default="assistant-speaks-first")
    system_prompt = Column(Text, nullable=False, default="")

    # ── Language — THE single source of truth ─────
    # One agent, one language. BCP-47, never "auto".
    #
    # This column exists because there used to be two independently editable
    # language columns (stt_language, tts_language) plus a third de-facto source
    # (the voice catalog's static per-voice language). On agent
    # f367e0e2-4e31-41fd-8a4a-df0f6ebbd8d7 that produced four disagreeing values
    # at once, and pinned STT to Tamil while TTS spoke Malayalam — so a Malayalam
    # caller could not be understood. See backend/services/agent_defaults.py for
    # the full evidence and the migration's conflict-resolution rule.
    #
    # Everything language-related derives from this: the STT language code, the
    # TTS language, every UI label, and an explicit instruction in the LLM system
    # prompt. Whether the transcriber PINS to it or lets the provider detect is
    # the separate, pre-existing auto_detect_language boolean below.
    language = Column(String(20), default="en-IN", nullable=False, server_default="en-IN")

    # ── STT (Speech to Text) ─────────────────────
    # provider/model are LOCKED (deepgram/nova-3) — not configurable per agent.
    # agent_defaults.apply_locked_defaults is the only writer.
    stt_provider = Column(String(30), default="deepgram")
    stt_model = Column(String(50), default="nova-3")
    # DERIVED MIRROR of `language` — do not write directly, do not expose in UI.
    # Holds "auto" when auto_detect_language is set, otherwise `language`.
    # Written only by agent_defaults.apply_locked_defaults.
    #
    # Kept as a column rather than dropped because a deployed agent worker may
    # still be running a revision that reads it; dropping it would break live
    # calls the moment this migration lands. Safe to drop once every worker is
    # confirmed on a revision that reads `language`.
    #
    # varchar(20) to match the column the migration actually created
    # (2026_04_07_1512-bbf25bb3c633). Deliberately NOT widened: every real code is
    # <= 7 characters (see backend/services/stt_catalog.py), so a narrow column is
    # the backstop that catches a label-as-value bug instead of silently
    # persisting one — which is how a 37-character description
    # ("Multilingual (English/Hindi/Regional)") once reached this column.
    stt_language = Column(String(20), default="en-IN")
    transcriber_keywords = Column(Text, nullable=True)
    fallback_transcribers = Column(Text, nullable=True)

    # ── TTS (Text to Speech) ─────────────────────
    # provider/model are LOCKED (sarvam/bulbul:v3). tts_voice is NOT locked —
    # the voice/speaker choice and the Voice Library are deliberately preserved.
    tts_provider = Column(String(30), default="sarvam")
    tts_model = Column(String(50), default="bulbul:v3")
    tts_voice = Column(String(50), default="priya")
    # DERIVED MIRROR of `language` — see stt_language above. Always equals
    # `language`. Widened 10 -> 20 to match it, so the two mirrors can never
    # disagree by truncation.
    tts_language = Column(String(20), default="en-IN")
    tts_pitch = Column(Float, default=0.0)
    tts_pace = Column(Float, default=1.0)
    tts_loudness = Column(Float, default=1.0)
    tts_stability = Column(Float, default=0.5)
    tts_clarity = Column(Float, default=0.75)
    tts_speed = Column(Float, default=1.0)
    tts_style = Column(Float, default=0.0)
    tts_use_speaker_boost = Column(Boolean, default=False)
    tts_optimize_streaming_latency = Column(Integer, default=3)
    tts_input_preprocessing = Column(Boolean, default=True)
    tts_filler_injection = Column(Boolean, default=False)
    add_voice_manually = Column(String(100), nullable=True)
    fallback_voices = Column(Text, nullable=True)

    # ── LLM ──────────────────────────────────────
    # LOCKED (groq/llama-3.3-70b-versatile). The old defaults let provider and
    # model drift apart: one live agent held llm_provider='groq' with
    # llm_model='gemini-2.5-flash-8b', which Groq answers 404 for.
    llm_provider = Column(String(30), default="groq")
    llm_model = Column(String(100), default="llama-3.3-70b-versatile")
    llm_temperature = Column(Float, default=0.3)
    max_response_tokens = Column(Integer, default=500)
    llm_max_tokens = Column(Integer, default=250)
    llm_emotion_recognition = Column(Boolean, default=False)

    # ── Call Behavior ─────────────────────────────
    silence_timeout_seconds = Column(Integer, default=10)
    max_duration_seconds = Column(Integer, default=300)
    background_sound = Column(String(50), default="none")
    background_denoising = Column(Boolean, default=False)
    model_output_in_realtime = Column(Boolean, default=False)
    end_call_phrases = Column(
        JSON,
        default=lambda: [
            "dhanyavaad", "thank you", "bye",
            "goodbye", "shukriya", "alvida"
        ]
    )
    end_call_message = Column(
        Text,
        default="Thank you for calling. Goodbye!"
    )

    # ── Capabilities ──────────────────────────────
    can_book_appointments = Column(Boolean, default=True)
    can_cancel_appointments = Column(Boolean, default=True)
    can_check_availability = Column(Boolean, default=True)
    can_transfer_emergency = Column(Boolean, default=True)
    emergency_transfer_number = Column(String(20), nullable=True)
    # Whether the transcriber PINS to `language` or lets the provider detect.
    # This is the only language-adjacent knob besides `language` itself, and it
    # predates the unification — no new knob was added. See
    # agent_defaults.effective_stt_language.
    auto_detect_language = Column(Boolean, default=True)

    # ── Voicemail ─────────────────────────────────
    voicemail_detection_enabled = Column(Boolean, default=False)
    voicemail_message = Column(Text, nullable=True)

    # ── Recording ─────────────────────────────────
    record_calls = Column(Boolean, default=False)
    recording_consent_plan = Column(String(50), nullable=True)

    # ── Post-call Processing ──────────────────────
    summary_enabled = Column(Boolean, default=True)
    success_evaluation_enabled = Column(Boolean, default=True)
    structured_output_enabled = Column(Boolean, default=False)
    tools_enabled = Column(Text, nullable=True)
    predefined_functions = Column(Text, nullable=True)
    custom_functions = Column(Text, nullable=True)

    # ── Keypad / SMS / Compliance ─────────────────
    keypad_input_enabled = Column(Boolean, default=False)
    keypad_timeout = Column(Integer, default=5)
    sms_enabled = Column(Boolean, default=False)
    sms_provider = Column(String(50), nullable=True)
    sms_message_template = Column(Text, nullable=True)
    hipaa_enabled = Column(Boolean, default=False)
    pii_redaction_enabled = Column(Boolean, default=False)

    # ── Telephony ─────────────────────────────────
    telephony_option = Column(String(20), default="skip")
    country_code = Column(String(5), default="IN")
    ai_number = Column(String(25), nullable=True)
    sip_provider = Column(String(30), nullable=True)
    sip_account_sid = Column(String(100), nullable=True)
    sip_auth_token = Column(String(100), nullable=True)
    sip_domain = Column(String(200), nullable=True)
    existing_clinic_number = Column(String(25), nullable=True)

    # ── LiveKit ───────────────────────────────────
    livekit_url = Column(String(200), nullable=True)
    livekit_api_key = Column(String(100), nullable=True)
    livekit_api_secret = Column(String(100), nullable=True)

    # ── Clinic Knowledge ──────────────────────────
    clinic_info = Column(JSON, default=lambda: {
        "working_hours": "9:00 AM - 7:00 PM, Mon-Sat",
        "address": "",
        "emergency_number": "112",
        "services": [],
        "faqs": []
    })

    # ── Webhooks ──────────────────────────────────
    webhook_url = Column(String(500), nullable=True)

    # ── Embed / Widget ─────────────────────────────
    # Public URL of the per-agent widget avatar (Supabase public bucket).
    # Nullable — widget falls back to the default icon when unset.
    avatar_url = Column(String(500), nullable=True)
    embed_enabled = Column(Boolean, default=True)
    embed_allowed_domains = Column(JSON, default=list)
    embed_position = Column(String(20), default="bottom-right")
    embed_theme = Column(String(10), default="dark")
    embed_button_text = Column(String(50), default="Talk to Receptionist")
    embed_primary_color = Column(String(7), default="#3ECF8E")
    embed_show_branding = Column(Boolean, default=True)
    # Widget launcher display mode: "button" (icon + label), "icon" (icon only),
    # or "auto-invite" (panel auto-expands after a delay — never auto-starts audio).
    embed_display_mode = Column(String(20), default="button")
    embed_auto_invite_delay = Column(Integer, default=3)  # seconds, auto-invite only

    # ── Status & Meta ─────────────────────────────
    status = Column(String(20), default="CONFIGURED")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=lambda: datetime.now(timezone.utc)
    )
