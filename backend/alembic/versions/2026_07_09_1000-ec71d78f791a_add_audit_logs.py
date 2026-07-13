"""add audit_logs table (sensitive admin action trail)

Revision ID: ec71d78f791a
Revises: 9099592059e7
Create Date: 2026-07-09 10:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'ec71d78f791a'
down_revision: Union[str, None] = '9099592059e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id VARCHAR(36) PRIMARY KEY,
                actor VARCHAR(120) NOT NULL,
                action VARCHAR(40) NOT NULL,
                target VARCHAR(120),
                detail TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        op.execute("CREATE INDEX IF NOT EXISTS ix_audit_logs_created_at ON audit_logs (created_at)")
        # Consistent with the RLS rollout: enable + default-deny for anon/authenticated.
        op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY")
    else:
        import contextlib
        import sqlalchemy as sa
        with contextlib.suppress(Exception):
            op.create_table(
                'audit_logs',
                sa.Column('id', sa.String(36), primary_key=True),
                sa.Column('actor', sa.String(120), nullable=False),
                sa.Column('action', sa.String(40), nullable=False),
                sa.Column('target', sa.String(120), nullable=True),
                sa.Column('detail', sa.Text(), nullable=True),
                sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS audit_logs")
