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
from pipecat.frames.frames import LLMMessagesFrame, TextFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSModel, SarvamTTSService
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


def _build_system_prompt(agent_config: dict, tenant: dict) -> str:
    """
    Build the LLM system prompt from stored config, or render from template.

    Precedence:
      1. agent_config['system_prompt'] — custom prompt set by clinic admin
      2. Rendered prompt_templates entry for agent_config['template']
      3. Hardcoded fallback
    """
    custom_prompt = (agent_config.get("system_prompt") or "").strip()
    if custom_prompt:
        return custom_prompt

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
        return rendered

    except Exception as exc:
        log.warning("Template render failed, using fallback prompt: %s", exc)

    # Hardcoded fallback
    return (
        f"You are {agent_config.get('agent_name', 'Receptionist')}, "
        f"the AI voice receptionist for {tenant.get('clinic_name', 'the clinic')}. "
        "Be concise, professional, and helpful. Maximum 2 sentences per response. "
        "Never give medical advice."
    )


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
        "system_prompt":   metadata.get("system_prompt", ""),
        "template":        metadata.get("template", "clinic_receptionist"),
        "tts_voice":       metadata.get("tts_voice", "priya"),
        "tts_language":    metadata.get("tts_language", "hi-IN"),
        "tts_model":       metadata.get("tts_model", "bulbul:v3"),
        "tts_pace":        float(metadata.get("tts_pace", 1.05)),
        "stt_model":       metadata.get("stt_model", "saaras:v2"),
        "llm_model":       metadata.get("llm_model", "gemini-2.0-flash"),
        "llm_temperature": float(metadata.get("llm_temperature", 0.3)),
        "max_response_tokens": int(metadata.get("max_response_tokens", 120)),
    }

    tenant: dict = {
        "id":            tenant_id or "",
        "clinic_name":   metadata.get("clinic_name", "Clinic"),
        "working_hours": "9 AM – 7 PM, Mon–Sat",
        "doctors":       [],
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
                        "system_prompt":       cfg.system_prompt or "",
                        "template":            getattr(cfg, "template", "clinic_receptionist"),
                        "tts_voice":           cfg.tts_voice or "priya",
                        "tts_language":        cfg.tts_language or "hi-IN",
                        "tts_model":           cfg.tts_model or "bulbul:v3",
                        "tts_pace":            float(cfg.tts_pace or 1.05),
                        "stt_model":           cfg.stt_model or "saaras:v2",
                        "llm_model":           cfg.llm_model or "gemini-2.0-flash",
                        "llm_temperature":     float(cfg.llm_temperature or 0.3),
                        "max_response_tokens": int(cfg.max_response_tokens or 120),
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
    # ── Parse room metadata ────────────────────────────────────────────────
    metadata: dict = {}
    try:
        metadata = json.loads(ctx.room.metadata or "{}")
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

    # saaras:v2.5 auto-detects language — don't pass language for it
    if stt_model == "saaras:v2.5":
        stt_settings = SarvamSTTService.Settings(
            model=stt_model,
        )
    else:
        stt_settings = SarvamSTTService.Settings(
            model=stt_model,
            language=tts_language,
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

    # STT — Sarvam AI (Indian language speech recognition)
    stt = SarvamSTTService(
        api_key=settings.sarvam_api_key,
        settings=stt_settings,
    )

    # LLM — Google Gemini 2.0 Flash via Pipecat GoogleLLMService
    llm = GoogleLLMService(
        api_key=settings.gemini_api_key,
        model=agent_config.get("llm_model", "gemini-2.0-flash"),
        system_instruction=system_prompt,
        settings=GoogleLLMService.Settings(
            temperature=float(agent_config.get("llm_temperature", 0.3)),
            max_tokens=int(agent_config.get("max_response_tokens", 120)),
        ),
    )

    # Build LLM context (conversation history)
    context = LLMContext(
        messages=[
            {"role": "system", "content": system_prompt},
        ]
    )
    context_aggregator = llm.create_context_aggregator(context)

    # TTS — Sarvam AI (Indian language text-to-speech, streaming)
    tts = SarvamTTSService(
        api_key=settings.sarvam_api_key,
        model=tts_model_str,
        voice=tts_voice,
        language=tts_language,
        pace=tts_pace,
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
    )

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

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport_ref, participant_id: str) -> None:
        """Speak the first message when the caller joins the room."""
        log.info("Participant joined: %s — speaking first message.", participant_id)
        # Add the first message to LLM context so LLM is aware of it
        context.add_message({"role": "assistant", "content": first_message})
        # Queue the first greeting as a TextFrame to be synthesized directly by TTS (no self-talk trigger)
        await task.queue_frames([TextFrame(first_message)])

    @transport.event_handler("on_participant_disconnected")
    async def on_participant_disconnected(transport_ref, participant_id: str) -> None:
        """End the pipeline when the caller hangs up."""
        log.info("Participant disconnected: %s — ending pipeline.", participant_id)
        await task.cancel()

    # ── Run ────────────────────────────────────────────────────────────────
    runner = PipelineRunner()
    await runner.run(task)

    log.info("Pipeline finished for room=%s", room_name)


# ── Worker bootstrap ──────────────────────────────────────────────────────────

def prewarm(proc) -> None:
    """
    Pre-warm Silero VAD model before the first call.
    Called once when the worker process starts.
    """
    # Pipecat's SileroVADAnalyzer loads the model lazily on first call.
    # Pre-warming is handled internally by Pipecat — nothing to do here.
    log.info("Agent worker pre-warmed.")


if __name__ == "__main__":
    from livekit.agents import WorkerOptions, cli

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name="lifodial-inbound-agent",
        )
    )
