# backend/db.py
# Configured for Supabase Session Pooler
# Session Pooler: IPv4 compatible + asyncpg safe + no prepared statement issues

import os
import logging
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)

# Load .env into os.environ so get_database_url() below sees DATABASE_URL.
# pydantic-settings (backend/config.py) reads .env into the `settings` object
# but does NOT export to os.environ, and this module reads os.getenv directly.
# Without this, local dev silently fell back to SQLite whenever no other module
# happened to have called load_dotenv() first — a real source of "why is it on
# SQLite / why did it connect to the wrong DB" flakiness.
load_dotenv()


def get_database_url() -> str:
    raw = os.getenv("DATABASE_URL", "")

    if not raw:
        logger.warning("No DATABASE_URL - using SQLite fallback")
        return "sqlite+aiosqlite:///./lifodial.db"

    # Convert sync URL to async driver format
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgres://"):
        return raw.replace("postgres://", "postgresql+asyncpg://", 1)

    return raw


DATABASE_URL = get_database_url()
IS_SQLITE = "sqlite" in DATABASE_URL

# Detect Supabase by known URL patterns
IS_SUPABASE = any(x in DATABASE_URL for x in [
    "supabase.co",
    "supabase.com",
    "pooler.supabase",
])

db_label = "SQLite (local dev)"
if not IS_SQLITE:
    db_label = "Supabase PostgreSQL" if IS_SUPABASE else "PostgreSQL"
logger.info(f"Database engine: {db_label}")


if IS_SQLITE:
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        connect_args={"check_same_thread": False},
    )
else:
    # -- Supabase Session Pooler Configuration -----------------
    #
    # WHY NullPool:
    #   Supabase manages connection pooling externally.
    #   SQLAlchemy's own pool causes connection exhaustion
    #   on free tier. NullPool = open/close per request.
    #
    # WHY statement_cache_size=0:
    #   Prevents DuplicatePreparedStatementError which occurs
    #   when pooler routes requests to different PG backends.
    #   Required even for Session Pooler as safety measure.
    #
    # WHY jit=off:
    #   Supabase recommendation - improves query plan stability.
    #
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        poolclass=NullPool,
        connect_args={
            "statement_cache_size": 0,
            "server_settings": {"jit": "off"},
        },
    )

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

# Backwards-compat alias: several routers import 'async_session' from backend.db
async_session = AsyncSessionLocal


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI dependency - yields DB session per request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """
    Called at app startup.
    Creates missing tables (checkfirst=True = safe to call always).
    With Supabase: all tables exist already = instant no-op.
    """
    logger.info("init_db: starting...")
    _import_all_models()

    registered = list(Base.metadata.tables.keys())
    logger.info(f"init_db: {len(registered)} tables registered: {registered}")

    try:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda c: Base.metadata.create_all(c, checkfirst=True)
            )
        # create_all only creates missing TABLES, not new columns on existing
        # ones. Apply small additive column migrations idempotently here.
        await _apply_lightweight_migrations()
        logger.info("? init_db: complete")
        print(f"? Database ready ({db_label})")
    except Exception as e:
        # Non-fatal - tables likely already exist in Supabase
        logger.warning(f"init_db non-fatal warning: {str(e)[:120]}")
        print(f"??  DB init warning (non-fatal): {str(e)[:80]}")
        print("    Tables likely already exist in Supabase - continuing...")


async def _apply_lightweight_migrations():
    """Additive, idempotent column adds for existing tables. Postgres supports
    ADD COLUMN IF NOT EXISTS; SQLite (dev) is best-effort via try/except."""
    from sqlalchemy import text
    # (table, column, type + default) — safe to re-run every startup.
    migrations = [
        ("agent_configs", "embed_display_mode", "VARCHAR(20) DEFAULT 'button'"),
        ("agent_configs", "embed_auto_invite_delay", "INTEGER DEFAULT 3"),
    ]
    for table, column, coldef in migrations:
        try:
            if IS_SQLITE:
                # SQLite lacks ADD COLUMN IF NOT EXISTS; check pragma first.
                async with engine.begin() as conn:
                    cols = await conn.run_sync(
                        lambda c: [r[1] for r in c.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()]
                    )
                    if column not in cols:
                        await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")
            else:
                async with engine.begin() as conn:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {coldef}"))
        except Exception as e:
            logger.warning("Lightweight migration %s.%s skipped: %s", table, column, str(e)[:120])


def _import_all_models():
    """
    Import every model so SQLAlchemy's metadata knows about them.
    Must run before create_all or any table inspection.
    """
    # Core models - always required
    try:
        from backend.models.tenant import Tenant        # noqa: F401
        from backend.models.doctor import Doctor        # noqa: F401
        from backend.models.agent_config import AgentConfig  # noqa: F401
    except ImportError as e:
        logger.error(f"CRITICAL: Core model import failed: {e}")
        raise

    # Optional models - import safely, skip if not yet created
    optional = [
        "backend.models.appointment",
        "backend.models.call_log",
        "backend.models.call_record",
        "backend.models.phone_number",
        "backend.models.clinic_credits",   # contains ClinicCredits + CreditTransaction
        "backend.models.knowledge_base",
        "backend.models.bulk_call",        # was bulk_call_campaign
        "backend.models.embed_analytics",  # was embed_event
        "backend.models.onboarding_request",
        "backend.models.api_key_config",
        "backend.models.agent_prompt_history",
        "backend.models.audit_log",
    ]

    for module_path in optional:
        try:
            __import__(module_path)
        except ImportError:
            pass  # Model file doesn't exist yet - safe to skip
        except Exception as e:
            logger.warning(f"Model import warning [{module_path}]: {e}")
