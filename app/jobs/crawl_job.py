"""
app/jobs/crawl_job.py — APScheduler wrapper for the web crawler.

Thin wrapper that calls the crawler service and handles top-level
exceptions so a crash doesn't kill the scheduler.
"""

import logging

log = logging.getLogger("agricore.jobs.crawl")


async def crawl_job() -> None:
    """APScheduler entry point for the web crawler.

    Wraps the crawler service in a try/except so a crash in one run
    doesn't prevent future runs. All detailed logging is in the service.
    """
    log.info("Crawl job triggered.")
    try:
        from app.services.crawler import run_crawler
        await run_crawler()
    except Exception as exc:
        log.exception("Crawl job failed with unhandled exception: %s", exc)
