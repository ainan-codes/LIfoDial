"""
backend/alembic/env.py — Alembic migration environment.

Uses async SQLAlchemy engine (asyncpg) for online migrations,
but _run_alembic_migrations() in db.py uses a sync psycopg2 URL
for the stamp/upgrade check because Alembic's programmatic API
is synchronous.
"""
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from backend.db import Base, DATABASE_URL

# Import all models so metadata is fully populated
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
from backend.models.embed_analytics import EmbedEvent           # noqa: F401
from backend.models.bulk_call import BulkCallCampaign           # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = DATABASE_URL

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
