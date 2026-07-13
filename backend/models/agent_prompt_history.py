"""
backend/models/agent_prompt_history.py
Lightweight edit history for AgentConfig.system_prompt / first_message —
lets a superadmin revert a fat-fingered prompt edit. Callers are
responsible for trimming to the last N entries per (agent_id, field_name).
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, String, Text, func
from backend.db import Base


class AgentPromptHistory(Base):
    __tablename__ = "agent_prompt_history"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(
        String(36),
        ForeignKey("agent_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field_name = Column(String(20), nullable=False)  # 'system_prompt' | 'first_message'
    value = Column(Text, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
