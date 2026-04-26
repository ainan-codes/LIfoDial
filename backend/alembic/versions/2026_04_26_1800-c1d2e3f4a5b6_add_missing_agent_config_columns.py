"""add missing agent_config columns (idempotent)

Revision ID: c1d2e3f4a5b6
Revises: abcdef123456
Create Date: 2026-04-26 18:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, None] = 'abcdef123456'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == 'postgresql':
        # Capabilities
        op.execute("ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS can_book_appointments BOOLEAN NOT NULL DEFAULT TRUE")
        op.execute("ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS can_cancel_appointments BOOLEAN NOT NULL DEFAULT TRUE")
        op.execute("ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS can_check_availability BOOLEAN NOT NULL DEFAULT TRUE")
        op.execute("ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS can_transfer_emergency BOOLEAN NOT NULL DEFAULT TRUE")
        op.execute("ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS emergency_transfer_number VARCHAR(20)")
        op.execute("ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS auto_detect_language BOOLEAN NOT NULL DEFAULT TRUE")
        # Clinic info
        op.execute("""ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS clinic_info JSONB DEFAULT '{"working_hours": "9:00 AM - 7:00 PM, Mon-Sat", "address": "", "emergency_number": "112", "services": [], "faqs": []}'""")
        # Webhooks
        op.execute("ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS webhook_url VARCHAR(500)")
        # Embed / Widget
        op.execute("ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS embed_enabled BOOLEAN NOT NULL DEFAULT TRUE")
        op.execute("ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS embed_allowed_domains JSONB DEFAULT '[]'")
        op.execute("ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS embed_position VARCHAR(20) DEFAULT 'bottom-right'")
        op.execute("ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS embed_theme VARCHAR(10) DEFAULT 'dark'")
        op.execute("ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS embed_button_text VARCHAR(50) DEFAULT 'Talk to Receptionist'")
        op.execute("ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS embed_primary_color VARCHAR(7) DEFAULT '#3ECF8E'")
        op.execute("ALTER TABLE agent_configs ADD COLUMN IF NOT EXISTS embed_show_branding BOOLEAN NOT NULL DEFAULT TRUE")
    else:
        import contextlib
        with op.batch_alter_table('agent_configs', schema=None) as batch_op:
            for col, typ, kwargs in [
                ('can_book_appointments',    sa.Boolean(),    {'nullable': False, 'server_default': '1'}),
                ('can_cancel_appointments',  sa.Boolean(),    {'nullable': False, 'server_default': '1'}),
                ('can_check_availability',   sa.Boolean(),    {'nullable': False, 'server_default': '1'}),
                ('can_transfer_emergency',   sa.Boolean(),    {'nullable': False, 'server_default': '1'}),
                ('emergency_transfer_number',sa.String(20),  {'nullable': True}),
                ('auto_detect_language',     sa.Boolean(),   {'nullable': False, 'server_default': '1'}),
                ('clinic_info',              sa.JSON(),       {'nullable': True}),
                ('webhook_url',              sa.String(500), {'nullable': True}),
                ('embed_enabled',            sa.Boolean(),   {'nullable': False, 'server_default': '1'}),
                ('embed_allowed_domains',    sa.JSON(),      {'nullable': True}),
                ('embed_position',           sa.String(20),  {'nullable': True, 'server_default': 'bottom-right'}),
                ('embed_theme',              sa.String(10),  {'nullable': True, 'server_default': 'dark'}),
                ('embed_button_text',        sa.String(50),  {'nullable': True, 'server_default': 'Talk to Receptionist'}),
                ('embed_primary_color',      sa.String(7),   {'nullable': True, 'server_default': '#3ECF8E'}),
                ('embed_show_branding',      sa.Boolean(),   {'nullable': False, 'server_default': '1'}),
            ]:
                with contextlib.suppress(Exception):
                    batch_op.add_column(sa.Column(col, typ, **kwargs))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute("ALTER TABLE agent_configs DROP COLUMN IF EXISTS can_book_appointments")
        op.execute("ALTER TABLE agent_configs DROP COLUMN IF EXISTS can_cancel_appointments")
        op.execute("ALTER TABLE agent_configs DROP COLUMN IF EXISTS can_check_availability")
        op.execute("ALTER TABLE agent_configs DROP COLUMN IF EXISTS can_transfer_emergency")
        op.execute("ALTER TABLE agent_configs DROP COLUMN IF EXISTS emergency_transfer_number")
        op.execute("ALTER TABLE agent_configs DROP COLUMN IF EXISTS auto_detect_language")
        op.execute("ALTER TABLE agent_configs DROP COLUMN IF EXISTS clinic_info")
        op.execute("ALTER TABLE agent_configs DROP COLUMN IF EXISTS webhook_url")
        op.execute("ALTER TABLE agent_configs DROP COLUMN IF EXISTS embed_enabled")
        op.execute("ALTER TABLE agent_configs DROP COLUMN IF EXISTS embed_allowed_domains")
        op.execute("ALTER TABLE agent_configs DROP COLUMN IF EXISTS embed_position")
        op.execute("ALTER TABLE agent_configs DROP COLUMN IF EXISTS embed_theme")
        op.execute("ALTER TABLE agent_configs DROP COLUMN IF EXISTS embed_button_text")
        op.execute("ALTER TABLE agent_configs DROP COLUMN IF EXISTS embed_primary_color")
        op.execute("ALTER TABLE agent_configs DROP COLUMN IF EXISTS embed_show_branding")
