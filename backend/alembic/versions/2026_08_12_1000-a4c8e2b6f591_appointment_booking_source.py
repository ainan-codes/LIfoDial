"""appointments.source — which channel booked each appointment

Revision ID: a4c8e2b6f591
Revises: f3a7c9e1b4d2
Create Date: 2026-08-12 10:00:00.000000+00:00

Both dashboards claimed every appointment came from the voice agent — the clinic
Appointments table hardcoded a "Voice" badge (frontend/src/pages/Appointments.tsx)
and the superadmin view hardcoded ``"channel": "AI Call"``
(backend/routers/admin.py). On 2026-08-12 that was wrong about every row in
production: all three appointments had come from the chat/embed channel, and no
voice call had ever produced one.

This adds the column that makes the claim real. Existing rows are backfilled
from the only evidence they carry: ``call_id`` is set exclusively by the voice
pipeline's committer (booking_processor -> his.create_appointment), so

    call_id IS NOT NULL  ->  'voice'
    call_id IS NULL      ->  'chat'

New rows get their channel from the writer (see backend/models/appointment.py
for the vocabulary). The column stays NULLABLE with no server default on
purpose: a writer that does not say where a booking came from must surface as
"Unknown" in the dashboards rather than be silently attributed to a channel it
may not have come from.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a4c8e2b6f591'
down_revision: Union[str, None] = 'f3a7c9e1b4d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("appointments")}

    # Idempotent: this project's Postgres has been hand-patched before, so a
    # column may already exist (see 2026_04_26 add_tenant_missing_columns).
    if "source" not in columns:
        op.add_column("appointments", sa.Column("source", sa.String(length=20), nullable=True))

    indexes = {i["name"] for i in inspector.get_indexes("appointments")}
    if "ix_appointments_source" not in indexes:
        op.create_index("ix_appointments_source", "appointments", ["source"])

    # Backfill from call_id — the voice committer is the only writer that ever
    # set it. Only rows with no channel yet are touched, so re-running is safe.
    bind.execute(sa.text("""
        UPDATE appointments
           SET source = CASE WHEN call_id IS NOT NULL THEN 'voice' ELSE 'chat' END
         WHERE source IS NULL
    """))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    indexes = {i["name"] for i in inspector.get_indexes("appointments")}
    if "ix_appointments_source" in indexes:
        op.drop_index("ix_appointments_source", table_name="appointments")

    columns = {c["name"] for c in inspector.get_columns("appointments")}
    if "source" in columns:
        op.drop_column("appointments", "source")
