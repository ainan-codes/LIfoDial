"""alter record_calls to boolean

Revision ID: d5e6f7a8b9c0
Revises: c1d2e3f4a5b6
Create Date: 2026-04-27 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("""
            ALTER TABLE agent_configs 
            ALTER COLUMN record_calls DROP DEFAULT,
            ALTER COLUMN record_calls TYPE boolean USING CASE WHEN record_calls = 1 THEN TRUE ELSE FALSE END,
            ALTER COLUMN record_calls SET DEFAULT FALSE
        """)
    else:
        # SQLite doesn't strictly enforce boolean vs integer in the same way, but we can do batch_alter_table
        # However SQLite doesn't easily support TYPE changes via ALTER TABLE directly without recreation.
        # Given the issue is PostgreSQL specific, we'll leave SQLite as is (it works with INTEGER/BOOLEAN interchangeably).
        pass


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("""
            ALTER TABLE agent_configs 
            ALTER COLUMN record_calls DROP DEFAULT,
            ALTER COLUMN record_calls TYPE integer USING CASE WHEN record_calls THEN 1 ELSE 0 END,
            ALTER COLUMN record_calls SET DEFAULT 0
        """)
    else:
        pass
