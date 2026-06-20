"""
app/jobs/scheduler.py — APScheduler configuration and job registration.

Uses AsyncIOScheduler so jobs run in the same event loop as FastAPI.
Jobs are registered here and started/stopped via the FastAPI lifespan.

Job schedule
────────────
  crawl_job      → runs every CRAWL_INTERVAL_HOURS (default 24h)
                   also triggers once at startup (next_run_time=now)
  classify_job   → runs every CLASSIFY_INTERVAL_MINUTES (default 60min)
                   also triggers once at startup

Both jobs are fire-and-forget — if a run is still going when the next
one is scheduled, the new run is skipped (max_instances=1).

Logging
───────
APScheduler logs are noisy by default. We silence them below WARNING
to keep the console clean. Job-level logging is in each job module.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings

# Silence APScheduler's verbose internal logging
logging.getLogger("apscheduler").setLevel(logging.WARNING)

log = logging.getLogger("agricore.scheduler")

# Module-level scheduler instance
scheduler = AsyncIOScheduler(timezone="Africa/Nairobi")


def register_jobs() -> None:
    """Register all background jobs with the scheduler.

    Called once during app startup (in lifespan). Adding a new job:
      1. Write the job function in app/jobs/
      2. Import it here
      3. Add scheduler.add_job(...)
    """
    from app.jobs.crawl_job import crawl_job
    from app.jobs.classify_job import classify_job

    # ── Crawler ───────────────────────────────────────────────────────────────
    scheduler.add_job(
        crawl_job,
        trigger=IntervalTrigger(hours=settings.crawl_interval_hours),
        id="crawl_job",
        name="Web Crawler",
        max_instances=1,          # never run two crawls at once
        replace_existing=True,
        next_run_time=__import__("datetime").datetime.now(),  # run immediately on start
    )
    log.info(
        "Crawler job registered — interval: every %dh",
        settings.crawl_interval_hours,
    )

    # ── Classifier ────────────────────────────────────────────────────────────
    scheduler.add_job(
        classify_job,
        trigger=IntervalTrigger(minutes=settings.classify_interval_minutes),
        id="classify_job",
        name="AI Classifier",
        max_instances=1,
        replace_existing=True,
        next_run_time=__import__("datetime").datetime.now(),
    )
    log.info(
        "Classifier job registered — interval: every %dmin",
        settings.classify_interval_minutes,
    )


def start_scheduler() -> None:
    """Start the scheduler. Called during app startup."""
    register_jobs()
    scheduler.start()
    log.info("APScheduler started. Jobs: %d", len(scheduler.get_jobs()))


def stop_scheduler() -> None:
    """Gracefully stop the scheduler. Called during app shutdown."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        log.info("APScheduler stopped.")
