import uuid
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.db import Base

def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Booking channel vocabulary (Appointment.source) ───────────────────────────
#
# WHICH channel produced a booking is something the clinic has to be able to see
# — "the phone agent booked this" and "someone typed it into the website widget"
# are different facts about the clinic's day, and both dashboards used to assert
# the first one for every row regardless ("AI Voice" / "AI Call" were hardcoded
# in the UI). They were wrong about every row that existed: on 2026-08-12 all
# three real appointments had come from chat.
#
# The values are deliberately narrower than "voice or chat", because the product
# has two of each and the difference is visible to the clinic:
SOURCE_VOICE = "voice"           # inbound/outbound phone call — the LiveKit voice agent
SOURCE_WEB_VOICE = "web_voice"   # browser-mic call through the WebSocket widget
SOURCE_CHAT = "chat"             # text chat in the dashboard (agent test / API)
SOURCE_EMBED = "embed"           # text chat in the public website widget
SOURCE_DASHBOARD = "dashboard"   # entered by clinic staff by hand

#: Everything a writer is allowed to store. A value outside this set means a new
#: channel was added without teaching the dashboards about it, so his.py logs and
#: stores NULL ("Unknown") rather than inventing an attribution.
VALID_SOURCES = frozenset({
    SOURCE_VOICE, SOURCE_WEB_VOICE, SOURCE_CHAT, SOURCE_EMBED, SOURCE_DASHBOARD,
})


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        # A doctor can only have one ACTIVE (non-cancelled) appointment per
        # slot_time. This is the actual race-safety mechanism for concurrent
        # bookings: two simultaneous inserts for the same doctor+slot both
        # attempt the write, and the loser gets a clean IntegrityError instead
        # of silently creating a double-booking (see his.py::create_appointment).
        Index(
            "uq_appointments_doctor_slot_active", "doctor_id", "slot_time",
            unique=True,
            postgresql_where=text("status <> 'cancelled'"),
            sqlite_where=text("status <> 'cancelled'"),
        ),
        # Every list view orders by slot_time DESC and windows on it
        # (routers/admin.py::list_all_appointments,
        # routers/appointments.py::list_appointments). Nothing could serve that:
        # the unique index above leads with doctor_id, and tenant_id/status are
        # single-column, so both queries did a full scan plus an in-memory sort
        # on every dashboard load. Past ~8s that hits asyncpg's command_timeout
        # and the page 500s, which is the 2026-08-15 report.
        #
        # tenant_id first, then slot_time DESC: the clinic view filters on tenant
        # and orders within it (an index prefix serves that directly), while the
        # superadmin view crosses tenants and still gets the ordered slot_time
        # column for its date window.
        Index(
            "ix_appointments_tenant_slot_time",
            "tenant_id", text("slot_time DESC"),
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
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    #: Which channel booked this — one of VALID_SOURCES above. Nullable ON
    #: PURPOSE, with no default: a row whose writer did not say where it came
    #: from must read as "Unknown" in the dashboards, never be silently
    #: attributed to a channel it may not have come from. (The migration
    #: backfills existing rows from call_id, which is the only evidence those
    #: rows carry.)
    source: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    #: Set the moment a RESCHEDULE actually moves this row's slot_time (never on
    #: BOOK, never on a no-op reschedule that lands on the time it already held —
    #: see his.sync_appointment_to_db). Deliberately separate from `status`,
    #: which stays 'confirmed' either way: the availability engine and every
    #: existing "active appointment" query filters on status alone
    #: (status.in_(['pending','confirmed'])), and widening that vocabulary to a
    #: 'rescheduled' status would mean re-auditing every one of those call
    #: sites. This column exists purely so the dashboards can show a distinct
    #: "Rescheduled" badge without touching that logic at all.
    rescheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

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
