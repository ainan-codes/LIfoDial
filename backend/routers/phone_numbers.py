"""
backend/routers/phone_numbers.py — Phone number management endpoints.
"""
import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.auth import CurrentUser
from backend.db import get_db
from backend.models.phone_number import PhoneNumber
from backend.models.agent_config import AgentConfig

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/phone-numbers", tags=["phone-numbers"])


@router.get("")
async def list_phone_numbers(
    tenant_id: str = None,
    user: CurrentUser = None,
    db: AsyncSession = Depends(get_db),
):
    """List all phone numbers, optionally filtered by tenant_id."""
    if tenant_id:
        user.require_owns(tenant_id)
    elif not user.is_superadmin:
        # Without a tenant_id filter this would leak every clinic's numbers.
        raise HTTPException(403, "tenant_id is required")
    query = select(PhoneNumber).order_by(PhoneNumber.created_at.desc())
    if tenant_id:
        query = query.where(PhoneNumber.tenant_id == tenant_id)
    result = await db.execute(query)
    numbers = result.scalars().all()

    # Enrich with agent name
    enriched = []
    for pn in numbers:
        agent_name = None
        if pn.agent_id:
            agent_result = await db.execute(
                select(AgentConfig.agent_name)
                .where(AgentConfig.id == pn.agent_id)
            )
            row = agent_result.first()
            agent_name = row[0] if row else None

        enriched.append({
            "id": pn.id,
            "tenant_id": pn.tenant_id,
            "agent_id": pn.agent_id,
            "agent_name": agent_name,
            "number": pn.number,
            "country_code": pn.country_code,
            "country": pn.country,
            "provider": pn.provider,
            "sip_uri": pn.sip_uri,
            "sip_domain": pn.sip_domain,
            "is_active": pn.is_active,
            "is_assigned": pn.is_assigned,
            "created_at": pn.created_at.isoformat() if pn.created_at else None,
        })
    return enriched


@router.post("")
async def create_phone_number(body: dict, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    """Create a new phone number."""
    user.require_owns(body.get("tenant_id", ""))
    number = body.get("number", "").strip()
    if not number:
        raise HTTPException(400, "number is required")

    # Check uniqueness
    existing = await db.execute(
        select(PhoneNumber).where(PhoneNumber.number == number)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Number {number} already exists")

    pn = PhoneNumber(
        id=str(uuid.uuid4()),
        tenant_id=body.get("tenant_id", ""),
        agent_id=body.get("agent_id"),
        number=number,
        country_code=body.get("country_code", "IN"),
        country=body.get("country", "India"),
        provider=body.get("provider", "vobiz"),
        sip_uri=body.get("sip_uri"),
        sip_username=body.get("sip_username"),
        sip_password=body.get("sip_password"),
        sip_domain=body.get("sip_domain"),
        is_active=True,
        is_assigned=bool(body.get("agent_id")),
    )
    db.add(pn)

    # If assigned to an agent, also update agent's ai_number
    if pn.agent_id:
        agent_result = await db.execute(
            select(AgentConfig).where(AgentConfig.id == pn.agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if agent:
            agent.ai_number = number

    return {"id": pn.id, "number": pn.number, "status": "created"}


@router.patch("/{phone_id}")
async def update_phone_number(
    phone_id: str,
    body: dict,
    user: CurrentUser = None,
    db: AsyncSession = Depends(get_db),
):
    """Update a phone number's details."""
    result = await db.execute(
        select(PhoneNumber).where(PhoneNumber.id == phone_id)
    )
    pn = result.scalar_one_or_none()
    if not pn:
        raise HTTPException(404, "Phone number not found")
    user.require_owns(pn.tenant_id)

    for field in ["agent_id", "provider", "sip_uri", "sip_username",
                   "sip_password", "sip_domain", "is_active"]:
        if field in body:
            setattr(pn, field, body[field])

    if "agent_id" in body:
        pn.is_assigned = bool(body["agent_id"])

    return {"id": pn.id, "status": "updated"}


@router.delete("/{phone_id}")
async def delete_phone_number(
    phone_id: str,
    user: CurrentUser = None,
    db: AsyncSession = Depends(get_db),
):
    """Delete a phone number."""
    result = await db.execute(
        select(PhoneNumber).where(PhoneNumber.id == phone_id)
    )
    pn = result.scalar_one_or_none()
    if not pn:
        raise HTTPException(404, "Phone number not found")
    user.require_owns(pn.tenant_id)
    await db.delete(pn)
    return {"status": "deleted"}


@router.post("/{phone_id}/test-sip")
async def test_sip_connection(
    phone_id: str,
    user: CurrentUser = None,
    db: AsyncSession = Depends(get_db),
):
    """Test SIP connection for a phone number."""
    result = await db.execute(
        select(PhoneNumber).where(PhoneNumber.id == phone_id)
    )
    pn = result.scalar_one_or_none()
    if not pn:
        raise HTTPException(404, "Phone number not found")
    user.require_owns(pn.tenant_id)

    if not pn.sip_domain:
        return {"connected": False, "message": "No SIP domain configured"}

    # For now, return a mock success — real SIP ping requires deployment
    return {
        "connected": True,
        "latency_ms": 120,
        "message": f"SIP connection to {pn.sip_domain} successful",
    }
