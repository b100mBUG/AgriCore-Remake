"""
alembic/env.py — Alembic migration environment.

Configured for async SQLAlchemy (asyncpg/aiosqlite).
Alembic itself is synchronous, so we use run_sync to execute migrations.

To create a new migration:
    alembic revision --autogenerate -m "describe your change"

To apply migrations:
    alembic upgrade head

To rollback one step:
    alembic downgrade -1

The target_metadata points to Base.metadata after all models are
imported — this is what Alembic diffs against the live DB schema.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import app config and Base (which pulls in all models via app/models/__init__.py)
from app.core.config import settings
from app.core.database import Base
import app.models  # noqa: F401 — ensures all tables are in Base.metadata

# ── Alembic config ────────────────────────────────────────────────────────────
config = context.config

# Override sqlalchemy.url with our settings value so alembic.ini doesn't
# need to contain secrets
config.set_main_option("sqlalchemy.url", settings.database_url)

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


# ── Offline mode (generates SQL without a DB connection) ──────────────────────

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online mode (connects to DB and applies migrations) ───────────────────────

def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,       # detect column type changes
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations via run_sync."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,   # no connection pooling for migrations
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


# ── Entry point ───────────────────────────────────────────────────────────────
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
