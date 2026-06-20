"""
app/core/database.py — Async SQLAlchemy setup.

Provides:
  - engine          : AsyncEngine (created once at import time)
  - AsyncSessionLocal: session factory
  - get_db()        : FastAPI dependency — yields a session per request
  - init_db()       : creates all tables (used in lifespan, not Alembic)

Architecture note
─────────────────
Sessions are created per-request via the FastAPI dependency `get_db`.
Background jobs (crawler, classifier) use `AsyncSessionLocal` directly
because they run outside request context — see app/jobs/*.

SQLite vs PostgreSQL
────────────────────
connect_args differ between drivers:
  asyncpg (Postgres) — ssl=True in production, nothing in dev
  aiosqlite (SQLite)  — check_same_thread not applicable for async,
                        but we skip ssl entirely
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


# ── Declarative base ──────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """Shared base class for all ORM models.

    Import this — not sqlalchemy's Base — in every model file so
    Alembic can auto-detect tables via Base.metadata.
    """
    pass


# ── Engine ────────────────────────────────────────────────────────────────────

def _build_connect_args() -> dict:
    """Return driver-appropriate connect_args.

    asyncpg needs ssl=True in production Postgres deployments (Supabase,
    Neon, Render). aiosqlite ignores connect_args entirely so we pass {}.
    """
    if settings.is_sqlite:
        return {}
    # PostgreSQL via asyncpg
    if settings.is_production:
        return {"ssl": True}
    return {}


engine = create_async_engine(
    settings.database_url,
    connect_args=_build_connect_args(),
    echo=not settings.is_production,   # log SQL in dev, silent in prod
    future=True,
    pool_pre_ping=True,                # detect stale connections
)


# ── Session factory ───────────────────────────────────────────────────────────

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,   # keep objects accessible after commit
    autoflush=False,
    autocommit=False,
)


# ── FastAPI dependency ────────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields one session per request.

    Usage in a route:
        async def my_route(db: AsyncSession = Depends(get_db)):
            ...

    Commits on clean exit, rolls back on any exception.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Table initialisation (dev/test only) ──────────────────────────────────────


