import uuid
from sqlalchemy import (
    Column, String, Integer, Float, JSON,
    DateTime, Boolean, Text, ForeignKey
)
from datetime import datetime, timezone
from sqlalchemy.sql import func
from backend.db import Base


class CallRecord(Base):
    __tablename__ = "call_records"

    id = Column(String(36), primary_key=True,
                default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(36), ForeignKey("tenants.id"),
                       nullable=False, index=True)
    agent_id = Column(String(36), ForeignKey("agent_configs.id"),
                      nullable=True)

    # Call metadata
    call_type = Column(String(20), default="inbound")
    # inbound / outbound / web

    # Participants
    patient_number = Column(String(25), nullable=True)
    patient_number_masked = Column(String(25), nullable=True)
    ai_number = Column(String(25), nullable=True)

    # LiveKit
    livekit_room_name = Column(String(100), nullable=True)
    livekit_room_id = Column(String(100), nullable=True)

    # Timing
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, nullable=True)

    # AI performance
    avg_latency_ms = Column(Float, nullable=True)
    turn_count = Column(Integer, default=0)

    # Outcome
    status = Column(String(30), default="in_progress")
    # in_progress / completed / failed / transferred / voicemail
    end_reason = Column(String(50), nullable=True)
    # customer-ended-call / assistant-ended-call /
    # silence-timed-out / max-duration-exceeded /
    # transfer-completed / error
    outcome = Column(String(30), nullable=True)
    # booked / cancelled / resolved / unresolved / transferred

    # Content
    transcript = Column(JSON, default=list)
    summary = Column(Text, nullable=True)

    # Post-call analysis
    sentiment = Column(String(20), nullable=True)
    intent_detected = Column(String(50), nullable=True)
    booking_successful = Column(Boolean, nullable=True)

    # Language
    detected_language = Column(String(10), nullable=True)

    # Recording
    recording_url = Column(String(500), nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now()
    )
