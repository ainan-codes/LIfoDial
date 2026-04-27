import uuid
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, JSON, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.db import Base

def _now() -> datetime:
    return datetime.now(timezone.utc)

class CallLog(Base):
    __tablename__ = "call_logs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False,
    )

    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    call_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    caller_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    duration_secs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(
        String(30), nullable=False, default="answered", index=True
    )
    detected_lang: Mapped[str | None] = mapped_column(String(10), nullable=True)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transcript_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP")
    )

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="call_logs")

    def __repr__(self) -> str:
        return f"<CallLog id={self.id} call_id={self.call_id!r} outcome={self.outcome!r}>"
