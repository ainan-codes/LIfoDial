"""add tenant missing columns (idempotent)

Revision ID: abcdef123456
Revises: bbf25bb3c633
Create Date: 2026-04-26 17:41:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'abcdef123456'
down_revision: Union[str, None] = 'bbf25bb3c633'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use raw SQL with IF NOT EXISTS — completely safe to run multiple times.
    # This avoids errors if columns were already added manually to prod.
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == 'postgresql':
        # Postgres supports ADD COLUMN IF NOT EXISTS natively
        op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS forwarding_number VARCHAR(30)")
        op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS plan VARCHAR(20) NOT NULL DEFAULT 'Free'")
        op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS admin_password VARCHAR(100)")
        op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS ai_number VARCHAR(30)")
        op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS language VARCHAR(10) NOT NULL DEFAULT 'en-IN'")
        op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE")
    else:
        # SQLite: batch alter to add columns (will error if they exist — acceptable in dev)
        import contextlib
        with op.batch_alter_table('tenants', schema=None) as batch_op:
            for col, typ, kwargs in [
                ('admin_password', sa.String(100), {'nullable': True}),
                ('ai_number', sa.String(30), {'nullable': True}),
                ('forwarding_number', sa.String(30), {'nullable': True}),
                ('language', sa.String(10), {'nullable': False, 'server_default': 'en-IN'}),
                ('plan', sa.String(20), {'nullable': False, 'server_default': 'Free'}),
                ('is_active', sa.Boolean(), {'nullable': False, 'server_default': '1'}),
            ]:
                with contextlib.suppress(Exception):
                    batch_op.add_column(sa.Column(col, typ, **kwargs))


def downgrade() -> None:
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS forwarding_number")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS plan")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS admin_password")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS ai_number")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS language")
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS is_active")
