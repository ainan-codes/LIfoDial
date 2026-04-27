"""
backend/models/bulk_call.py — Bulk Call Campaign model.
Tracks outbound reminder/follow-up call campaigns per tenant.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, JSON, ForeignKey, Text, text
from sqlalchemy.sql import func
from backend.db import Base


class BulkCallCampaign(Base):
    __tablename__ = "bulk_call_campaigns"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.id"), nullable=False, index=True)
    agent_id = Column(String(36), ForeignKey("agent_configs.id"), nullable=True)

    # Campaign metadata
    name = Column(String(200), nullable=False)
    purpose = Column(String(50), default="reminder")
    # reminder / follow_up / announcement / reactivation

    # Status lifecycle
    status = Column(String(20), default="pending")
    # pending / running / completed / cancelled / paused

    # Progress counters
    total_contacts = Column(Integer, default=0)
    calls_made = Column(Integer, default=0)
    calls_answered = Column(Integer, default=0)
    calls_successful = Column(Integer, default=0)
    calls_failed = Column(Integer, default=0)
    calls_no_answer = Column(Integer, default=0)

    # Scheduling
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Contacts list — [{phone, name, appointment_id, custom_data}]
    contacts = Column(JSON, default=list)

    # Message template (for SIP/SMS campaigns)
    message_template = Column(Text, nullable=True)

    # Notes / error summary
    notes = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=lambda: datetime.now(timezone.utc)
    )
