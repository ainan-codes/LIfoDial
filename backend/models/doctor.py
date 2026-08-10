import uuid
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.db import Base

def _now() -> datetime:
    return datetime.now(timezone.utc)

class Doctor(Base):
    __tablename__ = "doctors"
    __table_args__ = (
        # A his_doctor_id, when set, must be unique per clinic — this is the
        # guard that was missing and let repeated "Add Doctor" clicks create
        # silent duplicates (e.g. three "Salman / HIS 002" rows in one
        # clinic). Free-text name collisions are handled at the app level in
        # add_doctor() instead — two real doctors can legitimately share a
        # common name, so that can't be a hard DB constraint.
        Index(
            "uq_doctors_tenant_his_id", "tenant_id", "his_doctor_id",
            unique=True,
            postgresql_where=text("his_doctor_id IS NOT NULL"),
            sqlite_where=text("his_doctor_id IS NOT NULL"),
        ),
    )

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

    # Availability — surfaced to the voice agent's system prompt and the
    # booking flow so a caller is told clearly instead of being offered a
    # slot with a doctor who isn't actually seeing patients.
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    leave_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP")
    )

    tenant: Mapped["Tenant"] = relationship("Tenant", back_populates="doctors")
    appointments: Mapped[list["Appointment"]] = relationship("Appointment", back_populates="doctor", cascade="all, delete-orphan")
    availability_windows: Mapped[list["DoctorAvailability"]] = relationship(
        "DoctorAvailability", back_populates="doctor", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Doctor id={self.id} name={self.name!r} tenant={self.tenant_id}>"
