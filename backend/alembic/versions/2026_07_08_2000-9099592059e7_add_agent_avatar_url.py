"""add agent_configs.avatar_url (per-agent widget avatar)

Revision ID: 9099592059e7
Revises: 697951a4f746
Create Date: 2026-07-08 20:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op


revision: str = '9099592059e7'
down_revision: Union[str, None] = '697951a4f746'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500)")
    else:
        import contextlib
        with contextlib.suppress(Exception):
            op.execute("ALTER TABLE agent_configs ADD COLUMN avatar_url VARCHAR(500)")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("ALTER TABLE agent_configs DROP COLUMN IF EXISTS avatar_url")
