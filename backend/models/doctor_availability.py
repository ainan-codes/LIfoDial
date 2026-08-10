import uuid
from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, SmallInteger, String, Time, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.db import Base


class DoctorAvailability(Base):
    """A doctor's recurring weekly working-hours window (IST wall-clock).

    One row per open window per day — a doctor with no rows for a given
    day_of_week is treated as not bookable that day (silence is not "always
    open"). Date-specific holiday/time-off overrides are intentionally not
    modeled here; Doctor.is_available/leave_reason already covers "off today"
    at the granularity the product needs.
    """

    __tablename__ = "doctor_availability"
    __table_args__ = (
        CheckConstraint("start_time < end_time", name="ck_doctor_availability_start_before_end"),
        CheckConstraint("day_of_week >= 0 AND day_of_week <= 6", name="ck_doctor_availability_dow_range"),
        Index("ix_doctor_availability_doctor_day", "doctor_id", "day_of_week"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4()), nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    doctor_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    # 0=Monday .. 6=Sunday — matches Python's date.weekday().
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_time: Mapped[object] = mapped_column(Time, nullable=False)
    end_time: Mapped[object] = mapped_column(Time, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("CURRENT_TIMESTAMP"),
    )

    tenant: Mapped["Tenant"] = relationship("Tenant")
    doctor: Mapped["Doctor"] = relationship("Doctor", back_populates="availability_windows")

    def __repr__(self) -> str:
        return (
            f"<DoctorAvailability doctor={self.doctor_id} day={self.day_of_week} "
            f"{self.start_time}-{self.end_time}>"
        )
