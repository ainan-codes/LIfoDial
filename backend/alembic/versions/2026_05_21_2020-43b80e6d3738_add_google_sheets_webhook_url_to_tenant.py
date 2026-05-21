"""add google sheets webhook url to tenant

Revision ID: 43b80e6d3738
Revises: d5e6f7a8b9c0
Create Date: 2026-05-21 20:20:00.144144+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '43b80e6d3738'
down_revision: Union[str, None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == 'postgresql':
        op.execute("ALTER TABLE tenants ADD COLUMN IF NOT EXISTS google_sheets_webhook_url VARCHAR(500)")
    else:
        import contextlib
        with op.batch_alter_table('tenants', schema=None) as batch_op:
            with contextlib.suppress(Exception):
                batch_op.add_column(sa.Column('google_sheets_webhook_url', sa.String(500), nullable=True))

def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == 'postgresql':
        op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS google_sheets_webhook_url")
    else:
        with op.batch_alter_table('tenants', schema=None) as batch_op:
            batch_op.drop_column('google_sheets_webhook_url')
