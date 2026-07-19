"""Incremental ingestion: scrape HiringCafe slices and upsert into Postgres.

Design:
- Each slice is walked newest-first (date-sorted). A slice stops early when an
  entire page contains only already-known jobs, so steady-state runs are cheap.
- Every page's hits are upserted in one transaction (idempotent by object_id).
- The whole cycle runs under a Postgres advisory lock so concurrent runs
  (scheduler + manual trigger, or two worker replicas) can never overlap.
- Jobs unseen for DEACTIVATE_AFTER_DAYS are soft-deactivated, never deleted.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import text

from hiringcafe_scraper.client import HiringCafeClient, HiringCafeError
from service.app.config import get_settings
from service.app.db import get_engine, get_session_factory
from service.app.repositories.jobs_repo import JobsRepository
from service.app.repositories.scrape_runs_repo import ScrapeRunsRepository
from service.worker.slices import SearchSlice, load_slices

logger = logging.getLogger("service.worker")

INGEST_ADVISORY_LOCK_KEY = 0x48435F494E47  # arbitrary constant: "HC_ING"


def _strip_nul(value):
    """Recursively remove NUL (\\u0000) characters - Postgres rejects them in
    TEXT and JSONB, and HiringCafe data occasionally contains them."""
    if isinstance(value, str):
        return value.replace("\x00", "").replace("\\u0000", "")
    if isinstance(value, list):
        return [_strip_nul(item) for item in value]
    if isinstance(value, dict):
        return {key: _strip_nul(item) for key, item in value.items()}
    return value


def _parse_published_at(v5: dict) -> datetime:
    raw = v5.get("estimated_publish_date")
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
        except ValueError:
            pass
    return datetime.now(UTC)


def map_hit(hit: dict) -> dict | None:
    """Map one raw HiringCafe hit to Job column values. None if unusable."""
    hit = _strip_nul(hit)
    object_id = hit.get("objectID") or hit.get("id")
    if not object_id:
        return None

    v5 = hit.get("v5_processed_job_data") or {}
    job_info = hit.get("job_information") or {}

    title = job_info.get("title") or v5.get("core_job_title") or ""
    if not title:
        return None

    countries = v5.get("workplace_countries") or []
    states = v5.get("workplace_states") or []
    cities = v5.get("workplace_cities") or []
    commitment = v5.get("commitment") or []
    tools = v5.get("technical_tools") or []

    return {
        "object_id": object_id,
        "title": title,
        "company": v5.get("company_name") or "",
        "location_formatted": v5.get("formatted_workplace_location") or "",
        "country": countries[0] if countries else "",
        "state": states[0] if states else "",
        "city": cities[0] if cities else "",
        "workplace_type": v5.get("workplace_type") or "",
        "seniority": v5.get("seniority_level") or "",
        "commitment": commitment if isinstance(commitment, list) else [],
        "category": v5.get("job_category") or "",
        "role_type": v5.get("role_type") or "",
        "salary_min_yearly": v5.get("yearly_min_compensation"),
        "salary_max_yearly": v5.get("yearly_max_compensation"),
        "salary_currency": v5.get("listed_compensation_currency") or "",
        "salary_frequency": v5.get("listed_compensation_frequency") or "",
        "min_yoe": v5.get("min_industry_and_role_yoe"),
        "technical_tools": tools if isinstance(tools, list) else [],
        "requirements_summary": v5.get("requirements_summary") or "",
        "apply_url": hit.get("apply_url") or "",
        "source_ats": hit.get("source") or "",
        "board_token": hit.get("board_token") or "",
        "published_at": _parse_published_at(v5),
        "raw": hit,
    }


async def _upsert_page(hits: list[dict]) -> tuple[int, int, int]:
    """Upsert one page of hits. Returns (seen, new, updated).

    Fast path: the whole page in one transaction. If that fails (one bad row
    aborts a Postgres transaction), fall back to row-by-row so a single bad
    record can never sink the rest of the page.
    """
    mapped = [values for hit in hits if (values := map_hit(hit)) is not None]

    factory = get_session_factory()
    try:
        seen = new = updated = 0
        async with factory() as session:
            async with session.begin():
                repo = JobsRepository(session)
                for values in mapped:
                    inserted = await repo.upsert_job(values)
                    seen += 1
                    new, updated = (new + 1, updated) if inserted else (new, updated + 1)
        return seen, new, updated
    except Exception as exc:
        logger.warning("Page-level upsert failed (%s); retrying row-by-row.", exc)

    seen = new = updated = 0
    for values in mapped:
        try:
            async with factory() as session:
                async with session.begin():
                    inserted = await JobsRepository(session).upsert_job(values)
            seen += 1
            new, updated = (new + 1, updated) if inserted else (new, updated + 1)
        except Exception as exc:
            logger.error("Skipping unusable job %s: %s", values.get("object_id"), exc)
    return seen, new, updated


async def run_slice(client: HiringCafeClient, search_slice: SearchSlice) -> dict:
    """Run one slice incrementally. Returns run stats."""
    factory = get_session_factory()

    async with factory() as session:
        async with session.begin():
            run = await ScrapeRunsRepository(session).start(search_slice.name)
            run_id = run.id

    pages_fetched = jobs_seen = jobs_new = jobs_updated = 0
    status, error = "success", ""

    try:
        seen_in_run: set[str] = set()
        for page in range(search_slice.max_pages):
            # The scraper client is sync (requests) - run it off the event loop.
            page_props = await asyncio.to_thread(
                client.search_page, search_slice.search_state, page
            )
            pages_fetched += 1
            hits = page_props.get("ssrHits") or []
            if not hits:
                break

            # Dedupe within the run (pages can overlap due to collapsing).
            fresh_hits = []
            page_object_ids = []
            for hit in hits:
                object_id = hit.get("objectID") or hit.get("id")
                if not object_id or object_id in seen_in_run:
                    continue
                seen_in_run.add(object_id)
                fresh_hits.append(hit)
                page_object_ids.append(object_id)

            if not fresh_hits:
                if page_props.get("ssrIsLastPage"):
                    break
                continue

            # Early-stop check BEFORE upserting (upsert refreshes last_seen_at).
            factory = get_session_factory()
            async with factory() as session:
                known = await JobsRepository(session).existing_object_ids(page_object_ids)
            all_known = len(known) == len(page_object_ids)

            seen, new, upd = await _upsert_page(fresh_hits)
            jobs_seen += seen
            jobs_new += new
            jobs_updated += upd

            logger.info(
                "slice=%s page=%d seen=%d new=%d known=%d",
                search_slice.name, page, seen, new, len(known),
            )

            if all_known and page > 0:
                logger.info("slice=%s: page %d fully known - early stop", search_slice.name, page)
                break
            if page_props.get("ssrIsLastPage"):
                break
    except HiringCafeError as exc:
        status, error = "failed", str(exc)
        logger.error("slice=%s failed: %s", search_slice.name, exc)
    except Exception as exc:
        status, error = "failed", f"unexpected: {exc}"
        logger.exception("slice=%s failed unexpectedly", search_slice.name)

    async with factory() as session:
        async with session.begin():
            await ScrapeRunsRepository(session).finish(
                run_id,
                status=status,
                pages_fetched=pages_fetched,
                jobs_seen=jobs_seen,
                jobs_new=jobs_new,
                jobs_updated=jobs_updated,
                error=error,
            )

    return {
        "slice": search_slice.name,
        "status": status,
        "pages": pages_fetched,
        "seen": jobs_seen,
        "new": jobs_new,
        "updated": jobs_updated,
    }


async def run_all_slices_async() -> list[dict]:
    """Run a full ingest cycle under a Postgres advisory lock."""
    settings = get_settings()
    factory = get_session_factory()

    # Hold the advisory lock on a dedicated connection for the whole cycle.
    engine_conn = await get_engine().connect()
    locked = False
    try:
        locked = (
            await engine_conn.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": INGEST_ADVISORY_LOCK_KEY}
            )
        ).scalar_one()
        if not locked:
            logger.warning("Another ingest cycle is already running; skipping.")
            return [{"status": "skipped", "reason": "already_running"}]

        slices = load_slices(settings.slices_json, settings.scrape_max_pages_per_slice)
        client = HiringCafeClient(
            delay=settings.scrape_delay_seconds,
            proxy_url=settings.proxy_url or None,
        )

        results = []
        for search_slice in slices:
            results.append(await run_slice(client, search_slice))

        async with factory() as session:
            async with session.begin():
                deactivated = await JobsRepository(session).deactivate_stale(
                    settings.deactivate_after_days
                )
        if deactivated:
            logger.info("Deactivated %d stale jobs.", deactivated)

        logger.info("Ingest cycle complete: %s", results)
        return results
    finally:
        if locked:
            await engine_conn.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": INGEST_ADVISORY_LOCK_KEY}
            )
        await engine_conn.close()
