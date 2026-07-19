"""Worker entrypoint: run ingest cycles on a fixed schedule.

Usage: python -m service.worker.scheduler
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import text

from service.app.config import get_settings
from service.app.db import dispose_engine, get_engine
from service.worker.ingest import run_all_slices_async

logger = logging.getLogger("service.worker")


async def wait_for_database(max_attempts: int = 30, delay_seconds: float = 2.0) -> None:
    """Block until Postgres is reachable and migrated (jobs table exists)."""
    for attempt in range(1, max_attempts + 1):
        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1 FROM jobs LIMIT 1"))
            logger.info("Database is ready.")
            return
        except Exception as exc:
            logger.info(
                "Waiting for database (attempt %d/%d): %s", attempt, max_attempts, exc
            )
            await asyncio.sleep(delay_seconds)
    raise RuntimeError("Database did not become ready in time.")


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )

    await wait_for_database()

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_all_slices_async,
        IntervalTrigger(minutes=settings.scrape_interval_minutes),
        id="ingest-cycle",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        "Worker started: ingest every %d minutes (plus one immediate run).",
        settings.scrape_interval_minutes,
    )

    # Immediate first run so a fresh deployment has data right away.
    await run_all_slices_async()

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        scheduler.shutdown(wait=False)
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
