"""
app/jobs/classify_job.py — APScheduler wrapper for the AI classifier.

Thin wrapper that calls the classifier service and handles top-level
exceptions so a crash doesn't kill the scheduler.
"""

import logging

log = logging.getLogger("agricore.jobs.classify")


async def classify_job() -> None:
    """APScheduler entry point for the AI classifier.

    Wraps the classifier service in a try/except so a crash in one run
    doesn't prevent future runs. All detailed logging is in the service.
    """
    log.info("Classify job triggered.")
    try:
        from app.services.classifier import run_classifier
        await run_classifier()
    except Exception as exc:
        log.exception("Classify job failed with unhandled exception: %s", exc)
