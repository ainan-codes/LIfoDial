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
    raw = os.getenv("DATABASE_URL", "").strip()
    env = os.getenv("ENVIRONMENT", "development").strip().lower()

    if not raw:
        # NON-NEGOTIABLE: production must NEVER silently fall back to SQLite.
        # A missing DATABASE_URL in production is a fatal misconfiguration —
        # refuse to boot loudly instead of coming up on an empty local file DB.
        if env == "production":
            raise RuntimeError(
                "FATAL: DATABASE_URL is not set but ENVIRONMENT=production. "
                "Refusing to boot on the SQLite fallback in production. Set "
                "DATABASE_URL to the Supabase session-pooler connection string."
            )
        logger.warning("No DATABASE_URL - using SQLite fallback (development only)")
        return "sqlite+aiosqlite:///./lifodial.db"

    # Convert sync URL to async driver format
    if raw.startswith("postgresql://"):
        url = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif raw.startswith("postgres://"):
        url = raw.replace("postgres://", "postgresql+asyncpg://", 1)
    else:
        url = raw

    # Even if DATABASE_URL is set, guard against it resolving to SQLite in prod
    # (e.g. someone sets DATABASE_URL=sqlite:///x.db). Same hard-fail rule.
    if env == "production" and "sqlite" in url.lower():
        raise RuntimeError(
            "FATAL: DATABASE_URL resolves to SQLite while ENVIRONMENT=production. "
            "Refusing to boot on SQLite in production."
        )

    return url


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
    # WHY NullPool (the default here):
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
    # ── DB_POOL_SIZE: keep connections alive instead (agent worker) ───────────
    # NullPool is right for the API — many concurrent requests, and Supabase's
    # connection budget is the scarce resource there. It is WRONG for the agent
    # worker, where connections are few but latency is everything: a fresh
    # asyncpg connect to Supabase is a TCP + TLS + auth round trip from
    # Singapore, and the caller sits in a silent room while it happens.
    #
    # Measured on the live worker (the "Call setup DB work finished in …" log
    # line, 2026-07-29): 2.94s / 3.10s / 3.88s / 3.96s / 5.04s per call — for
    # ~6 small indexed queries. The queries themselves are ~0.15-0.3s each; the
    # rest is the handshake, paid again on every single call.
    #
    # ⚠️ DO NOT SET DB_POOL_SIZE > 0 ON THE AGENT WORKER. The paragraph above is the
    # reasoning that led to DB_POOL_SIZE=2 there; it was measured, plausible, and
    # WRONG, and it was reverted to 0 on 2026-07-31. Why it cannot work:
    #
    # livekit-agents runs each job with its own event loop and closes it when the job
    # ends — livekit/agents/ipc/proc_client.py:61 does asyncio.new_event_loop() +
    # set_event_loop(), then run_until_complete(), then shuts the loop down. This is
    # true for JobExecutorType.THREAD (what the worker uses) as well as PROCESS.
    # asyncpg connections are bound to the loop that created them, but SQLAlchemy's
    # pool is process-global. So job 1 checks out a connection, uses it, returns it to
    # the pool, and its loop dies; job 2 then checks out that dead-loop connection.
    #
    # That is not a theoretical race. It showed up in production as
    #   Future exception was never retrieved
    #   future: <Future finished exception=InternalClientError(
    #       'got result for unknown protocol state 3')>
    # and reproduces locally as RuntimeError('Event loop is closed') on the second
    # loop. pool_pre_ping does NOT save you — the ping itself runs on the dead loop.
    #
    # And it bought nothing: per-call setup stayed at 1.65-2.89s with pooling
    # "enabled", because every call re-handshook anyway. Worse, _load_tenant_and_config
    # swallows DB errors and falls back to metadata defaults, so a corrupted
    # connection silently costs a real call its doctors/knowledge-base/system-prompt
    # instead of failing loudly.
    #
    # To actually amortise the handshake, the pool must be owned by ONE long-lived
    # event loop that outlives individual jobs, with job code submitting work to it
    # via asyncio.run_coroutine_threadsafe. That is a real refactor: EVERY worker DB
    # path has to go through it (call setup AND the call-logger's finalisation
    # writes), because one stray session on a job loop reintroduces the corruption.
    # Until that exists, 0 is the only correct value here.
    #
    # pool_pre_ping discards a connection Supabase's pooler has already closed
    # instead of failing the call with it; pool_recycle stays well under any idle
    # cutoff. Both are still right for any NON-job-executor consumer.
    try:
        _pool_size = int(os.getenv("DB_POOL_SIZE", "0") or 0)
    except ValueError:
        _pool_size = 0

    # ── Bounded connect + bounded query, always ───────────────────────────────
    #
    # Neither was set anywhere before this, and asyncpg's own defaults do not
    # save you: `timeout` (connection establishment) defaults to 60s, and
    # `command_timeout` (a single query, once connected) has NO default at all —
    # a query that never gets a reply from a stalled pooler connection hangs
    # FOREVER. Measured live, 2026-08-12: a voice CANCEL logged
    # "execute_started action=CANCEL" and then nothing — ever, for over 20
    # minutes — with no exception anywhere and no lock on the Postgres side
    # (confirmed via pg_stat_activity: nothing blocked, nothing idle-in-
    # transaction). The request never reached a state Postgres could see at
    # all, which is exactly the signature of a client stuck waiting on a
    # connection a proxy/pooler queue never serviced.
    #
    # The fix is not "retry harder" — it's "fail fast enough that the calling
    # code's own error handling (which already exists everywhere DB calls are
    # made: create_appointment, execute_booking_action, VoiceActionProcessor's
    # _execute) gets a chance to run at all." Both call paths (the awaited
    # asyncpg connect, and any single statement after connecting) are bounded
    # to single-digit seconds — long enough for Supabase's real handshake +
    # query latency (measured 1.5-4s per call, see DB_POOL_SIZE comment above),
    # short enough that a voice caller is never left in dead air for minutes
    # over a hung connection instead of a spoken "please try again."
    _connect_args = {
        "statement_cache_size": 0,
        "server_settings": {"jit": "off"},
        "timeout": 8,            # connection establishment
        "command_timeout": 8,    # any single query, once connected
    }

    if _pool_size > 0:
        logger.info(
            "DB connection pooling ENABLED (DB_POOL_SIZE=%d) — connections are reused "
            "across calls instead of re-handshaking with Supabase each time.",
            _pool_size,
        )
        engine = create_async_engine(
            DATABASE_URL,
            echo=False,
            pool_size=_pool_size,
            max_overflow=_pool_size,
            pool_pre_ping=True,
            pool_recycle=280,
            pool_timeout=10,
            connect_args=_connect_args,
        )
    else:
        engine = create_async_engine(
            DATABASE_URL,
            echo=False,
            poolclass=NullPool,
            connect_args=_connect_args,
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
        ("doctors", "is_available", "BOOLEAN DEFAULT true"),
        ("doctors", "leave_reason", "VARCHAR(500)"),
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

    # create_all() only creates missing TABLES too — on any DB where
    # 'doctors'/'appointments' already existed before this change, the new
    # partial unique indexes (doctor dedup guard, appointment conflict guard)
    # need the same idempotent treatment. Valid identical syntax on both
    # Postgres and SQLite, so no dialect branch needed here. If real
    # duplicates/conflicts still exist in this DB, this logs a warning and
    # boots anyway (same graceful-degradation policy as the column adds
    # above) — backend/scripts/find_duplicate_doctors.py and
    # find_appointment_slot_conflicts.py are the real prerequisite check.
    index_migrations = [
        ("uq_doctors_tenant_his_id",
         "CREATE UNIQUE INDEX IF NOT EXISTS uq_doctors_tenant_his_id "
         "ON doctors (tenant_id, his_doctor_id) WHERE his_doctor_id IS NOT NULL"),
        ("uq_appointments_doctor_slot_active",
         "CREATE UNIQUE INDEX IF NOT EXISTS uq_appointments_doctor_slot_active "
         "ON appointments (doctor_id, slot_time) WHERE status <> 'cancelled'"),
    ]
    for name, ddl in index_migrations:
        try:
            async with engine.begin() as conn:
                await conn.exec_driver_sql(ddl)
        except Exception as e:
            logger.warning("Lightweight index migration %s skipped: %s", name, str(e)[:200])


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
        # Superadmin "view as this clinic" sessions. MUST be listed here: this is
        # what create_all() below actually uses, and the table is the revocation
        # list every impersonated request is checked against — an unimported model
        # means no table, which means impersonation 401s everywhere.
        "backend.models.impersonation_session",
        "backend.models.doctor_availability",
    ]

    for module_path in optional:
        try:
            __import__(module_path)
        except ImportError:
            pass  # Model file doesn't exist yet - safe to skip
        except Exception as e:
            logger.warning(f"Model import warning [{module_path}]: {e}")
