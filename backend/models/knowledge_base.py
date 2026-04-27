"""
backend/models/knowledge_base.py
Per-clinic knowledge base: FAQs, working hours, docs, custom info.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Boolean, text
from backend.db import Base


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: str = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: str = Column(String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    category: str = Column(String(50), nullable=False)  # faq | hours | contact | custom | doc
    key: str = Column(String(100), nullable=True)        # e.g. "working_hours", "emergency_number"
    title: str = Column(String(255), nullable=False)
    content: str = Column(Text, nullable=False)
    is_active: bool = Column(Boolean, default=True)
    created_at: datetime = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: datetime = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(timezone.utc)
    )
