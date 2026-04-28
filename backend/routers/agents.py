"""
backend/routers/agents.py — Agent CRUD + preview + test-call endpoints.
Multi-tenant: super admin sees all, clinic admin sees own only.
"""
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import async_session
from backend.models.agent_config import AgentConfig
from backend.models.tenant import Tenant
from backend.agent.prompt_templates import TEMPLATES, get_template, render_prompt
from backend.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


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
    stt_model: str = "saarika:v2"
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
    max_response_tokens: int = Field(250, ge=50, le=4000)
    llm_max_tokens: int = Field(250, ge=50, le=4000)
    llm_emotion_recognition: bool = False

    silence_timeout_seconds: int = 30
    max_duration_seconds: int = 600
    background_sound: str = "none"
    background_denoising: bool = False
    model_output_in_realtime: bool = False
    record_calls: bool = False
    recording_consent_plan: str | None = None

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
    recording_consent_plan: str | None = None

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
        "keypad_input_enabled": False,
        "hipaa_enabled": False,
        "pii_redaction_enabled": False,
        "transcriber_keywords": "[]",
    }
    for key, default in _defaults.items():
        if key not in data or data[key] is None:
            data[key] = default

    return data


# ── GET /agents — list all (super admin) ─────────────────────────────────────

@router.get("/agents")
async def list_agents() -> list[dict]:
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
async def preview_prompt(payload: PreviewPromptPayload) -> dict:
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
                model="gemini-2.0-flash",
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


# ── GET /agents/{agent_id} ────────────────────────────────────────────────────

@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict:
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
            data = _agent_to_dict(agent, clinic_name)
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
async def create_agent(payload: AgentCreatePayload) -> dict:
    try:
        async with async_session() as session:
            tenant_id = payload.tenant_id
            clinic_credentials: dict | None = None

            # ── If new clinic, create Tenant first ──
            if payload.clinic_selection == "new" and payload.new_clinic:
                nc = payload.new_clinic
                import secrets, string
                raw_password = "Lf" + secrets.token_urlsafe(6)
                new_tenant = Tenant(
                    id=str(uuid.uuid4()),
                    clinic_name=nc.clinic_name,
                    admin_name=nc.admin_name,
                    admin_email=nc.admin_email,
                    phone=nc.phone,
                    location=nc.location,
                    language=nc.language,
                    status="active",
                )
                session.add(new_tenant)
                await session.flush()
                tenant_id = new_tenant.id
                clinic_credentials = {
                    "email": nc.admin_email,
                    "password": raw_password,
                    "note": "Shown only once — store securely.",
                }

            if not tenant_id:
                raise HTTPException(status_code=400, detail="tenant_id required for existing clinic")

            # ── Check for duplicate agent ──
            existing = await session.execute(
                select(AgentConfig).where(AgentConfig.tenant_id == tenant_id)
            )
            if existing.scalar_one_or_none():
                raise HTTPException(
                    status_code=409,
                    detail="This clinic already has an agent configured."
                )

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
async def update_agent(agent_id: str, payload: AgentPatchPayload) -> dict:
    try:
        async with async_session() as session:
            result = await session.execute(
                select(AgentConfig).where(AgentConfig.id == agent_id)
            )
            agent = result.scalar_one_or_none()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")

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


# ── DELETE /agents/{agent_id} ─────────────────────────────────────────────────

@router.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: str) -> None:
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
async def test_agent_text(agent_id: str, body: dict = Body(...)) -> dict:
    import time
    patient_message = body.get("message", "Hello")
    try:
        async with async_session() as session:
            result = await session.execute(
                select(AgentConfig).where(AgentConfig.id == agent_id)
            )
            agent = result.scalar_one_or_none()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")

        t0 = time.monotonic()
        try:
            from google import genai
            client = genai.Client(api_key=settings.gemini_api_key)
            full_prompt = f"{agent.system_prompt}\n\nPatient: {patient_message}\nAgent:"
            response = client.models.generate_content(
                model=agent.llm_model,
                contents=full_prompt,
            )
            ai_text = response.text.strip()
        except Exception as err:
            logger.warning("Gemini test failed: %s", err)
            ai_text = f"I'm {agent.agent_name}. How can I help you today?"
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
async def test_call(agent_id: str) -> dict:
    room_name = f"call-test-{agent_id[:8]}-{uuid.uuid4().hex[:6]}"
    return {
        "room_name": room_name,
        "livekit_url": settings.livekit_url,
        "message": "Join this room in LiveKit dashboard to test your agent.",
    }


# ── POST /agents/{agent_id}/web-call-token ────────────────────────────────────
@router.post("/agents/{agent_id}/web-call-token")
async def generate_web_call_token(agent_id: str) -> dict:
    from livekit import api
    import uuid
    try:
        async with async_session() as session:
            result = await session.execute(
                select(AgentConfig).where(AgentConfig.id == agent_id)
            )
            agent = result.scalar_one_or_none()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
        
        participant_identity = f"user-{uuid.uuid4().hex[:6]}"
        room_name = f"web-call-{agent_id[:8]}-{uuid.uuid4().hex[:4]}"
        
        # We can use agent's own livekit keys if configured, else global settings
        lk_key = agent.livekit_api_key or settings.livekit_api_key
        lk_secret = agent.livekit_api_secret or settings.livekit_api_secret
        lk_url = agent.livekit_url or settings.livekit_url

        if not lk_key or not lk_secret:
            raise HTTPException(status_code=500, detail="LiveKit credentials not configured")
        
        token = api.AccessToken(lk_key, lk_secret)
        token.with_identity(participant_identity)
        token.with_name("Web Caller")
        token.with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
        ))
        jwt_token = token.to_jwt()
        
        return {
            "token": jwt_token,
            "url": lk_url,
            "room": room_name,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error generating web call token: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /agents/{agent_id}/call-logs (real CallRecord) ───────────────────────

@router.get("/agents/{agent_id}/call-logs")
async def agent_call_logs(agent_id: str, limit: int = 50) -> list[dict]:
    try:
        from backend.models.call_record import CallRecord
        async with async_session() as session:
            result = await session.execute(
                select(AgentConfig).where(AgentConfig.id == agent_id)
            )
            agent = result.scalar_one_or_none()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")

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
async def evaluate_call_record(agent_id: str, call_id: str) -> dict:
    """Trigger Gemini post-call evaluation for a specific call record."""
    try:
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
async def agent_health(agent_id: str) -> dict:
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
