"""
backend/routers/agents.py — Agent CRUD + preview + test-call endpoints.
Multi-tenant: super admin sees all, clinic admin sees own only.
"""
import asyncio
import json
import logging
import os
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Body, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import CurrentUser, SuperAdmin
from backend.db import async_session
from backend.models.agent_config import AgentConfig
from backend.models.agent_prompt_history import AgentPromptHistory
from backend.models.api_key_config import ApiKeyConfig
from backend.models.doctor import Doctor
from backend.models.tenant import Tenant
from backend.agent.prompt_templates import TEMPLATES, get_template, render_prompt
from backend.services.tenant_service import create_tenant as create_tenant_row
from backend.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Prompt edit history — last N versions of system_prompt / first_message,
# with one-click revert. See update_agent() and the /prompt-history routes.
HISTORY_TRACKED_FIELDS = {"system_prompt", "first_message"}
HISTORY_MAX_ENTRIES = 5


async def _record_prompt_history(session: AsyncSession, agent_id: str, field_name: str, old_value: str) -> None:
    session.add(AgentPromptHistory(
        id=str(uuid.uuid4()), agent_id=agent_id, field_name=field_name, value=old_value,
    ))
    await session.flush()
    # Trim to the most recent HISTORY_MAX_ENTRIES for this (agent_id, field_name).
    stale_ids = (await session.execute(
        select(AgentPromptHistory.id)
        .where(AgentPromptHistory.agent_id == agent_id, AgentPromptHistory.field_name == field_name)
        .order_by(AgentPromptHistory.created_at.desc())
        .offset(HISTORY_MAX_ENTRIES)
    )).scalars().all()
    if stale_ids:
        await session.execute(sa_delete(AgentPromptHistory).where(AgentPromptHistory.id.in_(stale_ids)))


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class NewClinicPayload(BaseModel):
    clinic_name: str
    admin_name: str
    admin_email: str
    phone: str = ""
    location: str = ""
    language: str = "hi-IN"


class AgentCreatePayload(BaseModel):
    # Step 1 — Clinic
    clinic_selection: str = "existing"  # "existing" | "new"
    tenant_id: str | None = None
    new_clinic: NewClinicPayload | None = None

    # Step 2 — Identity
    agent_name: str = "Receptionist"
    template: str = "clinic_receptionist"
    first_message: str = ""
    first_message_mode: str = "assistant-speaks-first"
    system_prompt: str = ""

    # Step 3 — Voice
    stt_provider: str = "sarvam"
    stt_model: str = "saaras:v3"
    stt_language: str = "en-IN"
    transcriber_keywords: str | None = None
    fallback_transcribers: str | None = None

    tts_provider: str = "sarvam"
    tts_model: str = "bulbul:v3"
    tts_voice: str = "priya"
    tts_language: str = "hi-IN"
    tts_pitch: float = Field(0.0, ge=-1.0, le=1.0)
    tts_pace: float = Field(1.0, ge=0.5, le=2.0)
    tts_loudness: float = Field(1.0, ge=0.5, le=2.0)
    tts_stability: float = Field(0.5, ge=0.0, le=1.0)
    tts_clarity: float = Field(0.75, ge=0.0, le=1.0)
    tts_speed: float = Field(1.0, ge=0.5, le=2.0)
    tts_style: float = Field(0.0, ge=0.0, le=1.0)
    tts_use_speaker_boost: bool = False
    tts_optimize_streaming_latency: int = 3
    tts_input_preprocessing: bool = True
    tts_filler_injection: bool = False
    add_voice_manually: str | None = None
    fallback_voices: str | None = None

    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"
    llm_temperature: float = Field(0.7, ge=0.0, le=1.0)
    max_response_tokens: int = Field(500, ge=50, le=2000)
    llm_max_tokens: int = Field(250, ge=50, le=4000)
    llm_emotion_recognition: bool = False

    silence_timeout_seconds: int = 30
    max_duration_seconds: int = 600
    background_sound: str = "none"
    background_denoising: bool = False
    model_output_in_realtime: bool = False
    record_calls: bool = False
    recording_consent_plan: Literal["none", "inform", "require"] | None = "none"

    voicemail_detection_enabled: bool = False
    voicemail_message: str | None = None
    end_call_phrases: str | None = None
    end_call_message: str | None = None
    summary_enabled: bool = True
    success_evaluation_enabled: bool = True
    structured_output_enabled: bool = False

    tools_enabled: str | None = None
    predefined_functions: str | None = None
    custom_functions: str | None = None

    keypad_input_enabled: bool = False
    keypad_timeout: int = 5
    sms_enabled: bool = False
    sms_provider: str | None = None
    sms_message_template: str | None = None
    hipaa_enabled: bool = False
    pii_redaction_enabled: bool = False

    # Step 4 — Telephony
    telephony_option: str = "skip"  # "assign" | "existing" | "skip"
    country_code: str | None = None
    sip_provider: str | None = None
    sip_account_sid: str | None = None
    sip_auth_token: str | None = None
    sip_domain: str | None = None
    livekit_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None
    existing_clinic_number: str | None = None


class AgentPatchPayload(BaseModel):
    agent_name: str | None = None
    first_message: str | None = None
    first_message_mode: str | None = None
    system_prompt: str | None = None
    clinic_info: str | None = None
    
    stt_provider: str | None = None
    stt_model: str | None = None
    stt_language: str | None = None
    transcriber_keywords: str | None = None
    fallback_transcribers: str | None = None

    tts_provider: str | None = None
    tts_voice: str | None = None
    tts_model: str | None = None
    tts_language: str | None = None
    tts_pitch: float | None = None
    tts_pace: float | None = None
    tts_loudness: float | None = None
    tts_stability: float | None = None
    tts_clarity: float | None = None
    tts_speed: float | None = None
    tts_style: float | None = None
    tts_use_speaker_boost: bool | None = None
    tts_optimize_streaming_latency: int | None = None
    tts_input_preprocessing: bool | None = None
    tts_filler_injection: bool | None = None
    add_voice_manually: str | None = None
    fallback_voices: str | None = None

    llm_provider: str | None = None
    llm_temperature: float | None = None
    llm_model: str | None = None
    max_response_tokens: int | None = None
    llm_max_tokens: int | None = None
    llm_emotion_recognition: bool | None = None
    
    silence_timeout_seconds: int | None = None
    max_duration_seconds: int | None = None
    background_sound: str | None = None
    background_denoising: bool | None = None
    model_output_in_realtime: bool | None = None
    record_calls: bool | None = None
    recording_consent_plan: Literal["none", "inform", "require"] | None = None

    voicemail_detection_enabled: bool | None = None
    voicemail_message: str | None = None
    end_call_phrases: str | None = None
    end_call_message: str | None = None
    summary_enabled: bool | None = None
    success_evaluation_enabled: bool | None = None
    structured_output_enabled: bool | None = None

    tools_enabled: str | None = None
    predefined_functions: str | None = None
    custom_functions: str | None = None

    can_book_appointments: bool | None = None
    can_cancel_appointments: bool | None = None
    can_check_availability: bool | None = None
    can_transfer_emergency: bool | None = None
    emergency_transfer_number: str | None = None

    keypad_input_enabled: bool | None = None
    keypad_timeout: int | None = None
    sms_enabled: bool | None = None
    sms_provider: str | None = None
    sms_message_template: str | None = None
    hipaa_enabled: bool | None = None
    pii_redaction_enabled: bool | None = None

    telephony_option: str | None = None
    country_code: str | None = None
    ai_number: str | None = None
    sip_provider: str | None = None
    sip_account_sid: str | None = None
    sip_auth_token: str | None = None
    sip_domain: str | None = None
    livekit_url: str | None = None
    livekit_api_key: str | None = None
    livekit_api_secret: str | None = None
    existing_clinic_number: str | None = None
    google_sheets_webhook_url: str | None = None

    avatar_url: str | None = None
    # Website embed/widget — previously missing here, so the frontend showed
    # "Saved" while Pydantic silently dropped these on every PATCH.
    embed_enabled: bool | None = None
    embed_allowed_domains: list[str] | None = None
    embed_position: str | None = None
    embed_theme: str | None = None
    embed_button_text: str | None = None
    embed_primary_color: str | None = None
    embed_show_branding: bool | None = None
    embed_display_mode: str | None = None
    embed_auto_invite_delay: int | None = None

    status: str | None = None


class PreviewPromptPayload(BaseModel):
    template: str
    language: str
    patient_message: str = "I need an appointment"
    tenant_id: str | None = None
    clinic_name: str = "Demo Clinic"
    agent_name: str = "Receptionist"


# ── Helper ───────────────────────────────────────────────────────────────────

def _agent_to_dict(agent: AgentConfig, clinic_name: str = "") -> dict:
    data = {c.name: getattr(agent, c.name) for c in agent.__table__.columns if hasattr(agent, c.name)}
    data["clinic_name"] = clinic_name
    if data.get("first_message"):
        data["_first_message_preview"] = data["first_message"][:120] + "..." if len(data["first_message"]) > 120 else data["first_message"]
    
    # ISO strings for dates
    data["created_at"] = data["created_at"].isoformat() if data.get("created_at") else None
    data["updated_at"] = data["updated_at"].isoformat() if data.get("updated_at") else None

    # ── Safe defaults for frontend fields not in DB ──────────────────────
    # The AgentDetail.tsx form reads these; if missing the page crashes.
    _defaults = {
        "first_message_mode": "assistant-speaks-first",
        "background_sound": "none",
        "background_denoising": False,
        "record_calls": False,
        "model_output_in_realtime": False,
        "tts_stability": 0.5,
        "tts_clarity": 0.75,
        "tts_style": 0.0,
        "tts_speed": 1.0,
        "tts_use_speaker_boost": False,
        "tts_optimize_streaming_latency": 3,
        "tts_filler_injection": False,
        "tts_input_preprocessing": True,
        "voicemail_detection_enabled": False,
        "voicemail_message": "",
        "summary_enabled": True,
        "success_evaluation_enabled": True,
        "structured_output_enabled": False,
        "tools_enabled": "[]",
        "recording_consent_plan": "none",
        "can_book_appointments": True,
        "can_cancel_appointments": True,
        "can_check_availability": True,
        "can_transfer_emergency": True,
        "emergency_transfer_number": "",
        "keypad_input_enabled": False,
        "hipaa_enabled": False,
        "pii_redaction_enabled": False,
        "transcriber_keywords": "[]",
        "google_sheets_webhook_url": "",
        "embed_display_mode": "button",
        "embed_auto_invite_delay": 3,
    }
    for key, default in _defaults.items():
        if key not in data or data[key] is None:
            data[key] = default

    return data


# ── GET /agents — list all (super admin) ─────────────────────────────────────

@router.get("/agents")
async def list_agents(user: SuperAdmin = None) -> list[dict]:
    try:
        async with async_session() as session:
            result = await session.execute(
                select(AgentConfig, Tenant.clinic_name)
                .join(Tenant, AgentConfig.tenant_id == Tenant.id)
                .order_by(AgentConfig.created_at.desc())
            )
            rows = result.all()
            return [_agent_to_dict(agent, clinic_name) for agent, clinic_name in rows]
    except Exception as e:
        logger.exception("Error listing agents: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /agents/mine — clinic admin self-lookup by email ─────────────────────
# Called by MyAgent.tsx after login. Returns the single agent for the clinic
# whose admin_email matches the logged-in user.

@router.get("/agents/mine")
async def get_my_agent(email: str, user: CurrentUser = None) -> dict:
    """
    Clinic-admin endpoint: given an email, return that clinic's agent.
    Requires a valid session token; the resolved tenant must match the
    caller's own tenant (superadmin may look up any clinic).
    Returns 404 if no clinic/agent found for that email.
    """
    if not email:
        raise HTTPException(status_code=400, detail="email query param required")
    try:
        async with async_session() as session:
            result = await session.execute(
                select(AgentConfig, Tenant.clinic_name)
                .join(Tenant, AgentConfig.tenant_id == Tenant.id)
                .where(Tenant.admin_email == email.strip().lower())
            )
            row = result.first()
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail=f"No agent found for email: {email}"
                )
            agent, clinic_name = row
            user.require_owns(str(agent.tenant_id))
            data = _agent_to_dict(agent, clinic_name)
            data["system_prompt"] = agent.system_prompt
            data["first_message"] = agent.first_message
            return data
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error fetching agent for email %s: %s", email, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /auth/clinic-login ────────────────────────────────────────────────────
# Simple email-based login for clinic admin portal.
# Returns tenant_id so frontend can store it in localStorage.

class ClinicLoginPayload(BaseModel):
    email: str
    password: str


class SuperAdminLoginPayload(BaseModel):
    email: str
    password: str


@router.post("/auth/clinic-login")
async def clinic_login(payload: ClinicLoginPayload) -> dict:
    """
    Clinic admin login. Verifies the password against the stored hash and
    issues a signed JWT. Legacy plaintext passwords are transparently upgraded
    to a hash on first successful login.
    """
    from backend.security import (
        create_access_token,
        hash_password,
        needs_rehash,
        verify_password,
    )

    email = payload.email.strip().lower()
    # Uniform failure message — never reveal whether the email exists.
    invalid = HTTPException(status_code=401, detail="Invalid email or password")
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Tenant).where(Tenant.admin_email == email)
            )
            tenant = result.scalar_one_or_none()
            if not tenant or not verify_password(payload.password, tenant.admin_password):
                raise invalid

            # Transparently upgrade legacy plaintext / weak hashes.
            if needs_rehash(tenant.admin_password):
                tenant.admin_password = hash_password(payload.password)
                await session.commit()

            token = create_access_token(subject=str(tenant.id), role="clinic")
            return {
                "access_token": token,
                "token_type": "bearer",
                "role": "clinic",
                "tenant_id": tenant.id,
                "clinic_name": tenant.clinic_name,
                "admin_name": tenant.admin_name,
                "email": tenant.admin_email,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Clinic login error: %s", e)
        raise HTTPException(status_code=500, detail="Login failed")


@router.post("/auth/superadmin-login")
async def superadmin_login(payload: SuperAdminLoginPayload) -> dict:
    """Platform-owner login. Credentials come from env (SUPERADMIN_EMAIL /
    SUPERADMIN_PASSWORD) — never hardcoded in the client."""
    import hmac

    from backend.security import create_access_token

    if not settings.superadmin_password:
        raise HTTPException(status_code=503, detail="Superadmin login is not configured")

    email_ok = hmac.compare_digest(
        payload.email.strip().lower(), settings.superadmin_email.strip().lower()
    )
    pass_ok = hmac.compare_digest(payload.password, settings.superadmin_password)
    if not (email_ok and pass_ok):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(subject="superadmin", role="superadmin")
    return {"access_token": token, "token_type": "bearer", "role": "superadmin"}


# ── GET /agents/templates ─────────────────────────────────────────────────────

@router.get("/agents/templates")
async def list_templates() -> list[dict]:
    return [
        {
            "key": key,
            "name": tmpl["name"],
            "description": tmpl["description"],
            "icon": tmpl["icon"],
            "languages": list(tmpl["languages"].keys()),
        }
        for key, tmpl in TEMPLATES.items()
    ]


# ── POST /agents/preview-prompt ───────────────────────────────────────────────

@router.post("/agents/preview-prompt")
async def preview_prompt(payload: PreviewPromptPayload, user: CurrentUser = None) -> dict:
    import time
    try:
        tmpl_data = get_template(payload.template, payload.language)
        rendered_system = render_prompt(
            tmpl_data["system_prompt"],
            {
                "clinic_name": payload.clinic_name,
                "agent_name": payload.agent_name,
                "clinic_location": "India",
                "working_hours": "Mon-Sat 9AM-7PM",
                "emergency_number": "+91 80000 00000",
                "doctors_list": "Dr. Smith (General), Dr. Patel (Cardiology)",
            },
        )
        t0 = time.monotonic()
        try:
            from google import genai
            client = genai.Client(api_key=settings.gemini_api_key)
            full_prompt = f"{rendered_system}\n\nPatient: {payload.patient_message}\nAgent:"
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=full_prompt,
            )
            ai_text = response.text.strip()
        except Exception as gemini_err:
            logger.warning("Gemini preview failed: %s", gemini_err)
            ai_text = f"[Preview] Hello! Thank you for calling {payload.clinic_name}. How can I help you?"
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {
            "ai_response": ai_text,
            "latency_ms": latency_ms,
            "detected_intent": "booking" if "appoint" in payload.patient_message.lower() else "query",
        }
    except Exception as e:
        logger.exception("Preview prompt error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /agents/{agent_id}/generate-system-prompt ────────────────────────────
# Streams a fresh, clinic-specific system prompt using the agent's configured
# LLM provider (falling back to any other provider that has a configured key).
# Response body is newline-delimited JSON events consumed by the frontend as
# they arrive: {"type":"meta",...} once, then {"type":"chunk","text":...}
# repeatedly, then a single {"type":"done"} or {"type":"error","message":...}.

_GEN_PROVIDER_DEFAULTS = {
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5",
    "groq": "llama-3.3-70b-versatile",
    "deepseek": "deepseek-chat",
}

_GEN_LANG_NAMES = {
    "en-IN": "English", "hi-IN": "Hindi", "ta-IN": "Tamil", "te-IN": "Telugu",
    "kn-IN": "Kannada", "ml-IN": "Malayalam", "bn-IN": "Bengali", "gu-IN": "Gujarati",
    "mr-IN": "Marathi", "pa-IN": "Punjabi", "ar-SA": "Arabic",
}

# Headings inserted by the "Quick-inject" snippet chips in AgentDetail.tsx —
# used to tell the LLM which feature sections the admin has already turned on.
_GEN_CATEGORY_HEADINGS = [
    "## Appointment Booking", "## Clinic Hours & Location",
    "## Doctors & Specialities", "## Emergency Handling", "## Language",
]


# Maps an agent's enabled capabilities to the concrete behaviours the generated
# prompt is allowed to reference. dbField-backed tools are read from the agent's
# real columns; the rest come from the tools_enabled JSON list. Keeping this in
# one place guarantees the prompt never references a capability the agent lacks.
_TOOL_BEHAVIOURS = {
    "can_book_appointments": "book appointments (collect patient name, phone, preferred doctor, and preferred date/time, then read it back to confirm)",
    "can_check_availability": "check the status of an existing appointment for a caller",
    "can_cancel_appointments": "cancel or reschedule an existing appointment",
    "can_transfer_emergency": "detect a medical emergency and immediately direct the caller to the emergency number",
    "doctors": "answer questions about the clinic's doctors and their specialities",
    "hours": "tell callers the clinic's opening hours and location",
    "transfer_call": "offer to transfer the caller to a human staff member",
    "sms": "offer to send an SMS confirmation",
}


def _collect_agent_capabilities(agent) -> list[str]:
    """Human-readable list of what THIS agent is actually allowed to do, drawn
    from both the boolean AgentConfig columns and the tools_enabled JSON list."""
    caps: list[str] = []
    for col in ("can_book_appointments", "can_check_availability", "can_cancel_appointments", "can_transfer_emergency"):
        if getattr(agent, col, False):
            caps.append(_TOOL_BEHAVIOURS[col])
    try:
        extra = json.loads(agent.tools_enabled) if agent.tools_enabled else []
    except (json.JSONDecodeError, TypeError):
        extra = []
    for tid in extra:
        if tid in _TOOL_BEHAVIOURS and _TOOL_BEHAVIOURS[tid] not in caps:
            caps.append(_TOOL_BEHAVIOURS[tid])
    return caps


async def _fetch_doctor_names(session: AsyncSession, tenant_id: str) -> list[str]:
    """Doctor names + specialities for this tenant, for prompt grounding."""
    rows = (await session.execute(
        select(Doctor.name, Doctor.specialization).where(Doctor.tenant_id == tenant_id)
    )).all()
    return [f"{name} ({spec})" if spec else name for name, spec in rows]


def _build_prompt_meta_instruction(ctx: dict) -> str:
    """Hardened meta-prompt that asks the LLM to write a production system prompt."""
    lang_name = _GEN_LANG_NAMES.get(ctx["primary_language"], ctx["primary_language"] or "English")
    existing = (ctx["existing_system_prompt"] or "").strip()
    capabilities = ctx.get("capabilities") or []
    caps_block = (
        "- Enabled capabilities (reference ONLY these — do NOT mention any capability not listed):\n"
        + "\n".join(f"    • {c}" for c in capabilities)
        if capabilities
        else "- Enabled capabilities: NONE. This agent only answers general questions and hands off to staff — do NOT describe booking, cancellation, doctor lookup, or emergency handling."
    )
    doctors = ctx.get("doctors") or []
    doctors_block = (
        "- Clinic doctors (you may reference these by name/speciality):\n"
        + "\n".join(f"    • {d}" for d in doctors)
        if doctors
        else "- Clinic doctors: none on file — do not invent doctor names."
    )
    existing_block = (
        f'\n\nThe clinic admin\'s current draft system prompt (improve and build on this rather than '
        f'ignoring it, unless it is clearly a placeholder or test string):\n"""\n{existing}\n"""\n'
        if existing else "\n\nNo existing system prompt has been written yet — write one from scratch.\n"
    )
    return f"""You are an expert prompt engineer who writes production system prompts for AI voice/chat receptionists used by real medical clinics.

Write a complete system prompt for the following AI receptionist. Output ONLY the system prompt text itself — no preamble, no markdown code fences, no explanation before or after.

CLINIC CONTEXT:
- Clinic name: {ctx['clinic_name']}
- Agent name: {ctx['agent_name']}
- Agent purpose: {ctx['agent_purpose']}
- First message already spoken/shown to callers: "{ctx['first_message']}"
- Primary conversation language: {lang_name}
{caps_block}
{doctors_block}
{existing_block}
REQUIREMENTS:
1. Open by establishing the agent's identity and name the clinic explicitly ("{ctx['clinic_name']}").
2. Explain how the agent should greet callers and what it can help with — but ONLY the enabled capabilities listed above.
3. For each enabled capability, give clear, concrete instructions (e.g. for booking: what information to collect and how to confirm it). Do NOT write instructions for capabilities that are not enabled.
4. Instruct the agent on what to do when it cannot help or doesn't know an answer (never invent information; offer to escalate to clinic staff).
5. Explain how and when to hand off to a human (complex complaints, angry callers){', and how to handle medical emergencies' if any('emergency' in c for c in capabilities) else ''}.
6. Match the tone to a real clinic receptionist: warm, professional, concise — written for {lang_name} conversations.
7. Don't re-instruct the agent to speak the first message as a fresh greeting — it has already been said; write as if the conversation is already underway.

LENGTH: Between 150 and 400 words. Do not pad — every sentence must be operationally useful for a live phone/chat agent."""


def _build_first_message_instruction(ctx: dict) -> str:
    """Meta-prompt for the spoken/shown first greeting (Compose with AI)."""
    lang_name = _GEN_LANG_NAMES.get(ctx["primary_language"], ctx["primary_language"] or "English")
    agent_name = (ctx.get("agent_name") or "").strip()
    name_line = (
        f"- The agent's name is \"{agent_name}\" — introduce using this name."
        if agent_name and agent_name.lower() != "receptionist"
        else "- The agent has no name yet — invent a natural, warm receptionist first name that fits the "
             f"{lang_name} language/region, and introduce using it."
    )
    existing = (ctx.get("existing_first_message") or "").strip()
    existing_block = (
        f'\n\nThere is an existing draft greeting; produce an improved version in the same spirit:\n"""\n{existing}\n"""\n'
        if existing else ""
    )
    return f"""You write the opening greeting that an AI voice/chat receptionist speaks FIRST when answering for a real medical clinic.

Write ONE warm, professional first message. Output ONLY the greeting text — no preamble, no quotes, no explanation.

CONTEXT:
- Clinic name: {ctx['clinic_name']}
{name_line}
- Language: write the greeting entirely in {lang_name}.
- It is spoken aloud, so keep it to 1–2 natural sentences.
- Introduce who is speaking, name the clinic ("{ctx['clinic_name']}"), and warmly invite the caller to say how you can help (e.g. booking an appointment or a question).
- Do NOT list specific capabilities or doctors; keep it a friendly opener.{existing_block}"""


async def _resolve_llm_key(session: AsyncSession, provider: str) -> str | None:
    """DB-configured key first (encrypted, admin-managed), then .env fallback."""
    result = await session.execute(
        select(ApiKeyConfig).where(
            ApiKeyConfig.provider == provider,
            ApiKeyConfig.is_active == True,  # noqa: E712
        ).limit(1)
    )
    cfg = result.scalars().first()
    if cfg and cfg.api_key_enc:
        raw = cfg.get_key_raw()
        if raw:
            return raw

    env_map = {
        "gemini": settings.gemini_api_key or os.getenv("GEMINI_API_KEY"),
        "openai": settings.openai_api_key or os.getenv("OPENAI_API_KEY"),
        "anthropic": settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY"),
        "groq": settings.groq_api_key or os.getenv("GROQ_API_KEY"),
        "deepseek": settings.deepseek_api_key or os.getenv("DEEPSEEK_API_KEY"),
    }
    env_key = env_map.get(provider)
    return env_key.strip() if env_key else None


async def _stream_openai_compatible(api_key: str, base_url: str, model: str, prompt: str):
    """Streams token deltas from any OpenAI-compatible chat/completions API (Groq, OpenAI, DeepSeek)."""
    import httpx

    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream(
            "POST",
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 700,
                "temperature": 0.6,
                "stream": True,
            },
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                raise RuntimeError(f"{response.status_code} — {body.decode(errors='ignore')[:300]}")
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = (obj.get("choices") or [{}])[0].get("delta", {}).get("content")
                if delta:
                    yield delta


async def _stream_anthropic(api_key: str, model: str, prompt: str):
    import httpx

    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream(
            "POST",
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 700,
                "stream": True,
                "messages": [{"role": "user", "content": prompt}],
            },
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                raise RuntimeError(f"{response.status_code} — {body.decode(errors='ignore')[:300]}")
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if not data:
                    continue
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "content_block_delta":
                    delta = obj.get("delta", {}).get("text")
                    if delta:
                        yield delta


async def _generate_gemini_prompt(api_key: str, model: str, prompt: str) -> str:
    import httpx

    gemini_model = model if model.startswith("models/") else f"models/{model}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/{gemini_model}:generateContent",
            headers={"Content-Type": "application/json"},
            params={"key": api_key},
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 700, "temperature": 0.6},
            },
        )
        if response.status_code != 200:
            raise RuntimeError(f"{response.status_code} — {response.text[:300]}")
        data = response.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            raise RuntimeError("Gemini returned no candidate (likely blocked by a safety filter)")


async def _generate_prompt_stream(provider: str, api_key: str, model: str, prompt: str):
    """Yields text chunks as they're generated, uniformly across providers.
    Gemini has no simple REST streaming path here, so its single response is
    chunked into word groups with a tiny delay to still give a "typing" feel."""
    if provider == "groq":
        async for piece in _stream_openai_compatible(api_key, "https://api.groq.com/openai/v1", model, prompt):
            yield piece
    elif provider == "openai":
        async for piece in _stream_openai_compatible(api_key, "https://api.openai.com/v1", model, prompt):
            yield piece
    elif provider == "deepseek":
        async for piece in _stream_openai_compatible(api_key, "https://api.deepseek.com/v1", model, prompt):
            yield piece
    elif provider == "anthropic":
        async for piece in _stream_anthropic(api_key, model, prompt):
            yield piece
    elif provider == "gemini":
        text = await _generate_gemini_prompt(api_key, model, prompt)
        words = text.split(" ")
        buf = ""
        for i, w in enumerate(words):
            buf += w + (" " if i < len(words) - 1 else "")
            if len(buf) >= 12 or i == len(words) - 1:
                yield buf
                buf = ""
                await asyncio.sleep(0.02)
    else:
        raise RuntimeError(f"Unsupported LLM provider: {provider}")


async def _resolve_generation_model(session: AsyncSession, agent) -> tuple[str | None, str | None, str | None, bool]:
    """Pick the LLM to generate with: the agent's OWN selected provider/model when
    it has a configured key, otherwise fall back to the next configured provider.
    Returns (provider, model, key, fallback_used). Never a hardcoded model when the
    agent's selected provider is configured."""
    preferred = agent.llm_provider or "gemini"
    fallback_order = [preferred] + [
        p for p in ("groq", "gemini", "openai", "anthropic", "deepseek") if p != preferred
    ]
    for prov in fallback_order:
        key = await _resolve_llm_key(session, prov)
        if key:
            model = (
                agent.llm_model if prov == preferred and agent.llm_model
                else _GEN_PROVIDER_DEFAULTS.get(prov)
            )
            return prov, model, key, (prov != preferred)
    return None, None, None, False


def _generation_stream_response(provider, model, key, meta_prompt, fallback_used, label):
    """Shared ndjson streaming responder for both generation endpoints."""
    if not provider:
        async def _no_key_stream():
            yield json.dumps({
                "type": "error",
                "message": "No LLM provider is configured with a valid API key. Add one under Providers settings, then try again.",
            }) + "\n"
        return StreamingResponse(_no_key_stream(), media_type="application/x-ndjson")

    async def _stream():
        yield json.dumps({
            "type": "meta", "provider": provider, "model": model, "fallback_used": fallback_used,
        }) + "\n"
        try:
            collected: list[str] = []
            async for piece in _generate_prompt_stream(provider, key, model, meta_prompt):
                collected.append(piece)
                yield json.dumps({"type": "chunk", "text": piece}) + "\n"
            if not "".join(collected).strip():
                yield json.dumps({"type": "error", "message": "The model returned an empty response. Please try again."}) + "\n"
                return
            yield json.dumps({"type": "done"}) + "\n"
        except Exception as exc:
            logger.exception("%s generation failed (provider=%s): %s", label, provider, exc)
            yield json.dumps({"type": "error", "message": f"Generation failed via {provider}: {exc}"}) + "\n"

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


@router.post("/agents/{agent_id}/generate-system-prompt")
async def generate_system_prompt(agent_id: str, user: CurrentUser = None):
    async with async_session() as session:
        result = await session.execute(
            select(AgentConfig, Tenant.clinic_name)
            .join(Tenant, AgentConfig.tenant_id == Tenant.id)
            .where(AgentConfig.id == agent_id)
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Agent not found")
        agent, clinic_name = row
        user.require_owns(str(agent.tenant_id))

        clinic_name = clinic_name or "the clinic"
        # Pull REAL clinic context: enabled capabilities (from columns + tools list)
        # and the tenant's actual doctor roster — so the prompt only references
        # what this agent can do and never invents doctors.
        capabilities = _collect_agent_capabilities(agent)
        doctors = await _fetch_doctor_names(session, str(agent.tenant_id))
        ctx = {
            "clinic_name": clinic_name,
            "agent_name": agent.agent_name or "Receptionist",
            "agent_purpose": (
                f"AI receptionist for {clinic_name}, a medical clinic — handles inbound calls/chats "
                "and answers clinic questions"
            ),
            "first_message": agent.first_message or "",
            "primary_language": agent.tts_language or agent.stt_language or "en-IN",
            "existing_system_prompt": agent.system_prompt or "",
            "capabilities": capabilities,
            "doctors": doctors,
        }
        meta_prompt = _build_prompt_meta_instruction(ctx)
        provider, model, key, fallback_used = await _resolve_generation_model(session, agent)

    return _generation_stream_response(provider, model, key, meta_prompt, fallback_used, "System prompt")


@router.post("/agents/{agent_id}/generate-first-message")
async def generate_first_message(agent_id: str, user: CurrentUser = None):
    """Compose with AI — generate a warm, clinic-specific first greeting using the
    agent's OWN selected LLM provider/model."""
    async with async_session() as session:
        result = await session.execute(
            select(AgentConfig, Tenant.clinic_name)
            .join(Tenant, AgentConfig.tenant_id == Tenant.id)
            .where(AgentConfig.id == agent_id)
        )
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Agent not found")
        agent, clinic_name = row
        user.require_owns(str(agent.tenant_id))

        ctx = {
            "clinic_name": clinic_name or "the clinic",
            "agent_name": agent.agent_name or "",
            "primary_language": agent.tts_language or agent.stt_language or "en-IN",
            "existing_first_message": agent.first_message or "",
        }
        meta_prompt = _build_first_message_instruction(ctx)
        provider, model, key, fallback_used = await _resolve_generation_model(session, agent)

    return _generation_stream_response(provider, model, key, meta_prompt, fallback_used, "First message")


# ── GET /agents/{agent_id} ────────────────────────────────────────────────────

@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, user: CurrentUser = None) -> dict:
    try:
        async with async_session() as session:
            result = await session.execute(
                select(AgentConfig, Tenant.clinic_name)
                .join(Tenant, AgentConfig.tenant_id == Tenant.id)
                .where(AgentConfig.id == agent_id)
            )
            row = result.first()
            if not row:
                raise HTTPException(status_code=404, detail="Agent not found")
            agent, clinic_name = row
            user.require_owns(str(agent.tenant_id))
            data = _agent_to_dict(agent, clinic_name)
            
            # Fetch Tenant google_sheets_webhook_url
            t_res = await session.execute(select(Tenant).where(Tenant.id == agent.tenant_id))
            tenant = t_res.scalar_one_or_none()
            data["google_sheets_webhook_url"] = tenant.google_sheets_webhook_url if tenant else ""
            
            # Include full prompt for edit page
            data["system_prompt"] = agent.system_prompt
            data["first_message"] = agent.first_message
            data["tts_pitch"] = agent.tts_pitch
            data["tts_pace"] = agent.tts_pace
            data["tts_loudness"] = agent.tts_loudness
            data["llm_temperature"] = agent.llm_temperature
            data["max_response_tokens"] = agent.max_response_tokens
            return data
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error fetching agent %s: %s", agent_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /agents — create ─────────────────────────────────────────────────────

@router.post("/agents", status_code=201)
async def create_agent(payload: AgentCreatePayload, user: SuperAdmin = None) -> dict:
    try:
        async with async_session() as session:
            tenant_id = payload.tenant_id
            clinic_credentials: dict | None = None

            # ── If new clinic, create Tenant + Agent atomically in this transaction.
            # If anything below fails before commit, the tenant insert is rolled
            # back too — no orphaned zero-agent clinics from a failed attempt.
            if payload.clinic_selection == "new" and payload.new_clinic:
                nc = payload.new_clinic
                import secrets
                raw_password = "Lf" + secrets.token_urlsafe(6)
                try:
                    new_tenant = await create_tenant_row(
                        session,
                        clinic_name=nc.clinic_name,
                        admin_name=nc.admin_name,
                        admin_email=nc.admin_email,
                        phone=nc.phone,
                        location=nc.location,
                        language=nc.language,
                    )
                except IntegrityError:
                    await session.rollback()
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"A clinic named '{nc.clinic_name}' already exists. "
                            "Choose it from Existing Clinic instead."
                        ),
                    )
                tenant_id = new_tenant.id
                clinic_credentials = {
                    "email": nc.admin_email,
                    "password": raw_password,
                    "note": "Shown only once — store securely.",
                }
            elif payload.clinic_selection == "existing":
                # Never trust a client-supplied tenant_id blindly — verify it
                # resolves to a real clinic before attaching an agent to it.
                if not tenant_id:
                    raise HTTPException(status_code=400, detail="tenant_id required for existing clinic")
                existing_tenant = await session.execute(
                    select(Tenant.id).where(Tenant.id == tenant_id)
                )
                if not existing_tenant.scalar_one_or_none():
                    raise HTTPException(status_code=404, detail="Clinic not found")
            else:
                raise HTTPException(status_code=400, detail="tenant_id required for existing clinic")

            # NOTE: a clinic may have any number of agents — no per-clinic cap here.
            # (Plan-based limits are a deferred feature; see Tenant.max_agents.)

            # ── Use the first_message/system_prompt exactly as the user typed.
            # We do NOT auto-fill from template — user writes it from scratch.
            first_message = payload.first_message
            system_prompt = payload.system_prompt

            # ── Assign a dummy AI number if telephony requested ──
            ai_number = None
            if payload.telephony_option == "assign":
                country_prefix = "+91" if payload.country_code == "IN" else "+971"
                ai_number = f"{country_prefix} 90001 {str(uuid.uuid4().int)[:5]}"

            # ── Dynamic Model Filtering (Bug 1 Fix) ──
            # Only pass fields that exist in the AgentConfig model.
            from sqlalchemy import inspect as sa_inspect
            mapper = sa_inspect(AgentConfig)
            valid_columns = {col.key for col in mapper.columns}
            
            # Start with the dumped payload, then inject manual overrides
            raw_kwargs = payload.model_dump()
            raw_kwargs.update({
                "tenant_id": tenant_id,
                "first_message": first_message,
                "system_prompt": system_prompt,
                "livekit_url": payload.livekit_url or settings.livekit_url,
                "livekit_api_key": payload.livekit_api_key or settings.livekit_api_key,
                "livekit_api_secret": payload.livekit_api_secret or settings.livekit_api_secret,
                "status": "CONFIGURED",
                "ai_number": ai_number,
            })
            
            # Filter kwargs to only valid columns
            safe_kwargs = {
                k: v for k, v in raw_kwargs.items() 
                if k in valid_columns
            }

            agent = AgentConfig(**safe_kwargs)
            session.add(agent)
            await session.commit()
            await session.refresh(agent)

            livekit_test_url = f"{settings.livekit_url.replace('wss://', 'https://')}"

            return {
                "agent_id": agent.id,
                "tenant_id": tenant_id,
                "ai_number": ai_number,
                "status": "CONFIGURED",
                "clinic_credentials": clinic_credentials,
                "livekit_room_test_url": livekit_test_url,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error creating agent: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── PATCH /agents/{agent_id} — update ────────────────────────────────────────

@router.patch("/agents/{agent_id}")
async def update_agent(agent_id: str, payload: AgentPatchPayload, user: CurrentUser = None) -> dict:
    try:
        async with async_session() as session:
            result = await session.execute(
                select(AgentConfig).where(AgentConfig.id == agent_id)
            )
            agent = result.scalar_one_or_none()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            user.require_owns(str(agent.tenant_id))

            # If google_sheets_webhook_url is provided, update the Tenant's record
            if payload.google_sheets_webhook_url is not None:
                t_res = await session.execute(select(Tenant).where(Tenant.id == agent.tenant_id))
                tenant = t_res.scalar_one_or_none()
                if tenant:
                    tenant.google_sheets_webhook_url = payload.google_sheets_webhook_url.strip() if payload.google_sheets_webhook_url else None

            # Snapshot the outgoing system_prompt / first_message into history
            # BEFORE overwriting, so a bad edit can be reverted later.
            for tracked_field in HISTORY_TRACKED_FIELDS:
                new_value = getattr(payload, tracked_field)
                old_value = getattr(agent, tracked_field) or ""
                if new_value is not None and new_value != old_value:
                    await _record_prompt_history(session, agent.id, tracked_field, old_value)

            # Only set fields that actually exist as DB columns on AgentConfig
            _model_columns = {c.name for c in AgentConfig.__table__.columns}
            for field, value in payload.model_dump(exclude_none=True).items():
                if field in _model_columns:
                    setattr(agent, field, value)

            await session.commit()
            await session.refresh(agent)
            return {"id": agent.id, "status": agent.status, "updated": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error updating agent %s: %s", agent_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /agents/{agent_id}/prompt-history — last 5 versions of a field ───────

@router.get("/agents/{agent_id}/prompt-history")
async def get_prompt_history(agent_id: str, field: str, user: CurrentUser = None) -> list[dict]:
    if field not in HISTORY_TRACKED_FIELDS:
        raise HTTPException(status_code=400, detail=f"field must be one of {sorted(HISTORY_TRACKED_FIELDS)}")
    try:
        async with async_session() as session:
            agent = (await session.execute(
                select(AgentConfig).where(AgentConfig.id == agent_id)
            )).scalar_one_or_none()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            user.require_owns(str(agent.tenant_id))

            rows = (await session.execute(
                select(AgentPromptHistory)
                .where(AgentPromptHistory.agent_id == agent_id, AgentPromptHistory.field_name == field)
                .order_by(AgentPromptHistory.created_at.desc())
                .limit(HISTORY_MAX_ENTRIES)
            )).scalars().all()
            return [
                {
                    "id": r.id,
                    "field_name": r.field_name,
                    "value": r.value,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error fetching prompt history for agent %s: %s", agent_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /agents/{agent_id}/prompt-history/{history_id}/revert ──────────────

@router.post("/agents/{agent_id}/prompt-history/{history_id}/revert")
async def revert_prompt_history(agent_id: str, history_id: str, user: CurrentUser = None) -> dict:
    try:
        async with async_session() as session:
            agent = (await session.execute(
                select(AgentConfig).where(AgentConfig.id == agent_id)
            )).scalar_one_or_none()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            user.require_owns(str(agent.tenant_id))

            entry = (await session.execute(
                select(AgentPromptHistory).where(
                    AgentPromptHistory.id == history_id,
                    AgentPromptHistory.agent_id == agent_id,
                )
            )).scalar_one_or_none()
            if not entry:
                raise HTTPException(status_code=404, detail="History entry not found")

            field_name = entry.field_name
            current_value = getattr(agent, field_name) or ""
            # The value being replaced becomes its own history entry, so
            # reverting is itself undoable.
            if current_value != entry.value:
                await _record_prompt_history(session, agent.id, field_name, current_value)
            setattr(agent, field_name, entry.value)

            await session.commit()
            await session.refresh(agent)
            return {"id": agent.id, "field_name": field_name, "value": getattr(agent, field_name)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error reverting prompt history for agent %s: %s", agent_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /agents/{agent_id}/avatar — upload widget avatar ────────────────────

@router.post("/agents/{agent_id}/avatar")
async def upload_agent_avatar(agent_id: str, file: UploadFile = File(...), user: CurrentUser = None) -> dict:
    """
    Upload a per-agent widget avatar. Stored in the tenant's branding/ folder in
    the PUBLIC bucket (the widget loads it on external sites with no auth). The
    original is kept, but the widget is served an optimized 256×256 WebP so a
    large source upload never slows the embed script. PNG/JPG/WebP, ≤8MB.
    """
    from backend.services import storage

    ext = storage.AVATAR_MIME_EXT.get((file.content_type or "").lower())
    if not ext:
        raise HTTPException(status_code=400, detail="Avatar must be PNG, JPG, or WebP.")

    content = await file.read()
    if len(content) > storage.AVATAR_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Avatar must be 8MB or smaller.")
    if not content:
        raise HTTPException(status_code=400, detail="Empty file.")

    try:
        async with async_session() as session:
            agent = (await session.execute(
                select(AgentConfig).where(AgentConfig.id == agent_id)
            )).scalar_one_or_none()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            user.require_owns(str(agent.tenant_id))

            base = f"{agent.tenant_id}/branding/{agent_id}-avatar"
            # Keep the original (best-effort — never fail the upload if it errors).
            try:
                await storage.upload_public(f"{base}-orig.{ext}", content, file.content_type)
            except Exception as orig_err:
                logger.warning("Original avatar store failed for %s: %s", agent_id, orig_err)

            # Serve an optimized WebP when possible; fall back to the original bytes.
            optimized = storage.optimize_avatar_to_webp(content)
            if optimized:
                path = f"{base}.webp"
                url = await storage.upload_public(path, optimized, "image/webp")
            else:
                path = f"{base}.{ext}"
                url = await storage.upload_public(path, content, file.content_type)

            import time as _t
            # cache-busting query so a re-upload to the same path is picked up.
            agent.avatar_url = f"{url}?v={int(_t.time())}"
            await session.commit()
            await session.refresh(agent)
            return {"avatar_url": agent.avatar_url}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Avatar upload failed for agent %s: %s", agent_id, e)
        raise HTTPException(status_code=500, detail=f"Avatar upload failed: {str(e)[:200]}")


@router.delete("/agents/{agent_id}/avatar")
async def delete_agent_avatar(agent_id: str, user: CurrentUser = None) -> dict:
    """Clear the agent's avatar (widget reverts to the default icon)."""
    try:
        async with async_session() as session:
            agent = (await session.execute(
                select(AgentConfig).where(AgentConfig.id == agent_id)
            )).scalar_one_or_none()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            user.require_owns(str(agent.tenant_id))
            agent.avatar_url = None
            await session.commit()
            return {"avatar_url": None}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Avatar delete failed for agent %s: %s", agent_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── DELETE /agents/{agent_id} ─────────────────────────────────────────────────

@router.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: str, user: SuperAdmin = None) -> None:
    try:
        async with async_session() as session:
            result = await session.execute(
                select(AgentConfig).where(AgentConfig.id == agent_id)
            )
            agent = result.scalar_one_or_none()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            await session.delete(agent)
            await session.commit()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error deleting agent %s: %s", agent_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /agents/{agent_id}/test — text-based test ──────────────────────────

@router.post("/agents/{agent_id}/test")
async def test_agent_text(agent_id: str, body: dict = Body(...), user: CurrentUser = None) -> dict:
    import time
    patient_message = body.get("message", "Hello")
    session_id = body.get("session_id") or f"chat-test-{agent_id}"
    try:
        async with async_session() as session:
            result = await session.execute(
                select(AgentConfig).where(AgentConfig.id == agent_id)
            )
            agent = result.scalar_one_or_none()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            user.require_owns(str(agent.tenant_id))

            t0 = time.monotonic()
            
            # Use generate_llm_response which supports Groq/Gemini, conversation memory, and action tags interceptor!
            from backend.routers.agent_test import generate_llm_response
            
            ai_text = await generate_llm_response(
                agent=agent,
                user_message=patient_message,
                db=session,
                session_id=session_id
            )
            
            latency_ms = int((time.monotonic() - t0) * 1000)

            return {
                "agent_id": agent_id,
                "patient_message": patient_message,
                "ai_response": ai_text,
                "latency_ms": latency_ms,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Test agent error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /agents/{agent_id}/test-call — create LiveKit room ─────────────────

@router.post("/agents/{agent_id}/test-call")
async def test_call(agent_id: str, user: CurrentUser = None) -> dict:
    async with async_session() as session:
        result = await session.execute(select(AgentConfig).where(AgentConfig.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        user.require_owns(str(agent.tenant_id))
    room_name = f"call-test-{agent_id[:8]}-{uuid.uuid4().hex[:6]}"
    return {
        "room_name": room_name,
        "livekit_url": settings.livekit_url,
        "message": "Join this room in LiveKit dashboard to test your agent.",
    }


# NOTE: the /agents/{agent_id}/web-call-token route lived here previously but was
# a stripped-down duplicate — it never created the LiveKit room WITH metadata and
# never dispatched the Pipecat worker, so the agent couldn't identify the
# tenant/agent or even join. The authoritative implementation is in
# backend/routers/web_calls.py (creates the room + metadata, dispatches the
# worker via RoomAgentDispatch, supports test_mode). This duplicate was removed
# so that complete version is the one served.


# ── GET /agents/{agent_id}/call-logs (real CallRecord) ───────────────────────

@router.get("/agents/{agent_id}/call-logs")
async def agent_call_logs(agent_id: str, limit: int = 50, user: CurrentUser = None) -> list[dict]:
    try:
        from backend.models.call_record import CallRecord
        async with async_session() as session:
            result = await session.execute(
                select(AgentConfig).where(AgentConfig.id == agent_id)
            )
            agent = result.scalar_one_or_none()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            user.require_owns(str(agent.tenant_id))

            cr_result = await session.execute(
                select(CallRecord)
                .where(CallRecord.agent_id == agent_id)
                .order_by(CallRecord.created_at.desc())
                .limit(limit)
            )
            calls = cr_result.scalars().all()

            return [
                {
                    "id": c.id,
                    "call_type": c.call_type,
                    "patient_number_masked": c.patient_number_masked,
                    "started_at": c.started_at.isoformat() if c.started_at else None,
                    "ended_at": c.ended_at.isoformat() if c.ended_at else None,
                    "duration_seconds": c.duration_seconds,
                    "status": c.status,
                    "outcome": c.outcome,
                    "avg_latency_ms": c.avg_latency_ms,
                    "turn_count": c.turn_count,
                    "sentiment": c.sentiment,
                    "summary": c.summary,
                    "intent_detected": c.intent_detected,
                    "booking_successful": c.booking_successful,
                    "detected_language": c.detected_language,
                    "transcript": c.transcript or [],
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                }
                for c in calls
            ]
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error fetching call logs for agent %s: %s", agent_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /agents/{agent_id}/call-records/{call_id}/evaluate ───────────────────

@router.post("/agents/{agent_id}/call-records/{call_id}/evaluate")
async def evaluate_call_record(agent_id: str, call_id: str, user: CurrentUser = None) -> dict:
    """Trigger Gemini post-call evaluation for a specific call record."""
    try:
        async with async_session() as session:
            agent_res = await session.execute(select(AgentConfig).where(AgentConfig.id == agent_id))
            agent = agent_res.scalar_one_or_none()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            user.require_owns(str(agent.tenant_id))

        from backend.services.call_evaluator import evaluate_call
        async with async_session() as session:
            result = await evaluate_call(call_id, session)
        if not result:
            raise HTTPException(status_code=404, detail="Call record not found or has no transcript")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Evaluation error for call %s: %s", call_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /agents/{agent_id}/health ─────────────────────────────────────────────

@router.get("/agents/{agent_id}/health")
async def agent_health(agent_id: str, user: CurrentUser = None) -> dict:
    """
    Agent health dashboard data.
    Returns latency, evaluation stats, and recent call summary.
    """
    try:
        from backend.models.call_record import CallRecord
        from backend.services.call_evaluator import get_agent_evaluation_stats
        from datetime import datetime, timedelta
        from sqlalchemy import and_

        async with async_session() as session:
            # Verify agent exists
            agent_res = await session.execute(
                select(AgentConfig).where(AgentConfig.id == agent_id)
            )
            agent = agent_res.scalar_one_or_none()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            user.require_owns(str(agent.tenant_id))

            since_24h = datetime.utcnow() - timedelta(hours=24)
            since_7d = datetime.utcnow() - timedelta(days=7)

            # 24h call counts
            r24 = await session.execute(
                select(CallRecord).where(
                    and_(CallRecord.agent_id == agent_id, CallRecord.created_at >= since_24h)
                )
            )
            calls_24h = r24.scalars().all()

            total_24h = len(calls_24h)
            successful_24h = sum(1 for c in calls_24h if c.status == "completed")
            failed_24h = sum(1 for c in calls_24h if c.status == "failed")
            transferred_24h = sum(1 for c in calls_24h if c.outcome == "transferred")

            # Latency data
            latency_calls = [
                c.avg_latency_ms for c in calls_24h
                if c.avg_latency_ms is not None
            ]
            avg_latency = round(sum(latency_calls) / len(latency_calls)) if latency_calls else None

            # Eval stats (7 days)
            eval_stats = await get_agent_evaluation_stats(agent_id, session, days=7)

            # Status determination
            status = "healthy"
            if total_24h > 0 and failed_24h / total_24h > 0.3:
                status = "degraded"
            elif avg_latency and avg_latency > 2000:
                status = "slow"

            return {
                "agent_id": agent_id,
                "agent_name": agent.agent_name,
                "status": status,
                "last_24h": {
                    "total_calls": total_24h,
                    "successful": successful_24h,
                    "failed": failed_24h,
                    "transferred": transferred_24h,
                },
                "latency": {
                    "avg_ms": avg_latency,
                    "target_ms": 800,
                    "on_target": avg_latency is None or avg_latency <= 800,
                    "sample_size": len(latency_calls),
                },
                "evaluation_stats_7d": eval_stats,
                "simulation_score": None,  # populated by frontend after simulation run
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Agent health error for %s: %s", agent_id, e)
        raise HTTPException(status_code=500, detail=str(e))
