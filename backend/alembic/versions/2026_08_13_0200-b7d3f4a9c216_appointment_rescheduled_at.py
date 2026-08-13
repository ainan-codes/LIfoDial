"""appointments.rescheduled_at — a distinct "Rescheduled" badge without touching status

Revision ID: b7d3f4a9c216
Revises: a4c8e2b6f591
Create Date: 2026-08-13 02:00:00.000000+00:00

The clinic dashboard shows CONFIRMED as green and CANCELLED as red, but had no
way to show "this one was moved" as its own colour (requested: blue) — a
reschedule keeps ``status='confirmed'``, by design (see
his.py::sync_appointment_to_db's docstring): the availability engine and every
existing "is this appointment active" query filters on
``status.in_(['pending','confirmed'])``, and widening that vocabulary to a third
status value would mean re-auditing every one of those call sites for a change
that is purely cosmetic.

This column is nullable with no default, set only the moment a RESCHEDULE
genuinely moves a row's ``slot_time`` (never on the no-op case where the caller
asked for the time they already had — that path already returns a distinct
reason, ``already_at_that_time``, so it is never mistaken for a real move here
either).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b7d3f4a9c216'
down_revision: Union[str, None] = 'a4c8e2b6f591'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("appointments")}

    if "rescheduled_at" not in columns:
        op.add_column(
            "appointments",
            sa.Column("rescheduled_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("appointments")}

    if "rescheduled_at" in columns:
        op.drop_column("appointments", "rescheduled_at")
