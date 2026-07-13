"""
backend/routers/bulk_calls.py — Bulk Call Campaign CRUD
POST /bulk-calls
GET  /bulk-calls?tenant_id=xxx
GET  /bulk-calls/{id}
PATCH /bulk-calls/{id}/cancel
"""
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, and_

from backend.auth import CurrentUser
from backend.db import async_session
from backend.models.bulk_call import BulkCallCampaign

logger = logging.getLogger(__name__)
router = APIRouter()


class ContactItem(BaseModel):
    phone: str
    name: str
    appointment_id: Optional[str] = None
    custom_data: Optional[dict] = None


class BulkCallCreatePayload(BaseModel):
    tenant_id: str
    agent_id: Optional[str] = None
    name: str
    purpose: str = "reminder"
    # reminder / follow_up / announcement
    contacts: list[ContactItem]
    scheduled_at: Optional[str] = None  # ISO datetime string or null for immediate
    message_template: Optional[str] = None


def _campaign_to_dict(c: BulkCallCampaign) -> dict:
    return {
        "id": c.id,
        "tenant_id": c.tenant_id,
        "agent_id": c.agent_id,
        "name": c.name,
        "purpose": c.purpose,
        "status": c.status,
        "total_contacts": c.total_contacts,
        "calls_made": c.calls_made,
        "calls_answered": c.calls_answered,
        "calls_successful": c.calls_successful,
        "calls_failed": c.calls_failed,
        "calls_no_answer": c.calls_no_answer,
        "scheduled_at": c.scheduled_at.isoformat() if c.scheduled_at else None,
        "started_at": c.started_at.isoformat() if c.started_at else None,
        "completed_at": c.completed_at.isoformat() if c.completed_at else None,
        "contacts_preview": (c.contacts or [])[:3],  # first 3 for preview
        "contacts_count": len(c.contacts or []),
        "message_template": c.message_template,
        "notes": c.notes,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "progress_pct": (
            round(c.calls_made / c.total_contacts * 100)
            if c.total_contacts and c.total_contacts > 0 else 0
        ),
    }


# ── POST /bulk-calls ───────────────────────────────────────────────────────────

@router.post("/bulk-calls", status_code=201)
async def create_campaign(payload: BulkCallCreatePayload, user: CurrentUser = None) -> dict:
    """Create a new bulk call campaign."""
    user.require_owns(payload.tenant_id)
    if not payload.contacts:
        raise HTTPException(status_code=400, detail="contacts list cannot be empty")

    scheduled = None
    if payload.scheduled_at:
        try:
            scheduled = datetime.fromisoformat(payload.scheduled_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid scheduled_at datetime format")

    contacts_data = [c.model_dump() for c in payload.contacts]

    try:
        async with async_session() as db:
            campaign = BulkCallCampaign(
                id=str(uuid.uuid4()),
                tenant_id=payload.tenant_id,
                agent_id=payload.agent_id,
                name=payload.name,
                purpose=payload.purpose,
                status="pending" if scheduled else "pending",
                total_contacts=len(contacts_data),
                contacts=contacts_data,
                scheduled_at=scheduled,
                message_template=payload.message_template,
            )
            db.add(campaign)
            await db.commit()
            await db.refresh(campaign)
            return _campaign_to_dict(campaign)
    except Exception as e:
        logger.exception("Error creating bulk campaign: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /bulk-calls ────────────────────────────────────────────────────────────

@router.get("/bulk-calls")
async def list_campaigns(tenant_id: Optional[str] = None, user: CurrentUser = None) -> list[dict]:
    """List all campaigns, optionally filtered by tenant_id."""
    if tenant_id:
        user.require_owns(tenant_id)
    elif not user.is_superadmin:
        # Clinic users never see other tenants' campaigns — force their own scope.
        tenant_id = user.tenant_id
    try:
        async with async_session() as db:
            query = select(BulkCallCampaign).order_by(BulkCallCampaign.created_at.desc())
            if tenant_id:
                query = query.where(BulkCallCampaign.tenant_id == tenant_id)
            result = await db.execute(query)
            campaigns = result.scalars().all()
            return [_campaign_to_dict(c) for c in campaigns]
    except Exception as e:
        logger.exception("Error listing bulk campaigns: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /bulk-calls/{id} ──────────────────────────────────────────────────────

@router.get("/bulk-calls/{campaign_id}")
async def get_campaign(campaign_id: str, user: CurrentUser = None) -> dict:
    """Get a single campaign with full contacts list."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(BulkCallCampaign).where(BulkCallCampaign.id == campaign_id)
            )
            c = result.scalar_one_or_none()
            if not c:
                raise HTTPException(status_code=404, detail="Campaign not found")
            user.require_owns(str(c.tenant_id))
            data = _campaign_to_dict(c)
            data["contacts"] = c.contacts  # full contacts list
            return data
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error fetching campaign %s: %s", campaign_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── PATCH /bulk-calls/{id}/cancel ─────────────────────────────────────────────

@router.patch("/bulk-calls/{campaign_id}/cancel")
async def cancel_campaign(campaign_id: str, user: CurrentUser = None) -> dict:
    """Cancel a pending or running campaign."""
    try:
        async with async_session() as db:
            result = await db.execute(
                select(BulkCallCampaign).where(BulkCallCampaign.id == campaign_id)
            )
            campaign = result.scalar_one_or_none()
            if not campaign:
                raise HTTPException(status_code=404, detail="Campaign not found")
            user.require_owns(str(campaign.tenant_id))
            if campaign.status in ("completed", "cancelled"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot cancel a campaign with status: {campaign.status}"
                )
            campaign.status = "cancelled"
            campaign.notes = (campaign.notes or "") + f"\nCancelled at {datetime.utcnow().isoformat()}"
            await db.commit()
            return {"id": campaign_id, "status": "cancelled"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error cancelling campaign %s: %s", campaign_id, e)
        raise HTTPException(status_code=500, detail=str(e))
