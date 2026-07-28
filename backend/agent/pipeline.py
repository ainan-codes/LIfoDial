"""
backend/agent/pipeline.py — Pipecat voice agent pipeline for Lifodial.

Architecture:
    LiveKit Room (caller)
        → LiveKitTransport.input()
        → SarvamSTTService          (transcription, Indian language support)
        → LLMContextAggregator      (builds message history for LLM)
        → BookingProcessor          (booking state machine, transparent)
        → GoogleLLMService          (Gemini 2.0 Flash, streaming)
        → LLMAssistantContextAggregator (stores assistant replies)
        → CallLoggerProcessor       (latency tracking, DB writes, transparent)
        → SarvamTTSService          (text-to-speech, Indian voices, streaming)
        → LiveKitTransport.output() (sends audio back to caller)

Key production guarantees:
  ✓ Sarvam STT + TTS — first-party Pipecat service (no custom HTTP wrappers)
  ✓ Silero VAD — barge-in / interruption detection
  ✓ BookingProcessor — multi-turn appointment booking state machine
  ✓ CallLoggerProcessor — call record DB writes + credit deduction (background tasks)
  ✓ Zero added latency — all DB writes are asyncio.create_task (fire-and-forget)
  ✓ All existing services (credit_service, his, call_evaluator) used unchanged
  ✓ All existing FastAPI routers untouched

Entrypoint: run `python -m backend.agent.pipeline start`
This boots a LiveKit agent worker that connects to your LiveKit cloud project
and handles inbound calls dispatched by the LiveKit SIP trunk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Optional

from loguru import logger as pipecat_logger

# ── Pipecat core ──────────────────────────────────────────────────────────────
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSModel, SarvamTTSService
from pipecat.services.openai.stt import OpenAISTTService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport

# Deepgram lazy import (requires deepgram-sdk; guarded so missing dep doesn't
# crash the worker if Deepgram is not the configured provider)
def _import_deepgram_stt():
    from pipecat.services.deepgram.stt import DeepgramSTTService
    return DeepgramSTTService

# ── Pipecat LiveKit token helper ──────────────────────────────────────────────
from livekit import api as livekit_api

# ── Local processors ──────────────────────────────────────────────────────────
from backend.agent.processors.booking_processor import BookingProcessor
from backend.agent.processors.call_logger_processor import (
    CallLoggerProcessor,
    UserTranscriptTap,
)
from backend.agent.processors.language_switcher import LanguageSwitchProcessor
from backend.agent.processors.transcript_publisher import LiveKitTranscriptPublisher

# ── App config ────────────────────────────────────────────────────────────────
from backend.config import settings

# Standard logger for non-pipecat code
log = logging.getLogger(__name__)


# ── Language → Sarvam code mapping ────────────────────────────────────────────
_LANG_TO_SARVAM: dict[str, str] = {
    "hi-IN": "hi-IN",
    "en-IN": "en-IN",
    "ta-IN": "ta-IN",
    "te-IN": "te-IN",
    "kn-IN": "kn-IN",
    "ml-IN": "ml-IN",
    "mr-IN": "mr-IN",
    "bn-IN": "bn-IN",
    "pa-IN": "pa-IN",
    "gu-IN": "gu-IN",
}


def _safe_lang(lang: str) -> str:
    """Return a Sarvam-supported language code, defaulting to hi-IN."""
    return _LANG_TO_SARVAM.get(lang, "hi-IN")


def _kb_context_block(tenant: dict) -> str:
    """Render the tenant's knowledge base as an appendable prompt block. Empty
    string when there are no entries (turn proceeds normally without KB)."""
    entries = tenant.get("knowledge_base") or []
    if not entries:
        return ""
    lines = [f"[{(e.get('category') or 'info').upper()}] {e.get('title','')}: {e.get('content','')}" for e in entries]
    return (
        "\n\n--- CLINIC KNOWLEDGE BASE ---\n"
        + "\n".join(lines)
        + "\n--- END KNOWLEDGE BASE ---\n"
        "Use the knowledge base above to answer clinic-specific questions. "
        "If it doesn't cover something, say you'll check with the clinic — never invent details."
    )


_DEFAULT_WORKING_HOURS = "9 AM – 7 PM, Mon–Sat"


def _clinic_facts_block(tenant: dict) -> str:
    """Clinic hours + the full doctor roster, as a block appended to EVERY prompt.

    Why this is separate from the template's own {working_hours} / {doctors_list}
    placeholders: those are interpolated ONLY when the prompt comes from
    prompt_templates. A clinic with a custom system_prompt — which is precedence
    #1 in _build_system_prompt — got neither. So the two things a clinic admin can
    actually edit about their clinic (its timings, and its doctors) were invisible
    to the agent for exactly the clinics that had customised anything.

    Same reasoning as _doctor_availability_block below, which already had to solve
    this for on-leave doctors. This block carries the positive roster and the
    hours; that one carries the emphatic do-not-book warning for absences.
    """
    hours = (tenant.get("working_hours") or "").strip() or _DEFAULT_WORKING_HOURS
    doctors = tenant.get("doctors") or []

    lines = [f"Working hours: {hours}"]
    if tenant.get("address"):
        lines.append(f"Address: {tenant['address']}")

    available = [d for d in doctors if d.get("is_available", True)]
    if available:
        lines.append("Doctors available to book:")
        lines += [
            f"  - {d['name']} ({d.get('specialization') or 'Specialist'})"
            for d in available
        ]
    elif doctors:
        lines.append(
            "No doctor is available right now — every doctor on staff is on leave."
        )
    else:
        # An empty roster is a real state for a new clinic. Say so explicitly, or
        # the model invents a doctor to be helpful.
        lines.append(
            "No doctors have been added to this clinic yet. You therefore CANNOT "
            "book an appointment with a named doctor — offer to take the caller's "
            "details so the clinic can call them back instead, and never invent a "
            "doctor's name."
        )

    return (
        "\n\n--- CLINIC DETAILS ---\n"
        + "\n".join(lines)
        + "\n--- END CLINIC DETAILS ---\n"
        "Only ever offer or confirm appointment times that fall INSIDE the working "
        "hours above. If the caller asks for a time outside them, say the clinic is "
        "closed then and offer the nearest time that is open. Never invent a doctor, "
        "a specialization, or an opening time that is not listed above.\n"
    )


def _doctor_availability_block(tenant: dict) -> str:
    """Doctors currently on leave, as an appendable prompt block. Empty string
    when everyone is available (turn proceeds normally). Appended alongside
    the KB block so this reaches the LLM regardless of whether the clinic uses
    a custom prompt, a template, or the hardcoded fallback — the template's own
    doctors_list placeholder (below) is template-only and does NOT cover a
    custom prompt, so this is the one place that reaches every prompt path."""
    unavailable = [d for d in (tenant.get("doctors") or []) if not d.get("is_available", True)]
    if not unavailable:
        return ""
    lines = [
        f"- {d['name']} ({d.get('specialization', 'Specialist')}) is ON LEAVE"
        + (f" — {d['leave_reason']}" if d.get("leave_reason") else "")
        for d in unavailable
    ]
    return (
        "\n\n--- DOCTOR AVAILABILITY ---\n"
        + "\n".join(lines)
        + "\n--- END DOCTOR AVAILABILITY ---\n"
        "Any doctor listed above is NOT available right now. If the caller asks for one of "
        "them by name or specialization, tell them clearly that the doctor is on leave "
        "(mention the reason if one is given) — do not offer to book that doctor. Instead, "
        "offer another available doctor with the same specialization if one exists, or ask "
        "the caller if they'd like to be notified when the doctor is back."
    )


# Appended to EVERY system prompt (custom, template, or fallback). This is the
# honesty contract that pairs with BookingProcessor._commit_and_inject_result:
# the DB write is awaited and its outcome arrives as a [BOOKING_RESULT] system
# message BEFORE the LLM generates — so the model must never claim success on
# its own (audit FIX 4). Defined once in backend/agent/booking_rules.py and
# shared with the chat/embed path (agent_test.py) so the two implementations
# cannot drift apart again.
from backend.agent.booking_rules import BOOKING_RULES_BLOCK as _BOOKING_RULES_BLOCK


def _build_system_prompt(agent_config: dict, tenant: dict) -> str:
    """
    Build the LLM system prompt from stored config, or render from template,
    then append the clinic knowledge base (if any) and the booking honesty
    rules (always).

    Precedence:
      1. agent_config['system_prompt'] — custom prompt set by clinic admin
      2. Rendered prompt_templates entry for agent_config['template']
      3. Hardcoded fallback
    """
    # Pairs with LanguageSwitchProcessor: that processor retunes STT/TTS when the
    # caller changes language, but the words themselves come from the LLM, so the
    # model has to be told to follow the caller. Appended to every prompt path
    # (custom, template, fallback) so a clinic's own prompt can't lose it.
    _LANGUAGE_MIRROR_RULE = (
        "\n\n--- LANGUAGE ---\n"
        "Always reply in the SAME language the caller used in their most recent message, "
        "even if that changes part-way through the call. Never announce the switch or "
        "comment on which language is being spoken — just answer in it.\n"
    )

    kb_block = (
        _kb_context_block(tenant)
        # Hours + roster BEFORE the availability warning, so the model reads "who
        # exists and when we're open" and then "who of those is away".
        + _clinic_facts_block(tenant)
        + _doctor_availability_block(tenant)
        + _BOOKING_RULES_BLOCK
        + _LANGUAGE_MIRROR_RULE
    )

    custom_prompt = (agent_config.get("system_prompt") or "").strip()
    if custom_prompt:
        return custom_prompt + kb_block

    # Try template render
    try:
        from backend.agent.prompt_templates import get_template, render_prompt

        lang = agent_config.get("tts_language", "hi-IN")
        template_key = agent_config.get("template", "clinic_receptionist")
        tmpl = get_template(template_key, lang)

        doctors = tenant.get("doctors", [])
        doctors_list = "\n".join(
            f"- {d['name']} ({d.get('specialization', 'Specialist')})"
            + ("" if d.get("is_available", True) else " — ON LEAVE, do not book")
            for d in doctors
        ) or "- General Physician available"

        rendered = render_prompt(
            tmpl["system_prompt"],
            {
                "clinic_name": tenant.get("clinic_name", "the clinic"),
                "agent_name": agent_config.get("agent_name", "Receptionist"),
                "clinic_location": tenant.get("location", "India"),
                "working_hours": tenant.get("working_hours") or _DEFAULT_WORKING_HOURS,
                "emergency_number": tenant.get("emergency_number", "108"),
                "doctors_list": doctors_list,
            },
        )
        return rendered + kb_block

    except Exception as exc:
        log.warning("Template render failed, using fallback prompt: %s", exc)

    # Hardcoded fallback
    return (
        f"You are {agent_config.get('agent_name', 'Receptionist')}, "
        f"the AI voice receptionist for {tenant.get('clinic_name', 'the clinic')}. "
        "Be concise, professional, and helpful. Maximum 2 sentences per response. "
        "Never give medical advice."
    ) + kb_block


def _generate_agent_token(room_name: str) -> str:
    """
    Generate a LiveKit access token for the Pipecat agent to join a room.

    NOTE: agent=True in VideoGrants does NOT make LiveKit report this participant
    with kind=AGENT — verified empirically against LiveKit Cloud (livekit 1.1.3):
    a token carrying the agent grant still joins as kind=0/STANDARD. Only the
    livekit-agents job participant (from ctx.connect()) gets kind=AGENT, and it
    publishes no tracks. That is why the frontend cannot rely on
    useVoiceAssistant/kind to find the speaking agent and instead resolves it from
    the subscribed audio track's participant (see TestVoiceCallLK.tsx). The grant
    is kept because it is harmless and semantically correct.
    """
    token = livekit_api.AccessToken(
        settings.livekit_api_key,
        settings.livekit_api_secret,
    )
    token.with_identity(f"lifodial-agent-{uuid.uuid4().hex[:6]}")
    token.with_name("Lifodial AI Agent")
    token.with_grants(
        livekit_api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
            agent=True,  # REQUIRED: marks this as the AI agent for useVoiceAssistant
        )
    )
    return token.to_jwt()


async def _load_tenant_and_config(
    tenant_id: Optional[str],
    agent_id: Optional[str],
    metadata: dict,
) -> tuple[dict, dict]:
    """
    Load agent config and tenant data from DB.

    Falls back to metadata defaults if DB is unavailable (graceful degradation).

    Returns:
        (agent_config dict, tenant dict)
    """
    agent_config: dict = {
        "agent_name":      metadata.get("agent_name", "Receptionist"),
        "first_message":   metadata.get("first_message", ""),
        "first_message_mode": metadata.get("first_message_mode", "assistant-speaks-first"),
        "system_prompt":   metadata.get("system_prompt", ""),
        "template":        metadata.get("template", "clinic_receptionist"),
        "stt_provider":    metadata.get("stt_provider", "sarvam"),
        "tts_provider":    metadata.get("tts_provider", "sarvam"),
        "tts_voice":       metadata.get("tts_voice", "priya"),
        "tts_language":    metadata.get("tts_language", "hi-IN"),
        "tts_model":       metadata.get("tts_model", "bulbul:v3"),
        "tts_pace":        float(metadata.get("tts_pace", 1.05)),
        "tts_pitch":       float(metadata.get("tts_pitch", 0.0) or 0.0),
        "tts_loudness":    float(metadata.get("tts_loudness", 1.0) or 1.0),
        "tts_input_preprocessing": bool(metadata.get("tts_input_preprocessing", True)),
        "tts_stability":   metadata.get("tts_stability"),
        "tts_clarity":     metadata.get("tts_clarity"),
        "tts_style":       metadata.get("tts_style"),
        "tts_use_speaker_boost": bool(metadata.get("tts_use_speaker_boost", False)),
        "tts_speed":       metadata.get("tts_speed"),
        "stt_model":       metadata.get("stt_model", "saaras:v2"),
        "stt_language":    metadata.get("stt_language", "hi-IN"),
        "llm_model":       metadata.get("llm_model", "gemini-2.0-flash"),
        # The agent's EXPLICIT LLM provider choice. Without this key,
        # resilience.select_llm_provider() only ever saw "" and had to guess the
        # provider from the model-name prefix (_provider_from_model), so choosing
        # Anthropic/Mistral/Ollama or a custom OpenAI-compatible endpoint silently
        # ran Gemini instead, and a Cerebras "llama-*" model got routed to Groq.
        # The dashboard's text-test path already read agent.llm_provider, so the
        # test chat and the real voice call disagreed about which model was running.
        "llm_provider":    (metadata.get("llm_provider") or "").strip(),
        "llm_temperature": float(metadata.get("llm_temperature", 0.3)),
        "max_response_tokens": int(metadata.get("max_response_tokens", 120)),
        # ── Tool toggles (Tools tab) ──────────────────────────────────────
        "can_book_appointments":   bool(metadata.get("can_book_appointments", True)),
        "can_cancel_appointments": bool(metadata.get("can_cancel_appointments", True)),
        "can_check_availability":  bool(metadata.get("can_check_availability", True)),
        "can_transfer_emergency":  bool(metadata.get("can_transfer_emergency", True)),
        "emergency_transfer_number": metadata.get("emergency_transfer_number"),
        # ── Post-call analysis toggles (Analysis tab) ──────────────────────
        "summary_enabled":            bool(metadata.get("summary_enabled", True)),
        "success_evaluation_enabled": bool(metadata.get("success_evaluation_enabled", True)),
        "structured_output_enabled":  bool(metadata.get("structured_output_enabled", False)),
        # ── Call Behavior ───────────────────────────────────────────────────
        "silence_timeout_seconds": int(metadata.get("silence_timeout_seconds", 10) or 10),
        "max_duration_seconds":    int(metadata.get("max_duration_seconds", 300) or 300),
        "end_call_phrases":        metadata.get("end_call_phrases") or [],
        "end_call_message":        metadata.get("end_call_message", "Thank you for calling. Goodbye!"),
        "recording_consent_plan":  metadata.get("recording_consent_plan", "none"),
        # No real agent_id (ad-hoc/metadata-only test room) => nothing to
        # unpublish, so default to allowed. Overwritten below when a real
        # AgentConfig row is loaded.
        "status": "ACTIVE",
    }

    tenant: dict = {
        "id":            tenant_id or "",
        "clinic_name":   metadata.get("clinic_name", "Clinic"),
        "working_hours": "9 AM – 7 PM, Mon–Sat",
        "doctors":       [],
        "knowledge_base": [],
    }

    if not tenant_id and not agent_id:
        log.warning("No tenant_id or agent_id in room metadata — using defaults.")
        return agent_config, tenant

    try:
        from backend.db import AsyncSessionLocal
        from backend.models.agent_config import AgentConfig
        from backend.models.doctor import Doctor
        from backend.models.tenant import Tenant
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            # Load AgentConfig
            if agent_id:
                result = await db.execute(
                    select(AgentConfig).where(AgentConfig.id == agent_id)
                )
                cfg = result.scalar_one_or_none()
                if cfg:
                    agent_config.update({
                        "agent_name":          cfg.agent_name or "Receptionist",
                        "first_message":       cfg.first_message or "",
                        "first_message_mode":  getattr(cfg, "first_message_mode", "assistant-speaks-first") or "assistant-speaks-first",
                        "system_prompt":       cfg.system_prompt or "",
                        "template":            getattr(cfg, "template", "clinic_receptionist"),
                        "stt_provider":        getattr(cfg, "stt_provider", "sarvam") or "sarvam",
                        "tts_provider":        getattr(cfg, "tts_provider", "sarvam") or "sarvam",
                        "tts_voice":           cfg.tts_voice or "priya",
                        "tts_language":        cfg.tts_language or "hi-IN",
                        "tts_model":           cfg.tts_model or "bulbul:v3",
                        "tts_pace":            float(cfg.tts_pace or 1.05),
                        "tts_pitch":           float(cfg.tts_pitch if cfg.tts_pitch is not None else 0.0),
                        "tts_loudness":        float(cfg.tts_loudness if cfg.tts_loudness is not None else 1.0),
                        "tts_input_preprocessing": bool(cfg.tts_input_preprocessing if cfg.tts_input_preprocessing is not None else True),
                        "tts_stability":       cfg.tts_stability,
                        "tts_clarity":         cfg.tts_clarity,
                        "tts_style":           cfg.tts_style,
                        "tts_use_speaker_boost": bool(cfg.tts_use_speaker_boost or False),
                        "tts_speed":           cfg.tts_speed,
                        "stt_model":           cfg.stt_model or "saaras:v2",
                        "stt_language":        cfg.stt_language or "hi-IN",
                        "llm_model":           cfg.llm_model or "gemini-2.0-flash",
                        # See the note on "llm_provider" in the metadata-only branch
                        # above — the AgentConfig row has always had this column, the
                        # pipeline just never read it, so the provider was inferred
                        # from the model name and any non-inferable choice ran Gemini.
                        "llm_provider":        (getattr(cfg, "llm_provider", "") or "").strip(),
                        "llm_temperature":     float(cfg.llm_temperature or 0.3),
                        "max_response_tokens": int(cfg.max_response_tokens or 120),
                        "can_book_appointments":   bool(cfg.can_book_appointments if cfg.can_book_appointments is not None else True),
                        "can_cancel_appointments": bool(cfg.can_cancel_appointments if cfg.can_cancel_appointments is not None else True),
                        "can_check_availability":  bool(cfg.can_check_availability if cfg.can_check_availability is not None else True),
                        "can_transfer_emergency":  bool(cfg.can_transfer_emergency if cfg.can_transfer_emergency is not None else True),
                        "emergency_transfer_number": cfg.emergency_transfer_number,
                        "summary_enabled":            bool(cfg.summary_enabled if cfg.summary_enabled is not None else True),
                        "success_evaluation_enabled": bool(cfg.success_evaluation_enabled if cfg.success_evaluation_enabled is not None else True),
                        "structured_output_enabled":  bool(cfg.structured_output_enabled or False),
                        "silence_timeout_seconds": int(cfg.silence_timeout_seconds or 10),
                        "max_duration_seconds":    int(cfg.max_duration_seconds or 300),
                        "end_call_phrases":        cfg.end_call_phrases or [],
                        "end_call_message":        cfg.end_call_message or "Thank you for calling. Goodbye!",
                        "recording_consent_plan":  getattr(cfg, "recording_consent_plan", None) or "none",
                        # Clinic-owned facts the receptionist must know: working
                        # hours, address, emergency number, services, FAQs. This is
                        # what Settings -> Clinic Profile writes its calling hours
                        # into, and it is the ONLY place those hours are stored —
                        # Tenant has no working_hours column (see below).
                        "clinic_info":         cfg.clinic_info if isinstance(cfg.clinic_info, dict) else {},
                        "status":              cfg.status,
                    })
                    log.info("AgentConfig loaded from DB: agent_id=%s", agent_id)

            # Load Tenant + Doctors
            if tenant_id:
                t_result = await db.execute(
                    select(Tenant).where(Tenant.id == tenant_id)
                )
                t = t_result.scalar_one_or_none()
                if t:
                    tenant["id"]            = str(t.id)
                    tenant["clinic_name"]   = t.clinic_name
                    tenant["location"]      = getattr(t, "location", "India")

                    # Working hours come from AgentConfig.clinic_info, which is where
                    # Settings -> Clinic Profile saves them.
                    #
                    # This used to read getattr(t, "working_hours", "9 AM – 7 PM, Mon–Sat")
                    # — but Tenant has NO working_hours column, so getattr always fell
                    # through to that default. Every clinic's agent therefore believed it
                    # opened 9-7 Mon-Sat no matter what the clinic had configured, and a
                    # clinic that set 10:00-18:00 had an agent happily offering 9 AM.
                    _ci = agent_config.get("clinic_info") or {}
                    tenant["working_hours"] = (
                        (_ci.get("working_hours") or "").strip()
                        or _DEFAULT_WORKING_HOURS
                    )
                    # Same source for the other clinic facts the prompt interpolates.
                    if (_ci.get("emergency_number") or "").strip():
                        tenant["emergency_number"] = _ci["emergency_number"].strip()
                    if (_ci.get("address") or "").strip():
                        tenant["address"] = _ci["address"].strip()

                d_result = await db.execute(
                    select(Doctor).where(Doctor.tenant_id == tenant_id)
                )
                tenant["doctors"] = [
                    {
                        "id":             str(d.id),
                        "name":           d.name,
                        "specialization": d.specialization,
                        "is_available":   d.is_available,
                        "leave_reason":   d.leave_reason,
                    }
                    for d in d_result.scalars().all()
                ]
                log.info(
                    "Tenant loaded from DB: %s (%d doctors)",
                    tenant["clinic_name"], len(tenant["doctors"]),
                )

                # Knowledge base entries (same source the WS/embed path already
                # injects) — so the pipecat pipeline is KB-aware too.
                try:
                    from backend.models.knowledge_base import KnowledgeBase
                    kb_result = await db.execute(
                        select(KnowledgeBase).where(
                            KnowledgeBase.tenant_id == tenant_id,
                            KnowledgeBase.is_active == True,  # noqa: E712
                        )
                    )
                    tenant["knowledge_base"] = [
                        {"category": e.category, "title": e.title, "content": e.content}
                        for e in kb_result.scalars().all()
                    ]
                    log.info("Knowledge base loaded: %d entries", len(tenant["knowledge_base"]))
                except Exception as kb_exc:
                    log.warning("Knowledge base load failed (non-fatal): %s", kb_exc)

    except Exception as exc:
        log.warning(
            "DB load failed — using metadata defaults. Error: %s", exc
        )

    return agent_config, tenant


async def _create_call_record(
    tenant_id: Optional[str],
    agent_id: Optional[str],
    call_meta: dict,
) -> Optional[str]:
    """
    Create a CallRecord row at call start and return its UUID.

    Returns None if DB write fails (call continues regardless).
    """
    if not tenant_id:
        return None

    try:
        from datetime import datetime, timezone

        from backend.db import AsyncSessionLocal
        from backend.models.call_record import CallRecord

        call_id = str(uuid.uuid4())
        async with AsyncSessionLocal() as db:
            record = CallRecord(
                id=call_id,
                tenant_id=tenant_id,
                agent_id=agent_id,
                call_type=call_meta.get("call_type", "inbound"),
                patient_number_masked=_mask_phone(
                    call_meta.get("caller_phone", "unknown")
                ),
                started_at=datetime.now(timezone.utc),
                status="active",
                turn_count=0,
                transcript=[],
            )
            db.add(record)
            await db.commit()

        log.info("CallRecord created: id=%s", call_id)
        return call_id

    except Exception as exc:
        log.error("Failed to create CallRecord: %s", exc, exc_info=True)
        return None


def _mask_phone(phone: str) -> str:
    """Mask phone number for HIPAA-style PII reduction: +91XXXXXXX3456."""
    if not phone or phone == "unknown":
        return "unknown"
    if len(phone) > 4:
        return phone[:-4].replace(phone[2:-6], "X" * max(len(phone) - 6, 0)) + phone[-4:]
    return phone


# ── Main entrypoint ───────────────────────────────────────────────────────────

async def entrypoint(ctx) -> None:
    """
    LiveKit agent entrypoint.

    Called once per incoming call by the livekit-agents worker.
    Builds the Pipecat pipeline and runs it until the call ends.

    ctx: livekit.agents.JobContext
    """
    # ── Parse call metadata ─────────────────────────────────────────────────
    # Prefer room metadata (the web-call flow sets it at create_room). Fall back
    # to the job's dispatch metadata, which is where an explicit agent dispatch
    # (SIP inbound, or a programmatic create_dispatch) carries it. Without this
    # fallback, explicit-dispatch jobs saw tenant/agent = None and ran on
    # defaults.
    # ORDER MATTERS. ctx.room is a *not-yet-connected* rtc.Room at this point
    # (ctx.connect() happens further down), so ctx.room.metadata is ALWAYS "" here
    # — reading it first meant tenant_id/agent_id were silently None on every
    # single call, and the agent ran the whole conversation on hardcoded defaults:
    # wrong clinic name, no DB first_message, no knowledge base, no publish/credit
    # gate, and no CallRecord ("No call_record_id — skipping finalization").
    # ctx.job.room.metadata is the room's metadata as delivered with the job
    # dispatch and IS populated before connect — that's the authoritative source.
    metadata: dict = {}
    _raw_meta = ""
    try:
        _job = getattr(ctx, "job", None)
        for _candidate in (
            getattr(getattr(_job, "room", None), "metadata", ""),  # dispatch payload (reliable pre-connect)
            getattr(ctx.room, "metadata", ""),                     # only set post-connect
            getattr(_job, "metadata", ""),                         # explicit create_dispatch metadata
        ):
            _raw_meta = (_candidate or "").strip()
            if _raw_meta:
                break
        metadata = json.loads(_raw_meta or "{}")
    except (json.JSONDecodeError, AttributeError):
        pass

    tenant_id: Optional[str]  = metadata.get("tenant_id")
    agent_id: Optional[str]   = metadata.get("agent_id")
    caller_phone: str         = (
        metadata.get("caller_phone")
        or metadata.get("from_number")
        or metadata.get("patient_phone")
        or "unknown"
    )
    room_name: str = ctx.room.name

    log.info(
        "Agent entrypoint | room=%s tenant=%s agent=%s caller=%s",
        room_name, tenant_id, agent_id, caller_phone,
    )

    # ── Load config from DB ────────────────────────────────────────────────
    agent_config, tenant = await _load_tenant_and_config(tenant_id, agent_id, metadata)

    # ── Publish/Unpublish enforcement — single source of truth is
    # AgentConfig.status (see backend/routers/embed.py's _is_published for the
    # matching check on the widget side). Only enforced when this room is tied
    # to a real agent_id — an unpublished agent must not take NEW calls, but a
    # call already in progress when it's unpublished is unaffected (this check
    # only runs once, at room-join time, not mid-call). Declining here — before
    # ctx.connect() — means the room is never joined, so no call minutes/audio
    # are billed or recorded for a call that was never allowed to start.
    # test_mode (in-dashboard "Test Agent") bypasses the publish gate so an admin
    # can test an agent that isn't ACTIVE yet — it's the same pipeline, just not
    # a real/billable inbound call.
    test_mode = bool(metadata.get("test_mode", False))
    if agent_id and not test_mode and agent_config.get("status") != "ACTIVE":
        log.warning(
            "Declining call: agent_id=%s is unpublished (status=%s) — not joining room %s",
            agent_id, agent_config.get("status"), room_name,
        )
        return
    if test_mode:
        log.info("TEST MODE call — publish gate bypassed for agent_id=%s", agent_id)

    # ── Pre-call credit gate (audit P4) ─────────────────────────────────────
    # A real (non-test) call must not start unless the clinic's prepaid balance
    # covers the worst-case cost of a full-length call. Declining here — before
    # _create_call_record() / ctx.connect() — guarantees a call can never drive
    # the balance negative (the bug that left a clinic at ₹-1.50: deduction ran
    # post-call with no floor and nothing checked the balance up front). Same
    # decline-before-connect shape as the publish gate above; test_mode bypasses
    # it just like the publish gate.
    if tenant_id and not test_mode:
        from backend.db import AsyncSessionLocal
        from backend.services.credit_service import CreditService

        max_dur = int(agent_config.get("max_duration_seconds") or 300)
        async with AsyncSessionLocal() as _credit_db:
            gate = await CreditService.check_call_allowed(_credit_db, tenant_id, max_dur)
        if not gate["allowed"]:
            log.warning(
                "Declining call: tenant=%s failed credit gate (reason=%s balance=₹%.2f "
                "required=₹%.2f) — not joining room %s",
                tenant_id, gate["reason"], gate["balance"], gate["required"], room_name,
            )
            return

    # ── Create call record ─────────────────────────────────────────────────
    call_meta = {
        "caller_phone": caller_phone,
        "call_type":    "inbound",
        "room_name":    room_name,
    }
    call_record_id = await _create_call_record(tenant_id, agent_id, call_meta)
    call_meta["call_record_id"] = call_record_id

    # ── Connect to LiveKit room (REQUIRED by livekit-agents framework) ────────
    # ctx.connect() MUST be called here so livekit-agents worker framework marks
    # the dispatched job as accepted with LiveKit Cloud. auto_subscribe=False so
    # Pipecat's LiveKitTransport handles audio stream subscriptions cleanly.
    await ctx.connect(auto_subscribe=False)

    # ── Generate agent token ───────────────────────────────────────────────
    agent_token = _generate_agent_token(room_name)

    # ── Resolve TTS voice & model ──────────────────────────────────────────
    tts_model_str = agent_config.get("tts_model", "bulbul:v3")
    tts_voice     = agent_config.get("tts_voice", "priya")
    tts_pace      = min(max(float(agent_config.get("tts_pace", 1.05)), 0.5), 2.0)
    tts_language  = _safe_lang(agent_config.get("tts_language", "hi-IN"))
    # bulbul:v2 is the only Sarvam model that accepts pitch/loudness — Pipecat's
    # SarvamTTSService silently ignores them for v3/v3-beta, so it's always
    # safe to pass through (unlike the raw-httpx Sarvam calls elsewhere, which
    # error on these params for v3 and must guard explicitly).
    tts_pitch     = min(max(float(agent_config.get("tts_pitch") or 0.0), -0.75), 0.75)
    tts_loudness  = min(max(float(agent_config.get("tts_loudness") or 1.0), 0.3), 3.0)
    tts_input_preprocessing = bool(agent_config.get("tts_input_preprocessing", True))

    # Validate tts_model against Pipecat's SarvamTTSModel enum values
    valid_tts_models = {m.value for m in SarvamTTSModel}
    if tts_model_str not in valid_tts_models:
        log.warning(
            "Unknown TTS model '%s' — falling back to bulbul:v3", tts_model_str
        )
        tts_model_str = "bulbul:v3"

    # ── Build system prompt ────────────────────────────────────────────────
    system_prompt = _build_system_prompt(agent_config, tenant)

    # ── Build first message ────────────────────────────────────────────────
    first_message: str = (
        agent_config.get("first_message", "").strip()
        or f"Namaste! {tenant['clinic_name']} mein aapka swagat hai. "
           f"Main {agent_config.get('agent_name', 'Receptionist')} hoon. "
           "Aaj main aapki kaise madad kar sakti hoon?"
    )

    # ── STT Settings ───────────────────────────────────────────────────────
    stt_model = agent_config.get("stt_model", "saaras:v2")
    valid_stt_models = {"saarika:v2.5", "saaras:v2.5", "saaras:v3"}
    if stt_model not in valid_stt_models:
        # Legacy model name compat: "saaras:v2" → "saaras:v2.5"
        stt_model = "saaras:v2.5"

    # STT Language dropdown was previously ignored — _load_tenant_and_config
    # never loaded stt_language into agent_config, so this always fell back to
    # the TTS language. Now wired: use the agent's own STT language setting,
    # falling back to TTS language only if it's genuinely unset.
    stt_language = _safe_lang(agent_config.get("stt_language") or tts_language)

    # saaras:v2.5 auto-detects language — don't pass language for it
    if stt_model == "saaras:v2.5":
        stt_settings = SarvamSTTService.Settings(
            model=stt_model,
        )
    else:
        stt_settings = SarvamSTTService.Settings(
            model=stt_model,
            language=stt_language,
        )

    # ── Instantiate Pipecat services ───────────────────────────────────────

    # Transport — connects Pipecat to the LiveKit room.
    # agent_token has agent=True in VideoGrants so the frontend useVoiceAssistant
    # hook identifies this participant as the AI agent correctly.
    transport = LiveKitTransport(
        url=settings.livekit_url,
        token=agent_token,
        room_name=room_name,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            # ⚠️ Do NOT put vad_enabled / vad_analyzer here. Those were pipecat
            # 0.x transport params; in 1.5.0 LiveKitParams has neither field and
            # pydantic silently DROPS unknown kwargs — so the Silero analyzer
            # that used to be configured here was never instantiated and the
            # tuned VADParams were dead config. VAD is now a real pipeline
            # processor (see VADProcessor below), which is how 1.5.0 wires it.
        ),
    )

    # STT — Deepgram (real-time streaming), Sarvam AI, OpenAI Whisper, ElevenLabs,
    # or AssemblyAI
    stt_provider = agent_config.get("stt_provider", "sarvam")

    # Set by the branches below and consumed by LanguageSwitchProcessor: whether a
    # mid-call language change has to be pushed into the STT service, and how to
    # translate our BCP-47 code into that provider's code. Left False for models
    # that already detect language themselves (Sarvam saaras, Deepgram nova-3
    # "multi") — reconnecting a healthy socket would only add deaf time.
    stt_needs_language_switch = False
    stt_language_translator = None

    # ── Resolve STT provider keys — DB (AI Platform dashboard) first, env
    # fallback, via backend/agent/providers.py::resolve_key. A key saved
    # through the dashboard now takes effect on the very next call: no
    # redeploy, no env var edit, no worker restart. See providers.py for how
    # to register a new provider here.
    #
    # Only the selected provider (plus "sarvam", the deaf-agent-guard fallback,
    # and "openai" when stt_provider aliases to it) is ever read below, so only
    # those are resolved — each is a DB round-trip, and this runs on every call
    # setup on a latency-sensitive path.
    from backend.db import AsyncSessionLocal
    from backend.agent import providers as provider_registry
    _stt_needed = {stt_provider, "sarvam"}
    if stt_provider in ("openai", "whisper"):
        _stt_needed.add("openai")
    async with AsyncSessionLocal() as _key_db:
        _stt_keys = {
            p: await provider_registry.resolve_key(_key_db, p, category="stt")
            for p in _stt_needed
        }

    # ── Deaf-agent guard ────────────────────────────────────────────────────
    # NONE of the STT services raise when handed an empty api_key — they build
    # fine and only fail later at the websocket/HTTP handshake with a 401. The
    # pipeline keeps running, TTS still has its own key, so the agent joins the
    # room, speaks its greeting, and then never transcribes a single word. That
    # silent failure mode is what made "voice input is not taken at all" so hard
    # to see: the only symptom is the ABSENCE of transcription.
    #
    # This is the exact bug that shipped when stt_provider was switched to
    # deepgram while DEEPGRAM_API_KEY was never added to the agent worker's env
    # (render.yaml's lifodial-agent service). So: verify the selected provider's
    # key up front, shout if it's missing, and degrade to a provider that can
    # actually hear rather than running the whole call deaf.
    if not (_stt_keys.get(stt_provider) or "").strip():
        _fallback = "sarvam" if (_stt_keys.get("sarvam") or "").strip() else None
        log.critical(
            "STT provider '%s' is selected but its API key is MISSING/EMPTY. The STT "
            "socket will 401 and the agent would greet the caller and then never hear "
            "a word (silently deaf). %s",
            stt_provider,
            f"Falling back to '{_fallback}' STT for room={room_name}." if _fallback
            else "No fallback STT key available either — this call WILL have no speech input.",
        )
        if _fallback:
            stt_provider = _fallback

    # Unbuildable-provider guard. The guard above only proves the SELECTED provider
    # has a key — it can't see that the provider has no `elif` branch below and will
    # therefore fall through to the Sarvam `else:`. google_stt and azure_stt are both
    # in the AI Platform catalog and both resolve a key successfully (via
    # provider_status._SPECIAL_ATTR), so they passed the key check and then silently
    # transcribed through Sarvam instead — or, with no Sarvam key, ran the call
    # completely deaf, which is exactly what the guard above exists to prevent.
    #
    # The buildable set is imported, not redeclared: backend/services/provider_registry.py
    # is the one place that knows what this file can construct, and the API now
    # refuses to SAVE anything outside it — so reaching this branch means either an
    # older row or a registry/pipeline mismatch. Both are worth shouting about.
    from backend.services.provider_registry import BUILDABLE_STT as _BUILDABLE_STT
    if stt_provider not in _BUILDABLE_STT:
        log.critical(
            "STT provider '%s' is selected but this pipeline has no build branch for it "
            "(buildable: %s). Falling back to Sarvam STT for room=%s — transcription will "
            "NOT use the provider shown in the dashboard. Add a branch in pipeline.py + a "
            "builder in backend/agent/providers.py to support it.",
            stt_provider, sorted(_BUILDABLE_STT), room_name,
        )
        stt_provider = "sarvam"
        if not (_stt_keys.get("sarvam") or "").strip():
            log.critical(
                "…and there is no Sarvam key either — room=%s WILL have no speech input.",
                room_name,
            )

    if stt_provider == "deepgram":
        # Deepgram Nova-3: real-time streaming with ~200ms TTFB (vs ~800ms Sarvam batch).
        # Best for English; supports Hindi/Tamil/Telugu with nova-2.
        log.info("Instantiating Deepgram streaming STT...")
        DeepgramSTTService = _import_deepgram_stt()
        # Map our BCP-47 language code to Deepgram's language code
        _dg_lang_map = {
            "en-IN": "en-IN", "en-US": "en-US", "en-GB": "en-GB",
            "hi-IN": "hi",     "ta-IN": "ta",     "te-IN": "te",
            "kn-IN": "kn",     "ml-IN": "ml",     "mr-IN": "mr",
            "bn-IN": "bn",     "pa-IN": "pa",     "gu-IN": "gu",
        }
        dg_lang = _dg_lang_map.get(stt_language, "en-IN")
        # Honour the model picked in the UI (nova-2-phonecall, nova-2-medical, …).
        # agent_config["stt_model"] is shared with Sarvam and defaults to a
        # "saaras:*" value, so only accept strings that are actually Deepgram
        # model names; anything else falls back to the language-based default:
        # nova-3 for English, nova-2 for Indic (nova-3 is English-optimised).
        _dg_default = "nova-3" if dg_lang.startswith("en") else "nova-2"
        _dg_requested = (agent_config.get("stt_model") or "").strip()
        dg_model = (
            _dg_requested
            if _dg_requested.startswith(("nova-", "nova", "base", "enhanced"))
            else _dg_default
        )
        if _dg_requested and dg_model != _dg_requested:
            log.info(
                "STT model %r is not a Deepgram model — using %s for room=%s",
                _dg_requested, dg_model, room_name,
            )
        # nova-3 takes only "en*" or "multi" as its language.
        #
        # Prefer "multi" even for English: nova-3 multilingual code-switches inside
        # ONE socket, so a caller moving English → Hindi mid-call costs NOTHING.
        # Pinning "en-IN" instead would force LanguageSwitchProcessor to send an
        # STTUpdateSettingsFrame, and Deepgram's _update_settings reconnects the
        # websocket — ~200-400ms deaf right at the moment the caller switched.
        #
        # nova-3 multilingual does NOT cover every language this product serves
        # (Tamil/Telugu/Malayalam/Kannada/Bengali/Gujarati/Punjabi/Marathi/Odia are
        # nova-2-only), so for those an explicit nova-3 choice is downgraded to
        # nova-2 rather than silently transcribing nothing.
        _NOVA3_MULTI_LANGS = {"en", "hi"}  # of ours; nova-3 multi also covers es/fr/de/it/nl/pt/ja/ru
        if dg_model.startswith("nova-3"):
            _base = dg_lang.split("-")[0]
            if _base in _NOVA3_MULTI_LANGS:
                dg_lang = "multi"
            else:
                log.warning(
                    "nova-3 does not support %s — falling back to nova-2 for room=%s",
                    dg_lang, room_name,
                )
                dg_model = "nova-2"
        log.info("Deepgram STT: model=%s language=%s", dg_model, dg_lang)
        stt = DeepgramSTTService(
            api_key=_stt_keys["deepgram"],
            settings=DeepgramSTTService.Settings(
                model=dg_model,
                language=dg_lang,
                smart_format=True,
                interim_results=True,
                endpointing=300,  # 300ms silence before utterance finalized
                punctuate=True,
                utterance_end_ms=1000,
            ),
        )
        # nova-3 on "multi" already code-switches inside one socket, so only the
        # language-pinned models need a mid-call STT retune.
        stt_needs_language_switch = dg_lang != "multi"

        def stt_language_translator(code: str, _model: str = dg_model) -> str:
            # nova-3 accepts only "en*" or "multi"; older models take real codes.
            if _model.startswith("nova-3"):
                return "en" if code.startswith("en") else "multi"
            return _dg_lang_map.get(code, "")
    elif stt_provider in ("openai", "whisper"):
        log.info("Instantiating OpenAI Whisper STT...")
        stt = OpenAISTTService(
            api_key=_stt_keys.get(stt_provider) or _stt_keys["openai"],
            model="whisper-1"
        )
    elif stt_provider == "elevenlabs":
        log.info("Instantiating ElevenLabs Realtime STT...")
        from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService

        # ElevenLabs Scribe uses ISO 2-letter or 3-letter language code
        stt_lang = agent_config.get("stt_language") or tts_language
        if stt_lang and "-" in stt_lang:
            stt_lang = stt_lang.split("-")[0]

        stt = ElevenLabsRealtimeSTTService(
            api_key=_stt_keys["elevenlabs"],
            settings=ElevenLabsRealtimeSTTService.Settings(
                language=stt_lang or None,
            )
        )
        stt_needs_language_switch = bool(stt_lang)
        stt_language_translator = lambda code: code.split("-")[0]
    elif stt_provider == "assemblyai":
        log.info("Instantiating AssemblyAI streaming STT...")
        stt = provider_registry.build_assemblyai_stt(api_key=_stt_keys["assemblyai"])
        # AssemblyAI's streaming v3 API auto-detects language — no per-language
        # reconnect needed mid-call.
        stt_needs_language_switch = False
    else:
        log.info("Instantiating Sarvam STT...")
        stt = SarvamSTTService(
            api_key=_stt_keys["sarvam"],
            settings=stt_settings,
        )
        # saaras:v2.5 was built with no language at all (it auto-detects); the
        # pinned models (saarika, saaras:v3) need to be told.
        stt_needs_language_switch = stt_model != "saaras:v2.5"
        stt_language_translator = _safe_lang

    # LLM — resilient provider selection (audit FIX 2). Probe the configured
    # provider first, fall back through healthy alternatives. This is what makes
    # a dead/leaked primary key (the Gemini key is currently revoked) non-fatal:
    # the whole call runs on the first reachable provider instead of going silent.
    # Probes run once here at setup — never in the per-turn hot loop.
    from backend.agent.resilience import select_llm_provider, build_llm, ResilienceProcessor

    llm_provider, llm_key, llm_model = await select_llm_provider(agent_config)
    log.info("Using LLM provider=%s model=%s for room=%s", llm_provider, llm_model, room_name)
    llm = await build_llm(llm_provider, llm_key, llm_model, system_prompt, agent_config)

    # Build LLM context (conversation history) + a PROVIDER-AGNOSTIC aggregator.
    # llm.create_context_aggregator(...) only exists on GoogleLLMService; since
    # the LLM is now chosen at runtime (Gemini/Groq/OpenAI — audit FIX 2), use
    # the universal LLMContextAggregatorPair, which drives any provider off the
    # same LLMContext.
    # NO system message here — build_llm() already passes `system_prompt` to the
    # service as `system_instruction`, and pipecat's OpenAI-family adapter runs
    # with discard_context_system=False, i.e. it KEEPS BOTH. Every request was
    # therefore carrying two full copies of the system prompt (KB + booking rules
    # + language rule); measured with a stand-in prompt: 7602 chars vs 3802 for a
    # two-message exchange. The worker logged the warning on every turn:
    #
    #   "Both system_instruction and an initial system message in context are
    #    set, which may be unintended. Keeping both..."
    #
    # Doubling the prompt doubles input tokens, LLM time-to-first-byte and the
    # JSON the free-tier worker has to serialise per turn. Nothing reads
    # messages[0] — BookingProcessor and the greeting only ever append — so the
    # instruction alone is the single source of truth. Gemini's adapter
    # (discard_context_system=True) prefers system_instruction as well, so this is
    # correct for every provider build_llm() can return.
    context = LLMContext(messages=[])
    # ── Turn-stop strategy — cheap timer, not a local transformer ───────────
    # pipecat 1.5 defaults the STOP strategy to TurnAnalyzerUserTurnStopStrategy,
    # which runs the Local Smart Turn v3 ONNX transformer over the utterance audio
    # to decide semantically whether the caller is done. It is a good default on a
    # real CPU. This worker runs on Render's FREE plan (0.1 CPU), where that
    # inference is a synchronous block inside the event loop — and the live logs
    # show what it costs: bursts of
    #
    #   "libwebrtc audio_stream queue overflow; dropped 400 queued frames"
    #
    # right after each utterance. Dropped inbound frames plus a stalled loop is
    # exactly the reported symptom set: TTS that stutters mid-sentence and
    # barge-in that "doesn't stop" (the InterruptionFrame cannot be processed
    # while the loop is blocked, so the caller keeps hearing buffered audio).
    #
    # SpeechTimeoutUserTurnStopStrategy replaces it with two plain timers: no
    # model load per job, no inference per utterance. End-of-turn becomes
    # stop_secs (0.2) + user_speech_timeout (0.6) ≈ 0.8s, predictable.
    #
    # Semantic turn detection is strictly better once there is CPU for it, so this
    # is a flag, not a deletion: set AGENT_SMART_TURN=true after moving off the
    # free plan to get it back.
    if settings.agent_smart_turn:
        log.info("Turn detection: Local Smart Turn v3 (semantic) — AGENT_SMART_TURN is on")
        user_params = LLMUserAggregatorParams()
    else:
        log.info("Turn detection: speech-timeout timers (cheap) — set AGENT_SMART_TURN=true to use Smart Turn v3")
        user_params = LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                # start=[...] intentionally omitted — the default
                # [VADUserTurnStartStrategy, TranscriptionUserTurnStartStrategy]
                # is what fires barge-in, and must stay.
                stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.6)],
            ),
        )

    context_aggregator = LLMContextAggregatorPair(context, user_params=user_params)

    # ── VAD — barge-in / interruption (pipecat 1.5.0 wiring) ────────────────
    # This is the piece that was silently missing. In 1.5.0 VAD is neither a
    # transport param nor implicit: it is a PROCESSOR. Placed FIRST, right after
    # transport.input(), it broadcasts VADUserStartedSpeakingFrame /
    # VADUserStoppedSpeakingFrame, which two different consumers need:
    #
    #   1. The user aggregator's default turn-start strategies are
    #      [VADUserTurnStartStrategy, TranscriptionUserTurnStartStrategy]. The VAD
    #      one fires the interruption on speech ONSET (~start_secs); without it
    #      only the transcription strategy ran, so barge-in waited for Deepgram's
    #      first interim transcript to make the network round-trip. Interruptions
    #      themselves are ON by default in 1.5.0
    #      (BaseUserTurnStartStrategy.enable_interruptions) — the analyzer was the
    #      only missing half.
    #   2. SegmentedSTTService subclasses (OpenAI Whisper) slice utterances off
    #      these exact frames. That is why VAD must sit UPSTREAM of `stt` and not
    #      on the aggregator: an aggregator-hosted analyzer is downstream of STT,
    #      so whisper would never see a boundary and never transcribe. Deepgram
    #      and Sarvam are streaming services and segment server-side.
    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(
            params=VADParams(
                # start_secs 0.2  → speech onset (and interruption) at ~200ms
                # confidence 0.55 → slightly more sensitive, fewer missed starts
                #
                # ⚠️ stop_secs MUST stay at pipecat 1.5's 0.2 default. It was set to
                # 0.6 here as a "latency optimisation" carried over from 0.x, where
                # stop_secs directly gated end-of-turn. In 1.5 the turn machinery
                # subtracts stop_secs from the STT p99 latency budget, and the
                # worker logged exactly what goes wrong:
                #
                #   "VAD stop_secs (0.6s) >= STT p99 latency (0.35s). STT wait
                #    timeout collapsed to 0s, which may cause delayed turn
                #    detection specified by the user_turn_stop_timeout parameter"
                #
                # With the budget collapsed to 0 the turn fell through to the
                # user_turn_stop_timeout fallback (5s default), which is precisely
                # the stall seen on live calls: VAD stopped 15:38:22.7, turn
                # inference only fired 15:38:27.5 — 4.8s of dead air per turn.
                # Raising this "for latency" costs latency.
                stop_secs=0.2,
                start_secs=0.2,
                confidence=0.55,
            )
        )
    )

    # TTS — Sarvam AI, ElevenLabs, OpenAI, or Cartesia
    tts_provider = agent_config.get("tts_provider", "sarvam")

    # Providers this function can actually BUILD, i.e. those with an `elif` branch
    # below — imported from the shared registry rather than redeclared here, so the
    # API's write-time validation and this run-time guard can never disagree.
    from backend.services.provider_registry import BUILDABLE_TTS as _BUILDABLE_TTS

    # Same DB-first (AI Platform dashboard), env-fallback key resolution as STT
    # above — see backend/agent/providers.py. Only the keys actually read below are
    # resolved (each is a DB round-trip on a latency-critical path).
    #
    # "sarvam" is ALWAYS resolved because the final `else:` is the Sarvam branch —
    # it is the fallback every unrecognised provider lands on, and it reads
    # _tts_keys["sarvam"]. Omitting it here raised KeyError: 'sarvam' inside
    # entrypoint(), which kills the job before the agent joins the room: the
    # caller hears dead air and the logs show no reason why.
    _tts_needed = {tts_provider, "sarvam"}
    if tts_provider == "openai_tts":
        _tts_needed.add("openai")
    async with AsyncSessionLocal() as _key_db:
        _tts_keys = {
            p: await provider_registry.resolve_key(_key_db, p, category="tts")
            for p in _tts_needed
        }

    # Mute-agent guard — the TTS mirror of the deaf-agent guard above. Say so
    # loudly when the configured provider cannot be built, instead of quietly
    # speaking in a different voice than the dashboard shows.
    if tts_provider not in _BUILDABLE_TTS:
        log.critical(
            "TTS provider '%s' is selected but this pipeline has no build branch for it "
            "(buildable: %s). Falling back to Sarvam TTS for room=%s — the caller will "
            "hear a DIFFERENT voice than the dashboard shows. Add a branch in "
            "pipeline.py + a builder in backend/agent/providers.py to support it.",
            tts_provider, sorted(_BUILDABLE_TTS), room_name,
        )
        tts_provider = "sarvam"
    if not (_tts_keys.get(tts_provider) or "").strip():
        log.critical(
            "TTS provider '%s' has no API key (DB or env). Synthesis will 401 and the "
            "caller will hear NOTHING at all for room=%s.", tts_provider, room_name,
        )

    if tts_provider == "elevenlabs":
        # Safe fallback: if tts_voice is empty or is a Sarvam voice name, default to ElevenLabs' Rachel ID
        selected_voice = tts_voice
        sarvam_voice_ids = {
            "priya", "ritu", "neha", "simran", "kavya", "ishita", "shreya", "tanya", "pooja", "roopa",
            "kavitha", "suhani", "shruti", "niharika", "rupali", "rahul", "aditya", "ashutosh", "rohan",
            "amit", "dev", "ratan", "varun", "manan", "sumit", "kabir", "aayan", "shubh", "advait",
            "anand", "tarun", "sunny", "mani", "gokul", "vijay", "mohit", "rehan", "soham", "meera", "bulbul"
        }
        if not selected_voice or selected_voice.lower() in sarvam_voice_ids:
            selected_voice = "21m00Tcm4TlvDq8ikWAM"  # Rachel (Premium Female English)

        tts_model_configured = agent_config.get("tts_model", "eleven_flash_v2_5")
        if tts_model_configured not in ("eleven_flash_v2_5", "eleven_multilingual_v2", "eleven_turbo_v2_5"):
            tts_model_configured = "eleven_flash_v2_5"

        # Voice-character sliders (Stability / Clarity / Style / Speaker Boost /
        # Speed) — mapped 1:1 to ElevenLabsTTSSettings, which is the class this
        # websocket-based service actually exposes (confirmed against the
        # installed pipecat-ai package). `speed` is clamped to ElevenLabs'
        # accepted 0.7–1.2 range since the agent's slider goes 0.5–2.0.
        el_speed = agent_config.get("tts_speed")
        el_speed = min(max(float(el_speed), 0.7), 1.2) if el_speed is not None else None

        log.info("Instantiating ElevenLabs TTS for voice: %s, model: %s", selected_voice, tts_model_configured)
        tts = ElevenLabsTTSService(
            api_key=_tts_keys["elevenlabs"],
            voice_id=selected_voice,
            settings=ElevenLabsTTSService.Settings(
                model=tts_model_configured,
                stability=agent_config.get("tts_stability"),
                similarity_boost=agent_config.get("tts_clarity"),
                style=agent_config.get("tts_style"),
                use_speaker_boost=agent_config.get("tts_use_speaker_boost"),
                speed=el_speed,
            )
        )
    elif tts_provider == "openai_tts":
        log.info("Instantiating OpenAI TTS for voice: %s, model: %s", tts_voice, tts_model_str)
        openai_speed = agent_config.get("tts_speed")
        openai_speed = min(max(float(openai_speed), 0.25), 4.0) if openai_speed is not None else None
        tts = OpenAITTSService(
            api_key=_tts_keys.get("openai_tts") or _tts_keys["openai"],
            settings=OpenAITTSService.Settings(
                voice=tts_voice or "alloy",
                model=tts_model_str if tts_model_str.startswith("gpt-") or tts_model_str.startswith("tts-") else "gpt-4o-mini-tts",
                speed=openai_speed,
            ),
        )
    elif tts_provider == "cartesia":
        # tts_model_str was validated against Sarvam's own model enum above
        # (line ~607) and forced to a Sarvam value if it didn't match — read
        # the raw configured model directly here instead, same as the
        # ElevenLabs branch already does for its own model list.
        cartesia_model = agent_config.get("tts_model") or "sonic-2"
        log.info("Instantiating Cartesia TTS for voice: %s, model: %s", tts_voice, cartesia_model)
        tts = provider_registry.build_cartesia_tts(
            api_key=_tts_keys["cartesia"],
            voice_id=tts_voice or None,
            model=cartesia_model,
        )
    else:
        log.info("Instantiating Sarvam TTS...")
        # NOTE: SarvamTTSService.__init__ only accepts `api_key`/`model`/
        # `voice_id` as direct kwargs in the installed pipecat-ai release —
        # voice/language/pace/pitch/loudness/enable_preprocessing must go
        # through `settings=`, or they're silently swallowed by **kwargs and
        # never reach Sarvam at all (confirmed against pipecat-ai 1.5.0;
        # requirements.agent.txt pins no upper bound so this is what a fresh
        # deploy installs).
        tts = SarvamTTSService(
            api_key=_tts_keys["sarvam"],
            settings=SarvamTTSService.Settings(
                voice=tts_voice,
                model=tts_model_str,
                language=tts_language,
                pace=tts_pace,
                pitch=tts_pitch,
                loudness=tts_loudness,
                enable_preprocessing=tts_input_preprocessing,
                # ── Time-to-first-audio ────────────────────────────────────
                # These are sent to Sarvam in the websocket config frame and
                # decide when it STARTS synthesizing. pipecat's defaults are
                # min_buffer_size=50 / max_chunk_length=150 characters, which are
                # tuned for long-form narration and are wrong for a phone call:
                # this agent is instructed to answer in at most 2 sentences and
                # runs with max_response_tokens=120, so a typical reply is well
                # under 50 characters. Sarvam therefore buffered the ENTIRE reply
                # without emitting a sample, and audio only began after pipecat
                # flushed on LLMFullResponseEndFrame — i.e. the caller waited for
                # the whole LLM turn to finish before hearing the first word.
                #
                # 15/60 lets a short reply start synthesizing almost immediately
                # while still batching enough text for natural prosody. Costs
                # nothing but marginally more chunking on long replies.
                min_buffer_size=15,
                max_chunk_length=60,
            ),
        )

    # Custom processors — booking state machine + call logging
    booking_processor = BookingProcessor(
        tenant=tenant,
        agent_config=agent_config,
        call_meta=call_meta,
    )
    call_logger = CallLoggerProcessor(
        tenant_id=tenant_id or "",
        agent_id=agent_id,
        call_meta=call_meta,
        agent_config=agent_config,
    )
    # Feeds user utterances to call_logger from a position where
    # TranscriptionFrames still exist. Without it the logger counts zero turns
    # and stores an empty transcript, because context_aggregator.user() swallows
    # those frames before they can reach it (see UserTranscriptTap's docstring).
    user_transcript_tap = UserTranscriptTap(call_logger)

    # Never-silence guard (audit FIX 2): sits at the tail of the pipeline and,
    # on any LLM/TTS ErrorFrame, speaks a short reassurance phrase in the agent's
    # language instead of leaving dead air. Task is bound after PipelineTask
    # construction below.
    resilience = ResilienceProcessor(language=tts_language)

    # Mid-call language switching: watches the caller's final transcripts and
    # retunes TTS (and STT, when the model is language-pinned) the moment they
    # change language. Sits between `stt` and the user aggregator for the same
    # reason user_transcript_publisher does — the aggregator eats
    # TranscriptionFrames without forwarding them.
    language_switcher = LanguageSwitchProcessor(
        tts=tts,
        stt=stt,
        initial_language=tts_language,
        stt_language_map=stt_language_translator,
        switch_stt=stt_needs_language_switch,
        # One clear turn is enough — a caller who switches expects to be answered
        # in the new language on the very next reply, not two turns later.
        confirm_turns=1,
        on_switch=resilience.set_language,
    )

    # ── Build the Pipeline ─────────────────────────────────────────────────
    # Data flows left to right through each processor:
    #
    #   audio in → STT → context_in → booking → LLM → context_out → logger → TTS → audio out
    #
    # Mirrors the agent's spoken text into the room as transcriptions so the
    # browser Test widget shows a live transcript (transparent passthrough,
    # fully guarded — see LiveKitTranscriptPublisher). Placed right after TTS so
    # it sees TTSTextFrames (the text actually being spoken).
    # TWO publisher instances, one per side of the conversation. They cannot be a
    # single processor because the two frame types they mirror only exist at
    # opposite ends of the pipeline:
    #
    #   • user_transcript_publisher MUST sit between `stt` and
    #     context_aggregator.user(). The user aggregator CONSUMES both
    #     TranscriptionFrame and InterimTranscriptionFrame without pushing them
    #     downstream (pipecat 1.5.0, llm_response_universal.py:794-799 — final
    #     transcriptions go to _handle_transcription() with no push_frame, and the
    #     interim branch is a bare `pass` whose own comment says "not pushed
    #     downstream, same as final TranscriptionFrame"). While the publisher sat
    #     AFTER the aggregator it received zero user transcriptions for the whole
    #     call, so the browser never got a DataReceived event and the caller saw
    #     nothing at all while speaking. STT was fine; only the mirror was dead.
    #     This is the same swallowing bug as context_aggregator.assistant() below,
    #     at the other end of the pipeline.
    #
    #   • agent_transcript_publisher MUST sit after `tts`, because TTSTextFrame is
    #     pushed downstream BY the TTS service — nothing upstream of it can see one.
    user_transcript_publisher = LiveKitTranscriptPublisher(transport)
    agent_transcript_publisher = LiveKitTranscriptPublisher(transport)

    pipeline = Pipeline([
        transport.input(),                       # Audio in from LiveKit room
        vad,                                     # Silero VAD → speech start/stop (barge-in + segmentation)
        stt,                                     # Speech → Transcription/InterimTranscriptionFrame
        language_switcher,                       # Caller changed language? retune STT/TTS (transparent)
        user_transcript_tap,                     # Feed user turns to call_logger (transparent)
        user_transcript_publisher,               # Mirror USER text → room data channel (transparent)
        context_aggregator.user(),               # Accumulates user turns into LLMContext
        booking_processor,                       # Booking state machine (transparent)
        llm,                                     # LLMContext → LLMResponseFrame (streaming)
        tts,                                     # LLMResponseFrame → TTSAudioRawFrame
        call_logger,                             # Metrics + call record updates (transparent)
        agent_transcript_publisher,              # Mirror AGENT text → room transcript (transparent)
        resilience,                              # Never-silence: ErrorFrame → spoken fallback
        transport.output(),                      # Audio out to LiveKit room
        context_aggregator.assistant(),          # Stores assistant reply in context — MUST BE LAST
    ])
    # ⚠️ context_aggregator.assistant() MUST be the LAST processor — never between
    # the LLM and TTS. LLMAssistantAggregator.process_frame() CONSUMES
    # LLMFullResponseStartFrame / LLMFullResponseEndFrame / TextFrame without
    # pushing them downstream (it buffers them to build the assistant context
    # message). Sitting it directly after the LLM therefore swallowed 100% of the
    # LLM's output text before it could reach `tts` — so the agent joined the
    # room, published an audio track, and then never spoke a single word on any
    # turn, including the greeting. That was the sole cause of the "room is
    # created but there's no conversation" bug. Verified against
    # pipecat-ai 1.5.0 (processors/aggregators/llm_response_universal.py:1493-1498
    # and _handle_text at :1935 — all return without push_frame).
    #
    # call_logger also moved to AFTER `tts`: it reacts to TTSStartedFrame (resets
    # the silence-timeout idle clock when the AGENT speaks) and to the TTS
    # MetricsFrame (TTFB latency). Both are pushed DOWNSTREAM by `tts`, so while
    # call_logger sat upstream of it neither ever arrived — the idle-clock reset
    # added in c594ea6 was dead code and TTS latency was never recorded.
    # TranscriptionFrame / End / Cancel still reach it: they propagate downstream
    # from stt and the task source respectively.

    # ── Build & run the task ───────────────────────────────────────────────
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            # NOTE: allow_interruptions used to be passed here. PipelineParams has
            # no such field in pipecat 1.5.0 and pydantic drops unknown kwargs, so
            # it was a no-op. Interruptions are ON by default in 1.5.0 — the knob
            # that actually matters is the VAD analyzer on the user aggregator
            # above (BaseUserTurnStartStrategy.enable_interruptions defaults True).
            enable_metrics=True,          # Enables MetricsFrame for latency tracking
            enable_usage_metrics=True,    # Enables token usage tracking
        ),
    )

    # ── Event handlers ─────────────────────────────────────────────────────

    first_message_mode = agent_config.get("first_message_mode", "assistant-speaks-first")
    recording_consent_plan = agent_config.get("recording_consent_plan", "none") or "none"
    _CONSENT_NOTICE = "This call may be recorded for quality and training purposes."

    # ── Recording is NOT implemented (audit FIX 5, Option B) ─────────────────
    # Call audio is never captured and recording_url is never written. Asking a
    # caller to consent to a recording that does not exist is a trust/legal
    # problem, so the consent prompt is force-disabled at runtime regardless of
    # the stored recording_consent_plan. The admin field is left intact; when
    # real recording (LiveKit Egress → Supabase recordings/) is built in a later
    # batch, flip RECORDING_IMPLEMENTED to True and this suppression lifts itself.
    RECORDING_IMPLEMENTED = False
    if not RECORDING_IMPLEMENTED and recording_consent_plan != "none":
        log.warning(
            "Recording is not implemented — ignoring recording_consent_plan=%s and NOT asking "
            "the caller to consent to a recording that will not happen (audit FIX 5, Option B).",
            recording_consent_plan,
        )
        recording_consent_plan = "none"

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport_ref, participant_id: str) -> None:
        """
        Greet the caller and start the silence watchdog.

        IMPORTANT: silence watchdog starts HERE (not at room-connect) so the
        agent greeting time (~4-5s) is not counted toward the silence timeout.
        Previously last_activity_ts was set at __init__ and the 10s clock started
        at call-connect — the greeting ate ~5s, leaving callers only ~5s to speak
        before the call was axed.
        """
        # Reset activity clock to NOW (participant just joined, greeting about to play)
        call_logger.last_activity_ts = time.time()

        # Start the silence watchdog NOW — after participant joins — so greeting
        # time is excluded from the idle clock.
        if not _silence_watchdog_task:  # only start once
            _silence_watchdog_task.append(
                asyncio.create_task(
                    _enforce_silence_timeout(task, call_logger, silence_timeout_seconds, end_call_message)
                )
            )
            watchdog_tasks.append(_silence_watchdog_task[0])

        if recording_consent_plan == "require":
            log.info("Participant joined: %s — asking for recording consent before proceeding.", participant_id)
            call_logger.begin_consent_gate(
                decline_message=(
                    "No problem — this call will not be recorded. Unfortunately I can't "
                    "continue without your consent, so I'll have to end the call here. Goodbye."
                ),
                resume_message=first_message if first_message_mode != "wait" else None,
            )
            await task.queue_frames([
                TTSSpeakFrame(f"{_CONSENT_NOTICE} Is that okay with you?", append_to_context=False)
            ])
            return

        effective_first_message = first_message
        if recording_consent_plan == "inform":
            effective_first_message = f"{_CONSENT_NOTICE} {first_message}"

        if first_message_mode == "wait":
            log.info("Participant joined: %s — mode=wait, staying silent until caller speaks.", participant_id)
            context.add_message({"role": "assistant", "content": effective_first_message})
            return
        log.info("Participant joined: %s — speaking first message.", participant_id)
        # TTSSpeakFrame (not TextFrame): TTSService handles TTSSpeakFrame as a
        # standalone utterance and synthesizes it immediately. A bare TextFrame
        # queued at the task source is only ever flushed as part of an LLM
        # response turn, so the greeting never got spoken.
        # append_to_context=False because we add it to the context ourselves above.
        context.add_message({"role": "assistant", "content": effective_first_message})
        await task.queue_frames([
            TTSSpeakFrame(effective_first_message, append_to_context=False)
        ])

    @transport.event_handler("on_participant_disconnected")
    async def on_participant_disconnected(transport_ref, participant_id: str) -> None:
        """End the pipeline when the caller hangs up and delete the room instantly."""
        log.info("Participant disconnected: %s — ending pipeline and deleting room %s.", participant_id, room_name)
        await task.cancel()
        try:
            async with livekit_api.LiveKitAPI(settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret) as lk:
                await lk.room.delete_room(livekit_api.DeleteRoomRequest(room=room_name))
                log.info("Deleted LiveKit room %s on disconnect ✓", room_name)
        except Exception as _del_err:
            log.warning("Could not delete room %s on disconnect (non-fatal): %s", room_name, _del_err)

    # ── Call-length watchdogs (Call Behavior tab) ───────────────────────────
    end_call_message = agent_config.get("end_call_message") or "Thank you for calling. Goodbye!"
    max_duration_seconds = int(agent_config.get("max_duration_seconds", 300) or 300)
    # FIX: default 30s (was 10s) and clamp to ≥20s so the greeting (4-5s) plus
    # a reasonable pause never trips the timeout on a normal call.
    silence_timeout_seconds = max(int(agent_config.get("silence_timeout_seconds", 30) or 30), 20)

    # Give the logger a way to end the call directly (used for closing-intent
    # detection — see CallLoggerProcessor._on_user_speech).
    call_logger.task = task

    # …and a way to know when audio has genuinely finished playing, so the silence
    # timer restarts and a goodbye hangs up only after the caller has heard the
    # whole sentence.
    call_logger.set_playout_drain(_make_playout_drain(transport))

    # Let the never-silence guard inject a spoken phrase via the source on error.
    resilience.bind_task(task)

    # Silence watchdog is started AFTER the first participant joins (see
    # on_first_participant_joined below) so the greeting time is not counted.
    # max_duration watchdog starts immediately (measures total call wall-time).
    _silence_watchdog_task: list = []  # mutable container so the closure can populate it

    watchdog_tasks = [
        asyncio.create_task(
            _enforce_max_duration(task, max_duration_seconds, end_call_message, call_logger)
        ),
        asyncio.create_task(_monitor_event_loop_lag(room_name)),
    ]

    # ── Run ────────────────────────────────────────────────────────────────
    # handle_sigint=False: the livekit-agents worker runs each job in its own
    # subprocess/thread and owns process lifecycle + signal handling. Letting
    # PipelineRunner install its own SIGINT handler crashes with
    # "signal only works in main thread" (and is unnecessary — the worker
    # already handles graceful shutdown).
    runner = PipelineRunner(handle_sigint=False)
    try:
        await runner.run(task)
    finally:
        for t in watchdog_tasks:
            if not t.done():
                t.cancel()
        # Keep the job process alive until the CallRecord is finalized. The
        # end/cancel frame schedules finalization as a task; without this await
        # the process could exit before duration/transcript/latency persist
        # (audit FIX 3 — call_records must finalize on real hangups).
        try:
            ok = await call_logger.wait_finalized(timeout=10.0)
            log.info("Finalization %s for room=%s", "completed" if ok else "TIMED OUT", room_name)
        except Exception as exc:
            log.error("Error awaiting finalization for room=%s: %s", room_name, exc)

    log.info("Pipeline finished for room=%s", room_name)


def _make_playout_drain(transport, timeout: float = 6.0):
    """Build an awaitable that waits for LiveKit to finish PLAYING queued audio.

    pipecat's BotStoppedSpeakingFrame fires when the output transport has written
    the last audio chunk, but LiveKit's rtc.AudioSource is constructed with a
    1000ms queue and capture_frame() self-paces against it — so up to a second of
    speech can still be unplayed at that moment. Hanging up or restarting the
    silence timer there clips the tail of the agent's last sentence (measured on a
    live call: a ~2s goodbye reported "stopped speaking" 1.26s in).

    rtc.AudioSource.wait_for_playout() is the real end-of-audio signal. Reaching
    it means touching pipecat's private transport internals, which is why every
    step is guarded and a failure simply means "don't wait" — the same
    best-effort approach LiveKitTranscriptPublisher takes to resolve the room.
    """
    async def drain() -> None:
        try:
            output = transport.output()
            source = getattr(getattr(output, "_client", None), "_audio_source", None)
            if source is None or not hasattr(source, "wait_for_playout"):
                return
            queued = getattr(source, "queued_duration", 0) or 0
            if queued <= 0:
                return
            await asyncio.wait_for(source.wait_for_playout(), timeout=timeout)
        except asyncio.TimeoutError:
            log.warning("LiveKit playout drain exceeded %.0fs — continuing.", timeout)
        except Exception as exc:
            log.debug("LiveKit playout drain unavailable (non-critical): %s", exc)

    return drain


async def _monitor_event_loop_lag(
    room_name: str,
    sample_interval: float = 0.5,
    warn_threshold: float = 0.15,
) -> None:
    """Log whenever the event loop is starved, with the size of the stall.

    Stuttering TTS on this stack has exactly one mechanism: LiveKit's AudioSource
    is created with a 1000ms queue and its capture_frame() self-paces, so pipecat
    keeps roughly a second of audio buffered ahead. The caller therefore only
    hears a gap when the event loop is blocked long enough to drain that — i.e.
    hundreds of milliseconds to a second, not the ~40ms of a chunk boundary.

    That makes "is the loop stalling, and for how long" the one measurement worth
    having, and it is not something a code read can answer. This sampler sleeps
    for a known interval and reports the overshoot, so a real call leaves evidence
    in the logs instead of an argument. Cost is two wakeups a second and a
    subtraction; it only logs when it has something to report.
    """
    try:
        worst = 0.0
        while True:
            before = time.monotonic()
            await asyncio.sleep(sample_interval)
            lag = time.monotonic() - before - sample_interval
            if lag >= warn_threshold:
                worst = max(worst, lag)
                log.warning(
                    "Event loop stalled %.0fms (worst %.0fms this call) — audio "
                    "output can gap when this approaches 1000ms | room=%s",
                    lag * 1000, worst * 1000, room_name,
                )
    except asyncio.CancelledError:
        if worst:
            log.info("Worst event-loop stall this call: %.0fms | room=%s", worst * 1000, room_name)


async def _enforce_max_duration(
    task: "PipelineTask",
    max_duration_seconds: int,
    end_call_message: str,
    call_logger: "CallLoggerProcessor | None" = None,
) -> None:
    """Ends the call once it has run longer than the agent's configured ceiling."""
    from backend.agent.processors.call_logger_processor import speak_and_end_call

    try:
        await asyncio.sleep(max_duration_seconds)
        log.info("Max call duration (%ss) reached — ending call.", max_duration_seconds)
        await speak_and_end_call(
            task,
            end_call_message,
            wait_playback=call_logger.wait_playback_complete if call_logger else None,
        )
    except asyncio.CancelledError:
        pass


async def _enforce_silence_timeout(
    task: "PipelineTask",
    call_logger: "CallLoggerProcessor",
    silence_timeout_seconds: int,
    end_call_message: str,
) -> None:
    """Ends the call if the CALLER goes silent for longer than configured.

    "Silent" means the caller is not speaking AND the agent is not speaking to
    them. The timer previously ran continuously and was only reset when TTS
    STARTED, so the whole playback of an answer counted as caller silence: a
    multi-sentence reply (doctor names, dates, slots) could exceed the timeout and
    hang up on a caller who was simply listening, mid-sentence.

    Two things fix that, both keyed on real playback rather than an estimate:
      * while call_logger.bot_speaking is True the timer does not advance at all;
      * BotStoppedSpeakingFrame — pushed from the output transport's audio task
        after the audio has drained — resets last_activity_ts, so the countdown
        starts from the moment the caller could actually begin replying.
    """
    from backend.agent.processors.call_logger_processor import speak_and_end_call

    try:
        while True:
            await asyncio.sleep(2.0)

            # The agent is talking: the caller is listening, not silent. Keep the
            # clock pinned to now so no silence accrues during playback.
            if call_logger.bot_speaking:
                call_logger.last_activity_ts = time.time()
                continue

            idle_seconds = time.time() - call_logger.last_activity_ts
            if idle_seconds >= silence_timeout_seconds:
                log.info(
                    "Silence timeout (%ss) reached with no caller speech — ending call.",
                    silence_timeout_seconds,
                )
                await speak_and_end_call(
                    task, end_call_message, wait_playback=call_logger.wait_playback_complete
                )
                return
    except asyncio.CancelledError:
        pass


# ── Worker bootstrap ──────────────────────────────────────────────────────────

# Single source of truth for the dispatch name — MUST equal
# backend/routers/web_calls.py::AGENT_NAME or dispatched calls connect but no
# agent ever joins (audit FIX 1.2).
AGENT_NAME = "lifodial-inbound-agent"

_PLACEHOLDER_LK_URL = "wss://your-project.livekit.cloud"


def prewarm(proc) -> None:
    """
    Pre-warm Silero VAD model before the first call.
    Called once when the worker process starts.
    """
    # Pipecat's SileroVADAnalyzer loads the model lazily on first call.
    # Pre-warming is handled internally by Pipecat — nothing to do here.
    log.info("Agent worker pre-warmed.")


def _preflight_or_die() -> None:
    """Fail LOUDLY before the worker starts if it can't possibly register with
    LiveKit (audit FIX 1.4 — never start silently and never pick up calls).

    A missing/placeholder LiveKit URL/key/secret is a fatal misconfiguration:
    the worker would otherwise appear to boot but never register, so every
    dispatched call would connect to a room no agent ever joins.
    """
    missing = [
        name for name, val in (
            ("LIVEKIT_URL", settings.livekit_url),
            ("LIVEKIT_API_KEY", settings.livekit_api_key),
            ("LIVEKIT_API_SECRET", settings.livekit_api_secret),
        )
        if not (val or "").strip()
    ]
    placeholder = settings.livekit_url.strip() == _PLACEHOLDER_LK_URL
    if missing or placeholder:
        reason = (
            f"placeholder LIVEKIT_URL ({_PLACEHOLDER_LK_URL})" if placeholder
            else f"missing {', '.join(missing)}"
        )
        log.critical(
            "FATAL: agent worker cannot register with LiveKit — %s. Refusing to start "
            "(a silently-started worker would never pick up any call). Set the LiveKit "
            "credentials and restart.", reason,
        )
        raise SystemExit(1)
    log.info("Preflight OK — LiveKit creds present; registering worker as agent_name=%s", AGENT_NAME)


if __name__ == "__main__":
    import os as _os
    from livekit.agents import WorkerOptions, JobExecutorType, cli

    _preflight_or_die()
    _port = int(_os.environ.get("PORT") or 8081)
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name=AGENT_NAME,
            ws_url=settings.livekit_url or None,
            api_key=settings.livekit_api_key or None,
            api_secret=settings.livekit_api_secret or None,
            host="0.0.0.0",
            port=_port,
            job_executor_type=JobExecutorType.THREAD,
            initialize_process_timeout=60.0,
            num_idle_processes=0,
            load_threshold=float("inf"),
        )
    )
