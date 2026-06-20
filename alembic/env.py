import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import your application's settings to load database URLs dynamically
from app.core.config import settings

# Import your declarative Base and all models to register metadata for autogenerate
from app.core.database import Base
from app.models.officer import ExtensionOfficer
from app.models.solution_card import SolutionCard
from app.models.card_view import CardView
from app.models.raw_content import RawContent
from app.models.input_ad import InputAd

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Fetch URL from environment variable or fallback to settings module
db_url = os.environ.get("DATABASE_URL") or settings.database_url

if db_url:
    # 1. Strip out any query parameters (?sslmode=...) to prevent driver conflicts
    if "?" in db_url:
        db_url = db_url.split("?")[0]

    # 2. Normalize driver strings to asyncpg
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    
    # Override the ini configuration value dynamically
    config.set_main_option("sqlalchemy.url", db_url)

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set the target metadata for autogenerate detection
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """Synchronous helper context required to run migrations on the connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    
    # Grab the current configuration section
    section = config.get_section(config.config_ini_section, {})
    
    # 3. Explicitly pass a clean SSL context argument to the engine creator.
    # This completely overrides the backend defaults and stops SQLAlchemy from injecting channel_binding.
    connect_args = {
        "ssl": "require"
    }

    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    async with connectable.connect() as connection:
        # Run the migrations inside the async connection block
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    # Run the online migration routine using the asyncio event loop
    asyncio.run(run_migrations_online())