"""add agent_prompt_history table (prompt edit history + revert)

Revision ID: 377274a6bed9
Revises: 93255d8d413e
Create Date: 2026-07-08 16:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '377274a6bed9'
down_revision: Union[str, None] = '93255d8d413e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == 'postgresql':
        op.execute("""
            CREATE TABLE IF NOT EXISTS agent_prompt_history (
                id VARCHAR(36) PRIMARY KEY,
                agent_id VARCHAR(36) NOT NULL REFERENCES agent_configs(id) ON DELETE CASCADE,
                field_name VARCHAR(20) NOT NULL,
                value TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_agent_prompt_history_agent_id "
            "ON agent_prompt_history (agent_id)"
        )
    else:
        import contextlib
        with contextlib.suppress(Exception):
            op.create_table(
                'agent_prompt_history',
                sa.Column('id', sa.String(36), primary_key=True),
                sa.Column('agent_id', sa.String(36), sa.ForeignKey('agent_configs.id', ondelete='CASCADE'), nullable=False, index=True),
                sa.Column('field_name', sa.String(20), nullable=False),
                sa.Column('value', sa.Text(), nullable=False),
                sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS agent_prompt_history")
