"""fix agent_configs.updated_at incorrectly NOT NULL (model says nullable=True, no insert-time default)

Revision ID: 697951a4f746
Revises: 8db6e8d2ed0c
Create Date: 2026-07-08 16:35:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op


revision: str = '697951a4f746'
down_revision: Union[str, None] = '8db6e8d2ed0c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("ALTER TABLE agent_configs ALTER COLUMN updated_at DROP NOT NULL")


def downgrade() -> None:
    pass
