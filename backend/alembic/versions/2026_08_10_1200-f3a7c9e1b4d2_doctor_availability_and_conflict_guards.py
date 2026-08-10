"""doctor_availability table + duplicate/conflict-safety unique indexes

Revision ID: f3a7c9e1b4d2
Revises: b8e4c2f7d1a9
Create Date: 2026-08-10 12:00:00.000000+00:00

Closes two confirmed data-integrity gaps:

  1. Nothing stopped "Add Doctor" from creating a second row for a doctor
     that already has a his_doctor_id — three "Salman / HIS 002" duplicates
     existed in one clinic's live data (cleaned up via
     backend/scripts/find_duplicate_doctors.py before this migration was
     written). uq_doctors_tenant_his_id closes that at the DB level.
  2. Nothing stopped two concurrent callers from booking the same doctor at
     the same slot_time. uq_appointments_doctor_slot_active closes that —
     the losing insert gets a clean IntegrityError instead of a silent
     double-booking (see backend/services/his.py::create_appointment).

Also adds doctor_availability: a doctor's recurring weekly working-hours
windows, replacing the previously-nonexistent per-doctor schedule concept
(his.get_slots() was 100% hardcoded and never even called by the pipeline).

Both unique indexes FAIL LOUDLY over pre-existing violations rather than
silently mangling data — run backend/scripts/find_duplicate_doctors.py and
backend/scripts/find_appointment_slot_conflicts.py and resolve any hits
before applying this migration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f3a7c9e1b4d2'
down_revision: Union[str, None] = 'b8e4c2f7d1a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == 'postgresql':
        his_id_dupes = bind.execute(sa.text("""
            SELECT tenant_id, his_doctor_id, count(*) AS n
            FROM doctors WHERE his_doctor_id IS NOT NULL
            GROUP BY tenant_id, his_doctor_id HAVING count(*) > 1
        """)).fetchall()
        if his_id_dupes:
            listing = ", ".join(f"tenant={r.tenant_id} his_doctor_id={r.his_doctor_id} (x{r.n})" for r in his_id_dupes)
            raise RuntimeError(
                "Cannot add unique index uq_doctors_tenant_his_id: duplicate "
                f"doctors exist: {listing}. Resolve via "
                "backend/scripts/find_duplicate_doctors.py, then re-run."
            )

        slot_conflicts = bind.execute(sa.text("""
            SELECT doctor_id, slot_time, count(*) AS n
            FROM appointments WHERE status <> 'cancelled'
            GROUP BY doctor_id, slot_time HAVING count(*) > 1
        """)).fetchall()
        if slot_conflicts:
            listing = ", ".join(f"doctor={r.doctor_id} slot={r.slot_time} (x{r.n})" for r in slot_conflicts)
            raise RuntimeError(
                "Cannot add unique index uq_appointments_doctor_slot_active: "
                f"double-booked slots exist: {listing}. Resolve via "
                "backend/scripts/find_appointment_slot_conflicts.py, then re-run."
            )

        op.execute("""
            CREATE TABLE IF NOT EXISTS doctor_availability (
                id VARCHAR(36) PRIMARY KEY,
                tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                doctor_id VARCHAR(36) NOT NULL REFERENCES doctors(id) ON DELETE CASCADE,
                day_of_week SMALLINT NOT NULL,
                start_time TIME NOT NULL,
                end_time TIME NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT ck_doctor_availability_start_before_end CHECK (start_time < end_time),
                CONSTRAINT ck_doctor_availability_dow_range CHECK (day_of_week >= 0 AND day_of_week <= 6)
            )
        """)
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_doctor_availability_tenant_id "
            "ON doctor_availability (tenant_id)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_doctor_availability_doctor_day "
            "ON doctor_availability (doctor_id, day_of_week)"
        )
        op.execute("ALTER TABLE doctor_availability ENABLE ROW LEVEL SECURITY")

        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_doctors_tenant_his_id "
            "ON doctors (tenant_id, his_doctor_id) WHERE his_doctor_id IS NOT NULL"
        )
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_appointments_doctor_slot_active "
            "ON appointments (doctor_id, slot_time) WHERE status <> 'cancelled'"
        )
    else:
        # SQLite (local dev only) — best-effort, non-fatal; tests create a
        # fresh DB via create_all(), which already picks up the model-level
        # Index(..., sqlite_where=...) definitions directly.
        import contextlib

        with contextlib.suppress(Exception):
            op.create_table(
                'doctor_availability',
                sa.Column('id', sa.String(36), primary_key=True),
                sa.Column('tenant_id', sa.String(36), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True),
                sa.Column('doctor_id', sa.String(36), sa.ForeignKey('doctors.id', ondelete='CASCADE'), nullable=False, index=True),
                sa.Column('day_of_week', sa.SmallInteger, nullable=False),
                sa.Column('start_time', sa.Time, nullable=False),
                sa.Column('end_time', sa.Time, nullable=False),
                sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
                sa.CheckConstraint('start_time < end_time', name='ck_doctor_availability_start_before_end'),
                sa.CheckConstraint('day_of_week >= 0 AND day_of_week <= 6', name='ck_doctor_availability_dow_range'),
            )
            op.create_index('ix_doctor_availability_doctor_day', 'doctor_availability', ['doctor_id', 'day_of_week'])
        with contextlib.suppress(Exception):
            op.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_doctors_tenant_his_id "
                "ON doctors (tenant_id, his_doctor_id) WHERE his_doctor_id IS NOT NULL"
            )
        with contextlib.suppress(Exception):
            op.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_appointments_doctor_slot_active "
                "ON appointments (doctor_id, slot_time) WHERE status <> 'cancelled'"
            )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_appointments_doctor_slot_active")
    op.execute("DROP INDEX IF EXISTS uq_doctors_tenant_his_id")
    op.execute("DROP TABLE IF EXISTS doctor_availability")
