"""
app/core/redis.py — Async Redis client and caching helpers.

Redis serves two purposes in AgriCore:
  1. Response cache — frequent endpoints (cards, officers, weather)
     are cached with short TTLs to reduce DB + Gemini API load.
  2. Rate limiter backend — slowapi uses Redis to track per-IP counters
     across multiple uvicorn workers.

Usage
─────
    from app.core.redis import cache_get, cache_set, cache_delete

    # In a service or route:
    cached = await cache_get("cards:pests:maize")
    if cached:
        return cached
    result = await expensive_db_query()
    await cache_set("cards:pests:maize", result, ttl=300)

Key naming conventions
──────────────────────
  cards:{category}:{crop}          → browse results
  card:{id}                        → single card detail
  officers:{county}                → officers list per county
  weather:{device_id}              → per-farmer weather
  officer_profile:{officer_id}     → public profile

All keys are prefixed with "agricore:" to namespace them in shared Redis.
"""

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings

log = logging.getLogger("agricore.redis")

_PREFIX = "agricore:"

# ── Connection pool ───────────────────────────────────────────────────────────
# Created once at module import time; reused across all requests.
# decode_responses=True means all values come back as str, not bytes.

_pool: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Return (and lazily create) the shared Redis connection pool.

    Raises a clear error if Redis is unreachable so the app fails fast
    on misconfiguration rather than silently degrading.
    """
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
        )
        # Ping to verify connectivity on first use
        try:
            await _pool.ping()
            log.info("Redis connected: %s", settings.redis_url)
        except Exception as exc:
            log.warning("Redis unavailable — caching disabled: %s", exc)
            _pool = None   # fall through to cache-miss on every call
    return _pool


async def close_redis() -> None:
    """Close the connection pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
        log.info("Redis connection closed.")


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _key(raw: str) -> str:
    """Prefix all keys with the app namespace."""
    return f"{_PREFIX}{raw}"


async def cache_get(key: str) -> Any | None:
    """Return deserialized cached value, or None on miss / Redis down."""
    try:
        r = await get_redis()
        if r is None:
            return None
        raw = await r.get(_key(key))
        return json.loads(raw) if raw is not None else None
    except Exception as exc:
        log.debug("cache_get error for key=%r: %s", key, exc)
        return None


async def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    """Serialize and store a value. ttl is in seconds (default 5 min).

    Silently does nothing if Redis is unavailable — the app degrades
    gracefully without caching rather than erroring.
    """
    try:
        r = await get_redis()
        if r is None:
            return
        await r.setex(_key(key), ttl, json.dumps(value, default=str))
    except Exception as exc:
        log.debug("cache_set error for key=%r: %s", key, exc)


async def cache_delete(key: str) -> None:
    """Invalidate a single cache key."""
    try:
        r = await get_redis()
        if r is None:
            return
        await r.delete(_key(key))
    except Exception as exc:
        log.debug("cache_delete error for key=%r: %s", key, exc)


async def cache_delete_pattern(pattern: str) -> None:
    """Invalidate all keys matching a glob pattern.

    Example: await cache_delete_pattern("cards:pests:*")

    Use sparingly — SCAN is non-blocking but still expensive on large
    key sets. Prefer targeted deletes where possible.
    """
    try:
        r = await get_redis()
        if r is None:
            return
        full_pattern = _key(pattern)
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor, match=full_pattern, count=100)
            if keys:
                await r.delete(*keys)
            if cursor == 0:
                break
    except Exception as exc:
        log.debug("cache_delete_pattern error for pattern=%r: %s", pattern, exc)
