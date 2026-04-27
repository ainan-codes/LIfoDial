import uuid
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.db import Base

def _now() -> datetime:
    return datetime.now(timezone.utc)

class Doctor(Base):
    __tablename__ = "doctors"

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

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    specialization: Mapped[str] = mapped_column(String(255), nullable=False, default="General")
    his_doctor_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP")
    )

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="doctors")
    appointments: Mapped[list["Appointment"]] = relationship("Appointment", back_populates="doctor", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Doctor id={self.id} name={self.name!r} tenant={self.tenant_id}>"
