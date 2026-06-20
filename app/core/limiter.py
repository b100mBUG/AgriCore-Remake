"""
app/core/limiter.py — Rate limiting via slowapi.

slowapi wraps limits-library on top of FastAPI. We use Redis as the
storage backend so limits are shared across multiple uvicorn workers
(important in production). Falls back to in-memory storage if Redis
is unavailable (development convenience).

Usage in routes
───────────────
    from app.core.limiter import limiter
    from fastapi import Request

    @router.get("/cards")
    @limiter.limit("60/minute")        # per-IP
    async def list_cards(request: Request, ...):
        ...

The `request: Request` parameter must be present in the route signature
for slowapi to extract the client IP — this is a slowapi requirement,
not optional.

Default limit is set from settings.rate_limit_per_minute and applied
globally in main.py via app.state.limiter + SlowAPIMiddleware.
Individual routes can override with their own @limiter.limit decorator.
"""

import logging

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

log = logging.getLogger("agricore.limiter")


def _get_storage_uri() -> str:
    """Return Redis URI for slowapi storage, or memory:// fallback."""
    if settings.redis_url:
        return settings.redis_url
    log.warning("No REDIS_URL configured — rate limiter using in-memory storage.")
    return "memory://"


# Module-level limiter instance — import this everywhere.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.rate_limit_per_minute}/minute"],
    storage_uri=_get_storage_uri(),
)
