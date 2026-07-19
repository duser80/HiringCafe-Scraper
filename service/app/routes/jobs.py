"""Public v1 job endpoints (API-key authenticated, rate limited)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session, get_session_factory
from ..errors import ApiError
from ..models import ApiKey
from ..ratelimit import enforce_rate_limit
from ..repositories.jobs_repo import JobsRepository
from ..repositories.keys_repo import KeysRepository
from ..repositories.scrape_runs_repo import ScrapeRunsRepository
from ..repositories.usage_repo import UsageRepository
from ..schemas import (
    SENIORITY_LEVELS,
    WORKPLACE_TYPES,
    JobListMeta,
    JobListResponse,
    JobOut,
    JobResponse,
    StatsOut,
    StatsResponse,
)

router = APIRouter(prefix="/v1", tags=["jobs"])

MAX_PAGE_SIZE = 100


async def _log_usage(key_id: uuid.UUID, endpoint: str, status_code: int, ip: str) -> None:
    """Record usage in its own session so it never blocks the response path."""
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            await UsageRepository(session).record(
                key_id=key_id, endpoint=endpoint, status_code=status_code, ip=ip
            )
            await KeysRepository(session).touch_last_used(key_id)


def _track(
    background: BackgroundTasks, request: Request, api_key: ApiKey, status_code: int = 200
) -> None:
    ip = request.client.host if request.client else ""
    background.add_task(_log_usage, api_key.id, request.url.path, status_code, ip)


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    request: Request,
    background: BackgroundTasks,
    q: str | None = Query(default=None, max_length=200, description="Full-text search over title and company"),
    location: str | None = Query(default=None, max_length=120, description="Substring match on location fields"),
    country: str | None = Query(default=None, min_length=2, max_length=2, description="ISO country code, e.g. US"),
    workplace_type: str | None = Query(default=None),
    seniority: str | None = Query(default=None),
    commitment: str | None = Query(default=None, max_length=40, description="e.g. Full Time"),
    category: str | None = Query(default=None, max_length=80),
    company: str | None = Query(default=None, max_length=120),
    salary_min: float | None = Query(default=None, ge=0, description="Minimum yearly salary"),
    posted_after: datetime | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, max_length=200),
    api_key: ApiKey = Depends(enforce_rate_limit),
    session: AsyncSession = Depends(get_session),
) -> JobListResponse:
    if workplace_type is not None and workplace_type not in WORKPLACE_TYPES:
        raise ApiError(400, "invalid_parameter",
                       f"workplace_type must be one of {WORKPLACE_TYPES}.")
    if seniority is not None and seniority not in SENIORITY_LEVELS:
        raise ApiError(400, "invalid_parameter",
                       f"seniority must be one of {SENIORITY_LEVELS}.")

    repo = JobsRepository(session)
    jobs, next_cursor = await repo.list_jobs(
        limit=limit,
        cursor=cursor,
        q=q,
        location=location,
        country=country,
        workplace_type=workplace_type,
        seniority=seniority,
        commitment=commitment,
        category=category,
        company=company,
        salary_min=salary_min,
        posted_after=posted_after,
    )
    _track(background, request, api_key)
    return JobListResponse(
        data=[JobOut.model_validate(job) for job in jobs],
        meta=JobListMeta(next_cursor=next_cursor, count=len(jobs)),
    )


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: uuid.UUID,
    request: Request,
    background: BackgroundTasks,
    api_key: ApiKey = Depends(enforce_rate_limit),
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    job = await JobsRepository(session).get_by_id(job_id)
    if job is None:
        raise ApiError(404, "not_found", "Job not found.")
    _track(background, request, api_key)
    return JobResponse(data=JobOut.model_validate(job))


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    request: Request,
    background: BackgroundTasks,
    api_key: ApiKey = Depends(enforce_rate_limit),
    session: AsyncSession = Depends(get_session),
) -> StatsResponse:
    stats = await JobsRepository(session).stats()
    last_run = await ScrapeRunsRepository(session).last_successful()
    _track(background, request, api_key)
    return StatsResponse(
        data=StatsOut(
            active_jobs=stats["active_jobs"],
            companies=stats["companies"],
            last_successful_scrape_at=last_run.finished_at if last_run else None,
            last_scrape_status=last_run.status if last_run else None,
        )
    )
