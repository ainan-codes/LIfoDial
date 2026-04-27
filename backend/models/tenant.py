import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.db import Base

def _now() -> datetime:
    return datetime.now(timezone.utc)

class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False,
    )

    clinic_name: Mapped[str] = mapped_column(String(255), nullable=False)
    admin_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    admin_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    
    # Store plain credentials (dev only as per prompt show-once requirement)
    admin_password: Mapped[str | None] = mapped_column(String(100), nullable=True)

    ai_number: Mapped[str | None] = mapped_column(String(30), nullable=True, unique=True)
    forwarding_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en-IN")
    plan: Mapped[str] = mapped_column(String(20), nullable=False, default="Free")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP")
    )

    # Relationships
    doctors: Mapped[list["Doctor"]] = relationship("Doctor", back_populates="tenant", cascade="all, delete-orphan")
    appointments: Mapped[list["Appointment"]] = relationship("Appointment", back_populates="tenant", cascade="all, delete-orphan")
    call_logs: Mapped[list["CallLog"]] = relationship("CallLog", back_populates="tenant", cascade="all, delete-orphan")
    agent_config: Mapped["AgentConfig | None"] = relationship("AgentConfig", back_populates="tenant", uselist=False, cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Tenant id={self.id} clinic={self.clinic_name!r}>"
