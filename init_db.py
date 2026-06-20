from app.core.database import engine
from app.core.database import Base


async def init_db() -> None:
    """Create all tables from metadata.

    Used during development and in the lifespan for SQLite.
    In production, Alembic migrations handle schema changes — this
    function should NOT be called there.
    """
    # Import all models so their table definitions land in Base.metadata
    import app.models  # noqa: F401 — triggers __init__.py imports

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)