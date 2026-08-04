"""unify agent language into one source of truth + lock provider/model

Adds ``agent_configs.language`` — the single language field — and collapses the
two pre-existing, independently editable language columns into it. Also forces
the locked LLM/STT/TTS provider+model onto every existing row, because those were
free-form pairs and invalid combinations had accumulated.

Why this migration exists
-------------------------
Verified against the live database on 2026-08-03. Agent
``f367e0e2-4e31-41fd-8a4a-df0f6ebbd8d7`` (clinic ``kmct``) held::

    stt_language = 'ta-IN'    tts_language = 'ml-IN'    tts_voice = 'shruti'
    llm_provider = 'groq'     llm_model    = 'gemini-2.5-flash-8b'

which rendered as four disagreeing languages in the UI simultaneously, pinned STT
to Tamil while TTS spoke Malayalam (so a Malayalam caller could not be
understood), and pointed Groq at a model it answers HTTP 404 for.

Conflict resolution — the documented rule
-----------------------------------------
Implemented by ``backend.services.agent_defaults.resolve_language``, which is the
same function the API uses, so the migration and the runtime can never disagree.
Precedence: existing ``language`` -> ``tts_language`` -> ``stt_language`` ->
``'en-IN'``, skipping ``'auto'`` at every step.

``tts_language`` wins a genuine disagreement because it is what the operator
actually saw (both the "SELECTED VOICE" header and the field labelled "LANGUAGE"
rendered it, making it the best record of intent), it is the only one constrained
to a speakable language, the voice was chosen against it, and it is what the
caller hears. For kmct that yields ``ml-IN`` — corroborated by the stakeholder's
own complaint being that Malayalam was missing.

Rows where the two legacy columns held two DIFFERENT real languages additionally
get ``auto_detect_language = true``. Such a row is genuinely ambiguous about what
the caller speaks, and hard-pinning the microphone to the tie-break winner would
have made kmct unable to hear the Tamil it had been configured to expect. Letting
the provider detect is the safe resolution; the in-call LanguageSwitchProcessor
then retunes TTS to match.

The legacy columns are KEPT as derived mirrors, not dropped: a deployed agent
worker may still be on a revision that reads them, and dropping them would break
live calls the moment this lands. They are safe to drop in a follow-up once every
worker is confirmed to read ``language``.

Revision ID: c7d1e9f2a3b4
Revises: a1b2c3d4e5f6
Create Date: 2026-08-04 09:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from backend.services.agent_defaults import (
    DEFAULT_LANGUAGE,
    LOCKED_LLM_MODEL,
    LOCKED_LLM_PROVIDER,
    LOCKED_STT_MODEL,
    LOCKED_STT_PROVIDER,
    LOCKED_TTS_MODEL,
    LOCKED_TTS_PROVIDER,
    effective_stt_language,
    resolve_language,
)

revision = "c7d1e9f2a3b4"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("agent_configs")}

    # ── 1. The canonical column ───────────────────────────────────────────────
    # Added nullable so the backfill can run, then made NOT NULL. Adding it
    # NOT NULL up front would stamp every existing row with the server_default
    # and destroy the very values the backfill needs to read.
    if "language" not in cols:
        op.add_column(
            "agent_configs",
            sa.Column("language", sa.String(length=20), nullable=True),
        )

    # ── 2. No DDL needed for the mirrors ──────────────────────────────────────
    # The ORM model declared tts_language as String(10), but the live column was
    # verified as varchar(20) on 2026-08-04 — the model was under-reporting, the
    # same way stt_language did. Both mirrors are therefore already varchar(20)
    # and match `language`, so no ALTER is issued here. The model declaration has
    # been corrected to String(20) to stop the drift.

    # ── 3. Backfill, row by row, through the shared resolver ──────────────────
    rows = bind.execute(
        sa.text(
            "SELECT id, language, stt_language, tts_language, auto_detect_language "
            "FROM agent_configs"
        )
    ).mappings().all()

    for row in rows:
        language, conflicting = resolve_language(
            language=row["language"],
            tts_language=row["tts_language"],
            stt_language=row["stt_language"],
        )

        # A genuinely ambiguous row goes to auto-detect rather than being pinned
        # to the tie-break winner. See the module docstring.
        auto_detect = True if conflicting else bool(row["auto_detect_language"])

        bind.execute(
            sa.text(
                "UPDATE agent_configs SET "
                "  language = :language,"
                "  tts_language = :language,"
                "  stt_language = :stt_language,"
                "  auto_detect_language = :auto_detect,"
                "  llm_provider = :llm_p, llm_model = :llm_m,"
                "  stt_provider = :stt_p, stt_model = :stt_m,"
                "  tts_provider = :tts_p, tts_model = :tts_m "
                "WHERE id = :id"
            ),
            {
                "id": row["id"],
                "language": language,
                "stt_language": effective_stt_language(language, auto_detect=auto_detect),
                "auto_detect": auto_detect,
                "llm_p": LOCKED_LLM_PROVIDER, "llm_m": LOCKED_LLM_MODEL,
                "stt_p": LOCKED_STT_PROVIDER, "stt_m": LOCKED_STT_MODEL,
                "tts_p": LOCKED_TTS_PROVIDER, "tts_m": LOCKED_TTS_MODEL,
            },
        )

    # ── 4. Lock it down ───────────────────────────────────────────────────────
    op.execute(
        sa.text(
            "UPDATE agent_configs SET language = :d WHERE language IS NULL OR language = ''"
        ).bindparams(d=DEFAULT_LANGUAGE)
    )
    if bind.dialect.name != "sqlite":
        op.alter_column(
            "agent_configs",
            "language",
            existing_type=sa.String(length=20),
            nullable=False,
            server_default=DEFAULT_LANGUAGE,
        )


def downgrade() -> None:
    # The legacy columns were never dropped and were kept in sync throughout, so
    # dropping `language` is a clean reversal — stt_language/tts_language still
    # hold usable values. What is NOT reversed is the provider/model lockdown:
    # the prior per-row values are not recoverable, and several of them
    # (groq + gemini-2.5-flash-8b) were broken configurations anyway. Restore
    # those from the pre-migration snapshot if a real rollback is ever needed.
    op.drop_column("agent_configs", "language")
