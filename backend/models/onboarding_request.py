"""
backend/models/onboarding_request.py
Database model for clinic onboarding (contact sales) submissions.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Text, text
from backend.db import Base


class OnboardingRequest(Base):
    __tablename__ = "onboarding_requests"

    id: str = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    clinic_name: str = Column(String, nullable=False)
    contact_name: str = Column(String, nullable=False)
    email: str = Column(String, nullable=False)
    phone: str = Column(String, nullable=False)
    plan: str = Column(String, default="Pro")          # Free | Pro | Enterprise
    location: str = Column(String, nullable=True)
    message: str = Column(Text, nullable=True)         # free-form message from form
    status: str = Column(String, default="Pending")    # Pending | Approved | Rejected
    note: str = Column(Text, nullable=True)            # internal rejection/approval note
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
