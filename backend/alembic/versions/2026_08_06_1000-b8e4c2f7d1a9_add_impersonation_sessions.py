"""add impersonation_sessions (superadmin "view as this clinic")

Revision ID: b8e4c2f7d1a9
Revises: c7d1e9f2a3b4
Create Date: 2026-08-06 10:00:00.000000+00:00

One row per superadmin session opened against a clinic's own admin dashboard.
The row is both the per-clinic audit trail and the revocation list that
backend/auth.py checks on every impersonated request — see
backend/models/impersonation_session.py.

Note this table is ALSO created by init_db()'s create_all() at startup (the model
is registered in db.py::_import_all_models), which is what actually provisions it
on deploys, since this project's migrations are not run automatically. This
revision exists so the schema is reproducible from Alembic alone, and so the
Postgres-only bits create_all() would miss — the RLS default-deny applied to
audit_logs, and ON DELETE CASCADE on tenant_id — are recorded somewhere.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'b8e4c2f7d1a9'
down_revision: Union[str, None] = 'c7d1e9f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("""
            CREATE TABLE IF NOT EXISTS impersonation_sessions (
                id VARCHAR(36) PRIMARY KEY,
                actor VARCHAR(120) NOT NULL,
                tenant_id VARCHAR(36) NOT NULL
                    REFERENCES tenants(id) ON DELETE CASCADE,
                started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at TIMESTAMPTZ NOT NULL,
                ended_at TIMESTAMPTZ,
                ended_reason VARCHAR(20)
            )
        """)
        # tenant_id: the per-clinic trail query. started_at: the newest-first ordering.
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_impersonation_sessions_tenant_id "
            "ON impersonation_sessions (tenant_id)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_impersonation_sessions_started_at "
            "ON impersonation_sessions (started_at)"
        )
        # Same posture as audit_logs: enable RLS with no policies, so anon/
        # authenticated Supabase roles are default-denied and only the service
        # role (the backend) can read a session row. A client that could read this
        # table could enumerate live session ids.
        op.execute("ALTER TABLE impersonation_sessions ENABLE ROW LEVEL SECURITY")
    else:
        import contextlib

        import sqlalchemy as sa

        with contextlib.suppress(Exception):
            op.create_table(
                'impersonation_sessions',
                sa.Column('id', sa.String(36), primary_key=True),
                sa.Column('actor', sa.String(120), nullable=False),
                sa.Column(
                    'tenant_id',
                    sa.String(36),
                    sa.ForeignKey('tenants.id', ondelete='CASCADE'),
                    nullable=False,
                    index=True,
                ),
                sa.Column('started_at', sa.DateTime(timezone=True), nullable=False,
                          server_default=sa.func.now(), index=True),
                sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
                sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
                sa.Column('ended_reason', sa.String(20), nullable=True),
            )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS impersonation_sessions")
