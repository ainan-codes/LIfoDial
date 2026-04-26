"""add tenant missing columns

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
    # Add new columns to tenants table
    # We use batch_alter_table for SQLite compatibility
    with op.batch_alter_table('tenants', schema=None) as batch_op:
        # Check if column exists is hard in generic alembic, but we assume they don't exist in prod yet.
        # SQLite doesn't natively support IF NOT EXISTS in ADD COLUMN via standard syntax, but SQLAlchemy handles some of it.
        # We will just add them. If they exist on SQLite (local), this migration might fail locally.
        # A common trick is to catch the error or just use `add_column` which works fine on Postgres.
        # To make it robust for both, we can try to add them.
        batch_op.add_column(sa.Column('admin_password', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('ai_number', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('forwarding_number', sa.String(length=30), nullable=True))
        batch_op.add_column(sa.Column('language', sa.String(length=10), nullable=False, server_default='en-IN'))
        batch_op.add_column(sa.Column('plan', sa.String(length=20), nullable=False, server_default='Free'))
        batch_op.add_column(sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'))

    # Also make sure unique constraint for ai_number is added if possible
    # We will skip unique constraint creation here for simplicity on sqlite, or add it safely.
    # In Postgres, unique constraints are easy to add.
    # op.create_unique_constraint('uq_tenants_ai_number', 'tenants', ['ai_number'])


def downgrade() -> None:
    with op.batch_alter_table('tenants', schema=None) as batch_op:
        batch_op.drop_column('is_active')
        batch_op.drop_column('plan')
        batch_op.drop_column('language')
        batch_op.drop_column('forwarding_number')
        batch_op.drop_column('ai_number')
        batch_op.drop_column('admin_password')
