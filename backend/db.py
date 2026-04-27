"""
backend/db.py — Database engine, session factory, and startup initialisation.

Key design decisions for Supabase + Render production:
  • NullPool        — required by PgBouncer (transaction-mode); no persistent
                      pool that PgBouncer cannot track.
  • statement_cache_size=0
                   — disables asyncpg prepared-statement cache; prepared
                      statements are per-connection and PgBouncer routes each
                      statement to a different backend connection, causing
                      "prepared statement does not exist" errors.
  • create_all checkfirst=True
                   — SQLAlchemy passes IF NOT EXISTS to Postgres, making the
                      call idempotent.  Safe for 2-worker simultaneous startup.
  • Alembic stamp  — after create_all we stamp the DB at "head" so Alembic
                      doesn't try to re-run migrations that create_all already
                      applied.  On subsequent starts upgrade head is a no-op.
"""

from sqlalchemy.ext.asyncio import (
    create_async_engine, AsyncSession, async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
import os
import logging

logger = logging.getLogger(__name__)


# ── URL normalisation ──────────────────────────────────────────────────────────

def _build_database_url() -> str:
    """Normalise DATABASE_URL to the correct async driver format."""
    raw = os.getenv("DATABASE_URL", "").strip()   # strip spaces/newlines from Render UI
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

logger.info("Database driver: %s", "SQLite" if IS_SQLITE else "PostgreSQL/asyncpg")


# ── Engine ─────────────────────────────────────────────────────────────────────

if IS_SQLITE:
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        connect_args={"check_same_thread": False},
    )
else:
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        # ── PgBouncer / Supabase compatibility ────────────────────────────
        # NullPool: don't keep a connection pool inside the app process.
        # PgBouncer is the pool; we ask for a connection, use it, return it.
        poolclass=NullPool,
        # Disable prepared-statement cache: PgBouncer (transaction mode)
        # routes each statement to a different backend connection, so cached
        # prepared statements are invisible to subsequent calls.
        connect_args={
            "statement_cache_size": 0,
        },
    )


# ── Session factory ────────────────────────────────────────────────────────────

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

async_session = AsyncSessionLocal   # convenience alias used across the codebase


# ── ORM base ──────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ── Startup initialisation ─────────────────────────────────────────────────────

async def init_db() -> None:
    """
    Called once from the FastAPI lifespan on every worker startup.

    Strategy (safe for 2-worker simultaneous boot):
      1. Import every ORM model so Base.metadata is fully populated.
      2. Run create_all(checkfirst=True) — translates to CREATE TABLE IF NOT
         EXISTS; concurrent workers both run this and it is idempotent.
      3. For PostgreSQL: stamp the Alembic version table so that 'upgrade head'
         is a no-op on a freshly created schema, then run upgrade head so that
         any real Alembic-only migrations (ALTER TABLE etc.) are applied.
    """
    logger.info("init_db: starting…")

    # ── 1. Register all models with Base.metadata ──────────────────────────
    _import_all_models()
    logger.info("init_db: registered tables → %s", sorted(Base.metadata.tables))

    if not Base.metadata.tables:
        logger.error("init_db: no tables in metadata — check model imports!")
        return

    # ── 2. CREATE TABLE IF NOT EXISTS (idempotent, race-safe) ─────────────
    async with engine.begin() as conn:
        logger.info("init_db: running create_all (checkfirst=True)…")
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(
            sync_conn, checkfirst=True
        ))
    logger.info("init_db: create_all complete")
    print(f"[OK] Database ready ({'SQLite' if IS_SQLITE else 'PostgreSQL'})")

    # ── 3. Alembic: stamp + upgrade (PostgreSQL only) ──────────────────────
    if not IS_SQLITE:
        _run_alembic_migrations()

    # ── 4. SQLite-only column backfills ───────────────────────────────────
    if IS_SQLITE:
        await _sqlite_migrations()


def _import_all_models() -> None:
    """Import every ORM model class to ensure it's registered with Base.metadata."""
    try:
        from backend.models.tenant import Tenant                        # noqa: F401
        from backend.models.doctor import Doctor                        # noqa: F401
        from backend.models.appointment import Appointment              # noqa: F401
        from backend.models.call_log import CallLog                     # noqa: F401
        from backend.models.agent_config import AgentConfig             # noqa: F401
        from backend.models.onboarding_request import OnboardingRequest # noqa: F401
        from backend.models.api_key_config import ApiKeyConfig          # noqa: F401
        from backend.models.knowledge_base import KnowledgeBase         # noqa: F401
        from backend.models.phone_number import PhoneNumber             # noqa: F401
        from backend.models.call_record import CallRecord               # noqa: F401
        from backend.models.embed_analytics import EmbedAnalytics       # noqa: F401
        from backend.models.bulk_call import BulkCallCampaign           # noqa: F401
    except ImportError as exc:
        logger.error("init_db: model import failed → %s", exc)
        raise


def _run_alembic_migrations() -> None:
    """
    Stamp the Alembic version table (if not already stamped) then run upgrade head.

    Why stamp first?
    - create_all already created every table; if Alembic's alembic_version table
      is empty it will attempt to replay all migrations from scratch (including
      CREATE TABLE statements that now fail on already-existing tables).
    - Stamping at 'head' tells Alembic "the schema is already at the latest
      version" without running any SQL.
    - upgrade head is then a quick no-op on fresh databases, but still applies
      any real ALTER TABLE / data migrations on existing DBs that are behind.
    """
    try:
        from alembic import command
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from sqlalchemy import create_engine, text

        # Build a sync URL for Alembic (it doesn't use asyncpg)
        sync_url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)

        base_dir = os.path.dirname(os.path.abspath(__file__))
        ini_path = os.path.join(base_dir, "alembic.ini")
        alembic_cfg = Config(ini_path)
        alembic_cfg.set_main_option("sqlalchemy.url", sync_url)

        # Check if alembic_version is already stamped
        sync_engine = create_engine(sync_url)
        with sync_engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            current_rev = ctx.get_current_revision()

        sync_engine.dispose()

        if current_rev is None:
            # Fresh database: stamp at head so upgrade head is a no-op
            logger.info("init_db: Alembic not stamped — stamping at head…")
            command.stamp(alembic_cfg, "head")
        else:
            logger.info("init_db: Alembic current revision → %s", current_rev)

        # Apply any real migrations that aren't covered by create_all
        logger.info("init_db: running alembic upgrade head…")
        command.upgrade(alembic_cfg, "head")
        logger.info("init_db: Alembic migrations applied successfully")

    except Exception as exc:
        # Non-fatal: create_all already ensured all tables exist.
        # Log clearly but don't crash startup.
        logger.warning("init_db: Alembic step failed (non-fatal) — %s", exc)
        print(f"  [WARN] Alembic: {exc}")


# ── SQLite-only column backfills ───────────────────────────────────────────────

async def _sqlite_migrations() -> None:
    """Adds missing columns to an existing SQLite dev database. Idempotent."""
    import aiosqlite

    db_path = DATABASE_URL.replace("sqlite+aiosqlite:///", "")
    if not db_path.startswith((".","/")):
        db_path = "." + db_path

    async with aiosqlite.connect(db_path) as db:

        async def get_cols(table: str) -> set:
            cur = await db.execute(f"PRAGMA table_info({table})")
            rows = await cur.fetchall()
            return {r[1] for r in rows}

        async def add_col(table: str, col: str, definition: str) -> None:
            if col not in await get_cols(table):
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
                print(f"  [OK] Migration: added {table}.{col}")

        cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in await cur.fetchall()}

        if "tenants" in tables:
            await add_col("tenants", "phone",             "TEXT")
            await add_col("tenants", "status",            "TEXT DEFAULT 'active'")
            await add_col("tenants", "plan",              "TEXT DEFAULT 'Free'")
            await add_col("tenants", "forwarding_number", "TEXT")

        if "appointments" in tables:
            await add_col("appointments", "patient_name",   "TEXT")
            await add_col("appointments", "his_booking_id", "TEXT")
            await add_col("appointments", "call_id",        "TEXT")

        if "api_key_configs" in tables:
            await add_col("api_key_configs", "provider",     "TEXT")
            await add_col("api_key_configs", "category",     "TEXT")
            await add_col("api_key_configs", "display_name", "TEXT")
            await add_col("api_key_configs", "api_key_enc",  "TEXT")
            await add_col("api_key_configs", "is_active",    "INTEGER DEFAULT 0")
            await add_col("api_key_configs", "extra_config", "TEXT")

        if "agent_configs" in tables:
            idx_cur = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='agent_configs'"
            )
            indexes = {r[0] for r in await idx_cur.fetchall()}
            if "ix_agent_configs_tenant_id" in indexes:
                await db.execute("DROP INDEX IF EXISTS ix_agent_configs_tenant_id")
                await db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_agent_tenant ON agent_configs(tenant_id)"
                )
                print("  [OK] Migration: dropped unique constraint on agent_configs.tenant_id")

            for col, defn in [
                ("record_calls",              "INTEGER DEFAULT 0"),
                ("webhook_url",               "TEXT"),
                ("embed_enabled",             "INTEGER DEFAULT 1"),
                ("embed_allowed_domains",     "TEXT DEFAULT '[]'"),
                ("embed_position",            "TEXT DEFAULT 'bottom-right'"),
                ("embed_theme",               "TEXT DEFAULT 'dark'"),
                ("embed_button_text",         "TEXT DEFAULT 'Talk to Receptionist'"),
                ("embed_primary_color",       "TEXT DEFAULT '#3ECF8E'"),
                ("embed_show_branding",       "INTEGER DEFAULT 1"),
                ("can_book_appointments",     "INTEGER DEFAULT 1"),
                ("can_cancel_appointments",   "INTEGER DEFAULT 1"),
                ("can_check_availability",    "INTEGER DEFAULT 1"),
                ("can_transfer_emergency",    "INTEGER DEFAULT 1"),
                ("emergency_transfer_number", "TEXT"),
                ("auto_detect_language",      "INTEGER DEFAULT 1"),
                ("clinic_info",               "TEXT"),
            ]:
                await add_col("agent_configs", col, defn)

        await db.commit()

    print("[OK] SQLite schema migrations complete")


# ── Request-scoped session dependency ──────────────────────────────────────────

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
