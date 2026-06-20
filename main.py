"""
app/main.py — FastAPI application factory.

Lifespan order (startup):
  1. init_db()          → create tables (dev/SQLite only)
  2. start_scheduler()  → register + start APScheduler jobs
  3. App ready

Lifespan order (shutdown):
  1. stop_scheduler()   → graceful APScheduler stop
  2. close_redis()      → close Redis connection pool
  3. engine.dispose()   → close all DB connections

Middleware stack (outermost → innermost):
  CORSMiddleware → SlowAPIMiddleware (rate limiting) → Routes

All route prefixes are defined in the router files. This file stays thin.
"""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.config import settings
from app.core.database import engine
from app.core.limiter import limiter
from app.core.redis import close_redis
from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.routes import all_routers

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.DEBUG if not settings.is_production else logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("agricore.main")


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown of long-lived resources."""
    log.info("AgriCore starting up (env=%s, version=%s)", settings.app_env, settings.app_version)

    # Create tables in dev/SQLite — Alembic handles this in production
    if not settings.is_production:
        #await init_db()
        log.info("Database tables initialised (dev mode).")

    # Start background jobs
    start_scheduler()

    yield   # ← App is live and serving requests here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    log.info("AgriCore shutting down...")
    stop_scheduler()
    await close_redis()
    await engine.dispose()
    log.info("Shutdown complete.")


# ── App factory ────────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title="AgriCore API",
        description=(
            "Knowledge platform for Kenyan smallholder farmers. "
            "AI-powered solution cards, extension officer directory, "
            "and farming weather briefs."
        ),
        version=settings.app_version,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ── Rate limiter ──────────────────────────────────────────────────────────
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    # ── CORS ──────────────────────────────────────────────────────────────────
    # allow_credentials=True is only valid (and only makes sense) once real
    # origins are configured — browsers reject "*" + credentials outright.
    # See Settings.origins_list for why an unset/"*" config yields [] here.
    _origins = settings.origins_list
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=bool(_origins),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if not _origins:
        log.warning(
            "ALLOWED_ORIGINS is not set to specific origins — CORS will block "
            "all cross-origin browser requests. Set it before deploying a web frontend."
        )

    # ── Routers ───────────────────────────────────────────────────────────────
    for router in all_routers:
        app.include_router(router, prefix="/api/v1")

    # ── Health check ──────────────────────────────────────────────────────────
    @app.get("/health", tags=["System"], include_in_schema=False)
    async def health() -> dict:
        """Uptime check — used by Render / Railway health probes."""
        return {"status": "ok", "version": settings.app_version, "env": settings.app_env}

    # ── Global exception handler ──────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled exception on %s %s: %s", request.method, request.url, exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An unexpected error occurred. Please try again."},
        )

    log.info("AgriCore app created. Routes registered: %d", len(all_routers))
    return app


app = create_app()
