"""
backend/routers/voice.py — Webhook entry point for new incoming calls.
"""
import uuid
import logging
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db import get_db
from backend.models.tenant import Tenant
from backend.models.call_log import CallLog
from backend.redis_client import save_session

logger = logging.getLogger(__name__)
router = APIRouter()

class VoiceIncomingRequest(BaseModel):
    called_number: str
    from_number: str
    call_id: Optional[str] = None

class VoiceIncomingResponse(BaseModel):
    status: str
    call_id: str

@router.post("/incoming", response_model=VoiceIncomingResponse)
async def incoming_call(payload: VoiceIncomingRequest, db: AsyncSession = Depends(get_db)):
    """
    Invoked when a new call hits the Vobiz/Twilio number.
    Resolves the tenant, creates a CallLog, and sets up the strict Redis session context.
    """
    logger.info(f"Incoming call: {payload.from_number} -> {payload.called_number}")
    
    # 1. Resolve Tenant
    # For testing, if called_number is our dummy or not provided accurately, fallback to first active
    stmt = select(Tenant).where(
        (Tenant.ai_number == payload.called_number) | 
        (Tenant.forwarding_number == payload.called_number)
    )
    result = await db.execute(stmt)
    tenant = result.scalar_one_or_none()
    
    if not tenant:
        # SECURITY: never attach an unmatched inbound number to an arbitrary
        # tenant in production — that cross-wires one clinic's call into another
        # clinic's account. The "any active tenant" fallback is dev/demo only.
        from backend.config import settings
        if settings.environment.lower() == "production":
            logger.warning(
                "No tenant matches called_number %s — rejecting call (no fallback in production).",
                payload.called_number,
            )
        else:
            logger.warning(f"No exact match for {payload.called_number}. Dev fallback to any active tenant.")
            result = await db.execute(select(Tenant).where(Tenant.is_active == True).limit(1))
            tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(status_code=404, detail="No active tenant found to route this call.")
        
    call_id = payload.call_id or f"lk_room_{uuid.uuid4().hex[:12]}"
    
    # 2. Create CallLog entry
    call_log = CallLog(
        tenant_id=tenant.id,
        call_id=call_id,
        status="in_progress",
        duration_secs=0,
        outcome=None,
        transcript_json={}
    )
    db.add(call_log)
    await db.commit()
    
    # 3. Create strictly-namespaced Redis Session 
    session_data = {
        "call_id": call_id,
        "tenant_id": str(tenant.id),
        "detected_lang": tenant.language,
        "clinic_voice_id": tenant.custom_voice_id,
        "turn_count": 0,
        "context": {
            "patient_phone": payload.from_number,
            "pending_booking": None,
            "confirmed_booking": None,
            "history": []
        }
    }
    
    await save_session(str(tenant.id), call_id, session_data)
    logger.info(f"Created session strictly for tenant_id: {tenant.id} in room: {call_id}")
    
    return VoiceIncomingResponse(status="ok", call_id=call_id)
