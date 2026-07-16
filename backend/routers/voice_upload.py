"""
backend/routers/voice_upload.py — Upload custom voice samples and check status.
"""
import uuid
import logging
from pathlib import Path
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import CurrentUser
from backend.db import get_db
from backend.models.tenant import Tenant
from backend.agent.sarvam import clone_voice

logger = logging.getLogger(__name__)
router = APIRouter()

UPLOADS_DIR = Path("uploads/voice_samples")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/{id}/upload-voice", status_code=status.HTTP_201_CREATED)
async def upload_voice_sample(
    # str, not uuid.UUID — tenant ids are varchar(36) in the DB ("varchar = uuid" 500)
    id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = None,
):
    """
    Upload a custom voice sample (WAV/MP3) for a tenant.
    Saves the file and marks voice_status = 'processing'.
    """
    result = await db.execute(select(Tenant).where(Tenant.id == id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    user.require_owns(str(tenant.id))

    # Validate file type
    allowed = {"audio/wav", "audio/mpeg", "audio/mp3", "audio/x-wav", "audio/mp4"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file.content_type}. Use WAV or MP3.")

    # Save file
    safe_filename = f"{id}_{uuid.uuid4().hex[:8]}.voice"
    save_path = UPLOADS_DIR / safe_filename
    content = await file.read()
    save_path.write_bytes(content)
    logger.info(f"Voice sample saved for tenant {id}: {save_path}")

    # Upload to Sarvam voice cloning API
    voice_id = await clone_voice(content)

    # Update tenant status
    tenant.custom_voice_id = voice_id
    await db.commit()

    return {
        "status": "active",
        "voice_id": voice_id,
        "message": "Voice sample successfully cloned and is now active."
    }


@router.get("/{id}/voice-status")
async def get_voice_status(id: str, db: AsyncSession = Depends(get_db), user: CurrentUser = None):
    """
    Returns the processing status of an uploaded voice sample for a tenant.
    """
    result = await db.execute(select(Tenant).where(Tenant.id == id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    user.require_owns(str(tenant.id))

    if not tenant.custom_voice_id:
        return {"status": "default", "message": "Using default AI voice."}

    return {
        "status": "custom",
        "voice_id": tenant.custom_voice_id,
        "message": "Custom voice is active."
    }
