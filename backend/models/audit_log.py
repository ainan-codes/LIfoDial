"""
backend/models/audit_log.py
Queryable audit trail for sensitive admin actions (provider key add/update/
delete, LiveKit credential changes). NEVER stores the key/secret value itself —
only who/what/when.
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, Text, func
from backend.db import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor = Column(String(120), nullable=False)        # subject from the JWT (e.g. "superadmin")
    action = Column(String(40), nullable=False)        # key.create | key.update | key.delete | livekit.update | render.push
    target = Column(String(120), nullable=True)        # provider/category or resource id — never a secret
    detail = Column(Text, nullable=True)               # short human note; MUST NOT contain key material
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )
