"""Index appointments for the list views' ORDER BY slot_time DESC.

Both appointment list endpoints order by ``slot_time DESC`` and the superadmin
one also windows on it:

    routers/admin.py::list_all_appointments        (all clinics)
    routers/appointments.py::list_appointments     (one clinic)

Nothing in the schema could serve that. ``uq_appointments_doctor_slot_active``
leads with ``doctor_id``, and ``tenant_id``/``status``/``source`` are separate
single-column indexes, so every dashboard load did a full scan of the table plus
an in-memory sort. That is fine at a hundred rows and fatal later: asyncpg is
configured with ``command_timeout=8`` (backend/db.py), so once the scan crosses
eight seconds the request raises and the page shows
"Could not load appointments". Observed live 2026-08-15 — a 500 on
/admin/appointments followed six seconds later by a 200 on a manual refresh,
i.e. a query sitting right on the timeout boundary.

Column order is ``(tenant_id, slot_time DESC)``: the clinic view filters on
tenant and orders within it, which an index prefix serves directly, and the
superadmin view crosses tenants but still gets slot_time pre-ordered for its
date window.

Revision ID: c9e5a1d7b304
Revises: b7d3f4a9c216
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c9e5a1d7b304'
down_revision: Union[str, None] = 'b7d3f4a9c216'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "ix_appointments_tenant_slot_time"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # CONCURRENTLY needs its own transaction — the table is live and this
        # index is being added precisely because reads on it are already slow.
        with op.get_context().autocommit_block():
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} "
                "ON appointments (tenant_id, slot_time DESC)"
            )
    else:
        op.create_index(
            INDEX_NAME, "appointments",
            ["tenant_id", sa.text("slot_time DESC")],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
    else:
        op.drop_index(INDEX_NAME, table_name="appointments")
