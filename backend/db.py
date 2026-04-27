from sqlalchemy.ext.asyncio import (
    create_async_engine, AsyncSession, async_sessionmaker
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
from uuid import uuid4
import os
import logging
from alembic import command
from alembic.config import Config

logger = logging.getLogger(__name__)


def _build_database_url() -> str:
    """Normalise DATABASE_URL to the correct async driver format."""
    raw = os.getenv("DATABASE_URL", "").strip()  # strip newlines/spaces from Render env UI
    if not raw:
        logger.warning("No DATABASE_URL set — using SQLite (development)")
        return "sqlite+aiosqlite:///./lifodial.db"
    # Supabase / Heroku / Render ship postgresql:// or postgres://
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    # Already correct (postgresql+asyncpg:// or sqlite+aiosqlite://)
    return raw


DATABASE_URL = _build_database_url()
IS_SQLITE = "sqlite" in DATABASE_URL

logger.info("Database driver: %s", "SQLite" if IS_SQLITE else "PostgreSQL (asyncpg)")

if IS_SQLITE:
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        connect_args={"check_same_thread": False}
    )
else:
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        pool_pre_ping=False,
        pool_size=5,
        max_overflow=10,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
        }
    )

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

class Base(DeclarativeBase):
    pass

async_session = AsyncSessionLocal

async def init_db():
    """Creates all tables automatically. Import all models first so metadata is populated."""
    logger.info("Initializing database...")
    
    # Import all models to register them with Base.metadata
    # Importing classes directly to ensure they are defined and registered
    try:
        from backend.models.tenant import Tenant
        from backend.models.doctor import Doctor
        from backend.models.appointment import Appointment
        from backend.models.call_log import CallLog
        from backend.models.agent_config import AgentConfig
        from backend.models.onboarding_request import OnboardingRequest
        from backend.models.api_key_config import ApiKeyConfig
        from backend.models.knowledge_base import KnowledgeBase
        from backend.models.phone_number import PhoneNumber
        from backend.models.call_record import CallRecord
        from backend.models.embed_analytics import EmbedAnalytics
        from backend.models.bulk_call import BulkCallCampaign
        
        logger.info(f"Registered models: {list(Base.metadata.tables.keys())}")
    except ImportError as e:
        logger.error(f"Failed to import models for DB init: {e}")
        raise

    if not Base.metadata.tables:
        logger.warning("No tables found in metadata! Check model imports.")

    async with engine.begin() as conn:
        logger.info("Running Base.metadata.create_all...")
        await conn.run_sync(Base.metadata.create_all)

    db_type = "SQLite" if IS_SQLITE else "PostgreSQL"
    logger.info(f"Database ready ({db_type})")
    print(f"[OK] Database ready ({db_type})")

    # Run Alembic migrations for PostgreSQL
    if not IS_SQLITE:
        logger.info("PostgreSQL detected — running Alembic migrations...")
        try:
            # alembic.ini is in the same directory as db.py
            base_dir = os.path.dirname(os.path.abspath(__file__))
            ini_path = os.path.join(base_dir, "alembic.ini")
            alembic_cfg = Config(ini_path)
            
            # Ensure Alembic uses the correct DATABASE_URL
            alembic_cfg.set_main_option("sqlalchemy.url", DATABASE_URL)
            
            # Run upgrade head
            command.upgrade(alembic_cfg, "head")
            logger.info("Alembic migrations applied successfully.")
        except Exception as e:
            logger.error(f"Alembic migration failed: {e}")
            # Non-fatal if create_all worked, but we should log it
            print(f"  [WARN] Alembic migration failed (non-fatal): {e}")

    # Always run safe migrations after create_all (mostly for SQLite column adds)
    await _run_migrations()


async def _run_migrations():
    """Safe schema migrations — adds missing columns without destroying data.
    Runs on every startup; idempotent (checks column existence first)."""
    # Only applies when using SQLite. PostgreSQL uses Alembic.
    if not IS_SQLITE:
        logger.info("PostgreSQL detected — skipping SQLite migrations")
        return

    import aiosqlite
    db_path = DATABASE_URL.replace("sqlite+aiosqlite:///", "")
    if not db_path.startswith(".") and not db_path.startswith("/"):
        db_path = "." + db_path  # relative path safety

    # Only applies when using SQLite. Skip for PostgreSQL.
    if "sqlite" not in DATABASE_URL:
        return

    async with aiosqlite.connect(db_path) as db:
        async def get_cols(table: str):
            cursor = await db.execute(f"PRAGMA table_info({table})")
            rows = await cursor.fetchall()
            return {row[1] for row in rows}  # set of column names

        async def add_col(table: str, col: str, definition: str):
            cols = await get_cols(table)
            if col not in cols:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
                print(f"  [OK] Migration: added {table}.{col}")

        # ── tenants ─────────────────────────────────────────────────────────
        tables_cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {r[0] for r in await tables_cursor.fetchall()}

        if "tenants" in tables:
            await add_col("tenants", "phone",          "TEXT")
            await add_col("tenants", "status",         "TEXT DEFAULT 'active'")
            await add_col("tenants", "plan",           "TEXT DEFAULT 'Free'")
            await add_col("tenants", "forwarding_number", "TEXT")

        # ── appointments ────────────────────────────────────────────────────
        if "appointments" in tables:
            await add_col("appointments", "patient_name",   "TEXT")
            await add_col("appointments", "his_booking_id", "TEXT")
            await add_col("appointments", "call_id",        "TEXT")

        # ── api_key_configs ─────────────────────────────────────────────────
        if "api_key_configs" in tables:
            await add_col("api_key_configs", "provider",     "TEXT")
            await add_col("api_key_configs", "category",     "TEXT")
            await add_col("api_key_configs", "display_name", "TEXT")
            await add_col("api_key_configs", "api_key_enc",  "TEXT")
            await add_col("api_key_configs", "is_active",    "INTEGER DEFAULT 0")
            await add_col("api_key_configs", "extra_config", "TEXT")

        # ── agent_configs — drop unique constraint on tenant_id ──────────────
        # SQLite doesn't support DROP CONSTRAINT, so we check if the unique
        # index exists and recreate the table without it.
        if "agent_configs" in tables:
            idx_cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='agent_configs'"
            )
            indexes = {r[0] for r in await idx_cursor.fetchall()}
            # The auto-created unique index is named ix_agent_configs_tenant_id
            if "ix_agent_configs_tenant_id" in indexes:
                await db.execute("DROP INDEX IF EXISTS ix_agent_configs_tenant_id")
                # Create a non-unique index instead
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent_tenant ON agent_configs(tenant_id)"
                )
                print("  [OK] Migration: dropped unique constraint on agent_configs.tenant_id")

            # New columns for Phase 8 features
            await add_col("agent_configs", "record_calls", "INTEGER DEFAULT 0")
            await add_col("agent_configs", "webhook_url", "TEXT")

            # ── Embed / Widget columns ────────────────────────────────────
            await add_col("agent_configs", "embed_enabled", "INTEGER DEFAULT 1")
            await add_col("agent_configs", "embed_allowed_domains", "TEXT DEFAULT '[]'")
            await add_col("agent_configs", "embed_position", "TEXT DEFAULT 'bottom-right'")
            await add_col("agent_configs", "embed_theme", "TEXT DEFAULT 'dark'")
            await add_col("agent_configs", "embed_button_text", "TEXT DEFAULT 'Talk to Receptionist'")
            await add_col("agent_configs", "embed_primary_color", "TEXT DEFAULT '#3ECF8E'")
            await add_col("agent_configs", "embed_show_branding", "INTEGER DEFAULT 1")

            # ── Capabilities columns ──────────────────────────────────────
            await add_col("agent_configs", "can_book_appointments", "INTEGER DEFAULT 1")
            await add_col("agent_configs", "can_cancel_appointments", "INTEGER DEFAULT 1")
            await add_col("agent_configs", "can_check_availability", "INTEGER DEFAULT 1")
            await add_col("agent_configs", "can_transfer_emergency", "INTEGER DEFAULT 1")
            await add_col("agent_configs", "emergency_transfer_number", "TEXT")
            await add_col("agent_configs", "auto_detect_language", "INTEGER DEFAULT 1")
            await add_col("agent_configs", "clinic_info", "TEXT")

        # ── api_key_configs — deduplicate rows per (provider, category) ──────
        # If duplicate rows exist, keep only the most-recently created one.
        # Then enforce uniqueness so it can't happen again.
        if "api_key_configs" in tables:
            # Check for duplicates
            dup_cursor = await db.execute("""
                SELECT provider, category, COUNT(*) as cnt
                FROM api_key_configs
                GROUP BY provider, category
                HAVING cnt > 1
            """)
            dups = await dup_cursor.fetchall()
            if dups:
                for row in dups:
                    prov, cat, cnt = row
                    # Delete all but the latest row for this provider/category
                    await db.execute("""
                        DELETE FROM api_key_configs
                        WHERE provider = ? AND category = ?
                        AND id NOT IN (
                            SELECT id FROM api_key_configs
                            WHERE provider = ? AND category = ?
                            ORDER BY created_at DESC
                            LIMIT 1
                        )
                    """, (prov, cat, prov, cat))
                print(f"  [OK] Migration: deduplicated api_key_configs ({len(dups)} groups cleaned)")

            # Add unique index if missing
            idx_cursor2 = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_api_key_provider_category'"
            )
            if not await idx_cursor2.fetchone():
                await db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_api_key_provider_category "
                    "ON api_key_configs(provider, category)"
                )
                print("  [OK] Migration: added unique index on api_key_configs(provider, category)")

        await db.commit()
    print("[OK] Schema migrations complete")

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
