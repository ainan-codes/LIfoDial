"""fix agent_configs boolean columns that were created as integer

Several AgentConfig boolean fields were created as INTEGER on this table
(only `record_calls` was ever fixed, in migration d5e6f7a8b9c0). Every
INSERT/UPDATE through the ORM sends a real boolean, which asyncpg then
rejects with DatatypeMismatchError against an integer column — this was
silently blocking agent creation/update on any column in this list.

Revision ID: 8db6e8d2ed0c
Revises: 377274a6bed9
Create Date: 2026-07-08 16:30:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '8db6e8d2ed0c'
down_revision: Union[str, None] = '377274a6bed9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BOOLEAN_COLUMNS = [
    "background_denoising",
    "hipaa_enabled",
    "keypad_input_enabled",
    "llm_emotion_recognition",
    "model_output_in_realtime",
    "pii_redaction_enabled",
    "sms_enabled",
    "structured_output_enabled",
    "summary_enabled",
    "success_evaluation_enabled",
    "tts_filler_injection",
    "tts_input_preprocessing",
    "tts_use_speaker_boost",
    "voicemail_detection_enabled",
]


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == 'postgresql':
        for col in BOOLEAN_COLUMNS:
            op.execute(
                f'ALTER TABLE agent_configs ALTER COLUMN "{col}" '
                f'TYPE boolean USING ("{col}"::int <> 0)'
            )
    else:
        # SQLite has no real column-type enforcement (values already round-trip
        # fine as 0/1 truthy ints), so there's nothing to migrate there.
        pass


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == 'postgresql':
        for col in BOOLEAN_COLUMNS:
            op.execute(
                f'ALTER TABLE agent_configs ALTER COLUMN "{col}" '
                f'TYPE integer USING ("{col}"::int)'
            )
