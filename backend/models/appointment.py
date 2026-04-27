import uuid
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.db import Base

def _now() -> datetime:
    return datetime.now(timezone.utc)

class Appointment(Base):
    __tablename__ = "appointments"

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

    doctor_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("doctors.id", ondelete="SET NULL"),
        nullable=False,
    )

    slot_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    patient_phone: Mapped[str] = mapped_column(String(30), nullable=False)
    patient_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )
    his_booking_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    call_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP")
    )

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="appointments")
    doctor: Mapped["Doctor"] = relationship("Doctor", back_populates="appointments")

    def __repr__(self) -> str:
        return f"<Appointment id={self.id} tenant={self.tenant_id} status={self.status!r}>"
