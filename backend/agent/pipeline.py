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
from pipecat.frames.frames import TextFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSModel, SarvamTTSService
from pipecat.services.openai.stt import OpenAISTTService
from pipecat.services.openai.tts import OpenAITTSService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport

# ── Pipecat LiveKit token helper ──────────────────────────────────────────────
from livekit import api as livekit_api

# ── Local processors ──────────────────────────────────────────────────────────
from backend.agent.processors.booking_processor import BookingProcessor
from backend.agent.processors.call_logger_processor import CallLoggerProcessor

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


# Appended to EVERY system prompt (custom, template, or fallback). This is the
# honesty contract that pairs with BookingProcessor._commit_and_inject_result:
# the DB write is awaited and its outcome arrives as a [BOOKING_RESULT] system
# message BEFORE the LLM generates — so the model must never claim success on
# its own (audit FIX 4).
_BOOKING_RULES_BLOCK = (
    "\n\n--- APPOINTMENT BOOKING RULES (STRICT) ---\n"
    "1. When the caller wants an appointment, ask which doctor and what day/time "
    "they want. Never invent or assume a time yourself.\n"
    "2. Once they give a time, repeat the doctor + time back and ask them to confirm.\n"
    "3. NEVER say an appointment is booked, confirmed, or scheduled unless a system "
    "message starting with [BOOKING_RESULT success=true] appears. Until then, say it "
    "is not yet confirmed.\n"
    "4. If a [BOOKING_RESULT success=false] message appears, apologize, say the "
    "booking could not be saved, and offer to try again.\n"
    "--- END BOOKING RULES ---"
)


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
    kb_block = _kb_context_block(tenant) + _BOOKING_RULES_BLOCK

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
            for d in doctors
        ) or "- General Physician available"

        rendered = render_prompt(
            tmpl["system_prompt"],
            {
                "clinic_name": tenant.get("clinic_name", "the clinic"),
                "agent_name": agent_config.get("agent_name", "Receptionist"),
                "clinic_location": tenant.get("location", "India"),
                "working_hours": tenant.get("working_hours", "9 AM – 7 PM, Mon–Sat"),
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
    Generate a LiveKit access token for the agent to join a room.

    The agent joins as 'lifodial-agent' with full publish permissions.
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
                    tenant["working_hours"] = getattr(t, "working_hours", "9 AM – 7 PM, Mon–Sat")

                d_result = await db.execute(
                    select(Doctor).where(Doctor.tenant_id == tenant_id)
                )
                tenant["doctors"] = [
                    {
                        "id":             str(d.id),
                        "name":           d.name,
                        "specialization": d.specialization,
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
    metadata: dict = {}
    _raw_meta = ""
    try:
        _raw_meta = (getattr(ctx.room, "metadata", "") or "").strip()
        if not _raw_meta:
            _raw_meta = (getattr(getattr(ctx, "job", None), "metadata", "") or "").strip()
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

    # ── Create call record ─────────────────────────────────────────────────
    call_meta = {
        "caller_phone": caller_phone,
        "call_type":    "inbound",
        "room_name":    room_name,
    }
    call_record_id = await _create_call_record(tenant_id, agent_id, call_meta)
    call_meta["call_record_id"] = call_record_id

    # ── Connect to LiveKit room ────────────────────────────────────────────
    await ctx.connect()

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

    # Transport — connects Pipecat to the LiveKit room
    transport = LiveKitTransport(
        url=settings.livekit_url,
        token=agent_token,
        room_name=room_name,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(
                    stop_secs=0.85,     # 850ms silence before turn ends (allows natural mid-turn pauses)
                    start_secs=0.25,    # 250ms continuous speech to capture fast voice input
                    confidence=0.6,     # 60% confidence threshold (balanced for clear speech vs clicks)
                )
            ),
        ),
    )

    # STT — Sarvam AI, OpenAI Whisper, or ElevenLabs Realtime
    stt_provider = agent_config.get("stt_provider", "sarvam")
    if stt_provider in ("openai", "whisper"):
        log.info("Instantiating OpenAI Whisper STT...")
        stt = OpenAISTTService(
            api_key=settings.openai_api_key,
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
            api_key=settings.elevenlabs_api_key,
            settings=ElevenLabsRealtimeSTTService.Settings(
                language=stt_lang or None,
            )
        )
    else:
        log.info("Instantiating Sarvam STT...")
        stt = SarvamSTTService(
            api_key=settings.sarvam_api_key,
            settings=stt_settings,
        )

    # LLM — resilient provider selection (audit FIX 2). Probe the configured
    # provider first, fall back through healthy alternatives. This is what makes
    # a dead/leaked primary key (the Gemini key is currently revoked) non-fatal:
    # the whole call runs on the first reachable provider instead of going silent.
    # Probes run once here at setup — never in the per-turn hot loop.
    from backend.agent.resilience import select_llm_provider, build_llm, ResilienceProcessor

    llm_provider, llm_key, llm_model = await select_llm_provider(agent_config)
    log.info("Using LLM provider=%s model=%s for room=%s", llm_provider, llm_model, room_name)
    llm = build_llm(llm_provider, llm_key, llm_model, system_prompt, agent_config)

    # Build LLM context (conversation history) + a PROVIDER-AGNOSTIC aggregator.
    # llm.create_context_aggregator(...) only exists on GoogleLLMService; since
    # the LLM is now chosen at runtime (Gemini/Groq/OpenAI — audit FIX 2), use
    # the universal LLMContextAggregatorPair, which drives any provider off the
    # same LLMContext.
    context = LLMContext(
        messages=[
            {"role": "system", "content": system_prompt},
        ]
    )
    context_aggregator = LLMContextAggregatorPair(context)

    # TTS — Sarvam AI or ElevenLabs
    tts_provider = agent_config.get("tts_provider", "sarvam")
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
            api_key=settings.elevenlabs_api_key,
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
            api_key=settings.openai_api_key,
            settings=OpenAITTSService.Settings(
                voice=tts_voice or "alloy",
                model=tts_model_str if tts_model_str.startswith("gpt-") or tts_model_str.startswith("tts-") else "gpt-4o-mini-tts",
                speed=openai_speed,
            ),
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
            api_key=settings.sarvam_api_key,
            settings=SarvamTTSService.Settings(
                voice=tts_voice,
                model=tts_model_str,
                language=tts_language,
                pace=tts_pace,
                pitch=tts_pitch,
                loudness=tts_loudness,
                enable_preprocessing=tts_input_preprocessing,
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

    # Never-silence guard (audit FIX 2): sits at the tail of the pipeline and,
    # on any LLM/TTS ErrorFrame, speaks a short reassurance phrase in the agent's
    # language instead of leaving dead air. Task is bound after PipelineTask
    # construction below.
    resilience = ResilienceProcessor(language=tts_language)

    # ── Build the Pipeline ─────────────────────────────────────────────────
    # Data flows left to right through each processor:
    #
    #   audio in → STT → context_in → booking → LLM → context_out → logger → TTS → audio out
    #
    pipeline = Pipeline([
        transport.input(),                       # Audio in from LiveKit room
        stt,                                     # Speech → TranscriptionFrame
        context_aggregator.user(),               # Accumulates user turns into LLMContext
        booking_processor,                       # Booking state machine (transparent)
        llm,                                     # LLMContext → LLMResponseFrame (streaming)
        context_aggregator.assistant(),          # Stores assistant reply in context
        call_logger,                             # Metrics + call record updates (transparent)
        tts,                                     # LLMResponseFrame → TTSAudioRawFrame
        resilience,                              # Never-silence: ErrorFrame → spoken fallback
        transport.output(),                      # Audio out to LiveKit room
    ])

    # ── Build & run the task ───────────────────────────────────────────────
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,     # Barge-in: patient can interrupt agent speech
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
        Greet the caller — but only if the agent is set to speak first.
        With first_message_mode='wait', the agent stays silent and waits for the
        caller to speak (matching the browser-test path behavior). Previously the
        greeting always played regardless of this setting.

        recording_consent_plan handling (Advanced tab — previously stored but
        never acted on):
          - "none": no change to greeting behavior.
          - "inform": consent notice is prepended to the first message.
          - "require": consent notice is asked as a yes/no question BEFORE
            anything else; the caller's answer is intercepted by
            CallLoggerProcessor (see begin_consent_gate) rather than treated
            as normal conversation. A "no" ends the call politely.
        """
        if recording_consent_plan == "require":
            log.info("Participant joined: %s — asking for recording consent before proceeding.", participant_id)
            call_logger.begin_consent_gate(
                decline_message=(
                    "No problem — this call will not be recorded. Unfortunately I can't "
                    "continue without your consent, so I'll have to end the call here. Goodbye."
                ),
                resume_message=first_message if first_message_mode != "wait" else None,
            )
            await task.queue_frames([TextFrame(f"{_CONSENT_NOTICE} Is that okay with you?")])
            return

        effective_first_message = first_message
        if recording_consent_plan == "inform":
            effective_first_message = f"{_CONSENT_NOTICE} {first_message}"

        if first_message_mode == "wait":
            log.info("Participant joined: %s — mode=wait, staying silent until caller speaks.", participant_id)
            # Seed the greeting into context so the LLM knows its intended opener,
            # but do NOT synthesize/speak it.
            context.add_message({"role": "assistant", "content": effective_first_message})
            return
        log.info("Participant joined: %s — speaking first message.", participant_id)
        context.add_message({"role": "assistant", "content": effective_first_message})
        # Queue the first greeting as a TextFrame to be synthesized directly by TTS (no self-talk trigger)
        await task.queue_frames([TextFrame(effective_first_message)])

    @transport.event_handler("on_participant_disconnected")
    async def on_participant_disconnected(transport_ref, participant_id: str) -> None:
        """End the pipeline when the caller hangs up."""
        log.info("Participant disconnected: %s — ending pipeline.", participant_id)
        await task.cancel()

    # ── Call-length watchdogs (Call Behavior tab) ───────────────────────────
    # Both settings previously round-tripped to the DB with no runtime effect —
    # max_duration_seconds had no enforcement timer, and silence_timeout_seconds
    # was never compared against actual call activity.
    end_call_message = agent_config.get("end_call_message") or "Thank you for calling. Goodbye!"
    max_duration_seconds = int(agent_config.get("max_duration_seconds", 300) or 300)
    silence_timeout_seconds = int(agent_config.get("silence_timeout_seconds", 10) or 10)

    # Give the logger a way to end the call directly (used for end_call_phrases
    # detection — see CallLoggerProcessor._on_user_speech).
    call_logger.task = task

    # Let the never-silence guard inject a spoken phrase via the source on error.
    resilience.bind_task(task)

    watchdog_tasks = [
        asyncio.create_task(_enforce_max_duration(task, max_duration_seconds, end_call_message)),
        asyncio.create_task(_enforce_silence_timeout(task, call_logger, silence_timeout_seconds, end_call_message)),
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


async def _enforce_max_duration(task: "PipelineTask", max_duration_seconds: int, end_call_message: str) -> None:
    """Ends the call once it has run longer than the agent's configured ceiling."""
    from backend.agent.processors.call_logger_processor import speak_and_end_call

    try:
        await asyncio.sleep(max_duration_seconds)
        log.info("Max call duration (%ss) reached — ending call.", max_duration_seconds)
        await speak_and_end_call(task, end_call_message)
    except asyncio.CancelledError:
        pass


async def _enforce_silence_timeout(
    task: "PipelineTask",
    call_logger: "CallLoggerProcessor",
    silence_timeout_seconds: int,
    end_call_message: str,
) -> None:
    """Ends the call if the patient goes silent for longer than configured."""
    from backend.agent.processors.call_logger_processor import speak_and_end_call

    try:
        while True:
            await asyncio.sleep(2.0)
            idle_seconds = time.time() - call_logger.last_activity_ts
            if idle_seconds >= silence_timeout_seconds:
                log.info("Silence timeout (%ss) reached — ending call.", silence_timeout_seconds)
                await speak_and_end_call(task, end_call_message)
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
    from livekit.agents import WorkerOptions, cli

    _preflight_or_die()
    # Bind the worker's built-in HTTP health server to Render's injected $PORT so
    # this can run as a Render *web service* (incl. the free tier, which has no
    # background_worker type). host=0.0.0.0 so Render's health probe reaches it.
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
        )
    )
