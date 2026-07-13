"""
backend/routers/knowledge_base.py
Per-clinic knowledge base CRUD (FAQs, hours, contacts, custom info).
"""
import uuid
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from backend.auth import CurrentUser
from backend.db import AsyncSessionLocal
from backend.models.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)
router = APIRouter()


async def get_db():
    async with AsyncSessionLocal() as s:
        yield s


class KBEntry(BaseModel):
    category: str       # faq | hours | contact | custom
    key: Optional[str] = None
    title: str
    content: str
    is_active: Optional[bool] = True


# ── GET /tenants/{tenant_id}/kb ───────────────────────────────────────────────
@router.get("/tenants/{tenant_id}/kb")
async def list_kb(tenant_id: str, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    user.require_owns(tenant_id)
    try:
        result = await db.execute(
            select(KnowledgeBase)
            .where(KnowledgeBase.tenant_id == tenant_id, KnowledgeBase.is_active == True)
            .order_by(KnowledgeBase.category, KnowledgeBase.created_at)
        )
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "tenant_id": r.tenant_id,
                "category": r.category,
                "key": r.key,
                "title": r.title,
                "content": r.content,
                "is_active": r.is_active,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]
    except Exception as e:
        logger.exception("Error listing KB for %s: %s", tenant_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /tenants/{tenant_id}/kb ─────────────────────────────────────────────────
@router.post("/tenants/{tenant_id}/kb", status_code=201)
async def create_kb_entry(tenant_id: str, data: KBEntry, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    user.require_owns(tenant_id)
    try:
        entry = KnowledgeBase(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            category=data.category,
            key=data.key,
            title=data.title,
            content=data.content,
            is_active=data.is_active if data.is_active is not None else True,
        )
        db.add(entry)
        await db.commit()
        await db.refresh(entry)
        return {"id": entry.id, "status": "created"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ── PATCH /tenants/{tenant_id}/kb/{entry_id} ─────────────────────────────────
@router.patch("/tenants/{tenant_id}/kb/{entry_id}")
async def update_kb_entry(tenant_id: str, entry_id: str, data: KBEntry, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    user.require_owns(tenant_id)
    try:
        result = await db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id == entry_id,
                KnowledgeBase.tenant_id == tenant_id,
            )
        )
        entry = result.scalar_one_or_none()
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")
        entry.category = data.category
        entry.key = data.key
        entry.title = data.title
        entry.content = data.content
        if data.is_active is not None:
            entry.is_active = data.is_active
        await db.commit()
        return {"id": entry.id, "status": "updated"}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ── DELETE /tenants/{tenant_id}/kb/{entry_id} ────────────────────────────────
@router.delete("/tenants/{tenant_id}/kb/{entry_id}", status_code=204)
async def delete_kb_entry(tenant_id: str, entry_id: str, user: CurrentUser = None, db: AsyncSession = Depends(get_db)):
    user.require_owns(tenant_id)
    try:
        result = await db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id == entry_id,
                KnowledgeBase.tenant_id == tenant_id,
            )
        )
        entry = result.scalar_one_or_none()
        if not entry:
            raise HTTPException(status_code=404, detail="Entry not found")
        await db.delete(entry)
        await db.commit()
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
