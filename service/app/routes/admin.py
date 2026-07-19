"""Admin endpoints, guarded by the ADMIN_TOKEN bearer token."""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import KEY_PREFIX, generate_api_key, hash_api_key, require_admin
from ..db import get_session
from ..errors import ApiError
from ..repositories.keys_repo import KeysRepository
from ..repositories.scrape_runs_repo import ScrapeRunsRepository
from ..repositories.usage_repo import UsageRepository
from ..schemas import (
    ApiKeyCreatedOut,
    ApiKeyCreatedResponse,
    ApiKeyCreateIn,
    ApiKeyListResponse,
    ApiKeyOut,
    KeyUsageOut,
    ScrapeRunListResponse,
    ScrapeRunOut,
    ScrapeTriggerResponse,
    UsageResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post("/keys", response_model=ApiKeyCreatedResponse, status_code=201)
async def create_key(
    payload: ApiKeyCreateIn,
    session: AsyncSession = Depends(get_session),
) -> ApiKeyCreatedResponse:
    raw_key = generate_api_key()
    async with session.begin():
        api_key = await KeysRepository(session).create(
            key_hash=hash_api_key(raw_key),
            prefix=KEY_PREFIX.rstrip("_"),
            last_four=raw_key[-4:],
            name=payload.name,
            owner=payload.owner,
            rate_limit_per_minute=payload.rate_limit_per_minute,
            expires_at=payload.expires_at,
        )
        created = ApiKeyCreatedOut(
            id=api_key.id,
            api_key=raw_key,  # returned exactly once; only the hash is stored
            prefix=api_key.prefix,
            last_four=api_key.last_four,
            name=api_key.name,
            rate_limit_per_minute=api_key.rate_limit_per_minute,
            expires_at=api_key.expires_at,
        )
    return ApiKeyCreatedResponse(data=created)


@router.get("/keys", response_model=ApiKeyListResponse)
async def list_keys(session: AsyncSession = Depends(get_session)) -> ApiKeyListResponse:
    keys = await KeysRepository(session).list_all()
    return ApiKeyListResponse(data=[ApiKeyOut.model_validate(k) for k in keys])


@router.post("/keys/{key_id}/revoke", response_model=ApiKeyListResponse)
async def revoke_key(
    key_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> ApiKeyListResponse:
    async with session.begin():
        repo = KeysRepository(session)
        revoked = await repo.revoke(key_id)
        if not revoked:
            existing = await repo.get_by_id(key_id)
            if existing is None:
                raise ApiError(404, "not_found", "API key not found.")
            raise ApiError(409, "already_revoked", "API key is already revoked.")
        keys = await repo.list_all()
    return ApiKeyListResponse(data=[ApiKeyOut.model_validate(k) for k in keys])


@router.get("/usage", response_model=UsageResponse)
async def usage(session: AsyncSession = Depends(get_session)) -> UsageResponse:
    rows = await UsageRepository(session).usage_by_key()
    return UsageResponse(data=[KeyUsageOut(**row) for row in rows])


@router.get("/scrape/runs", response_model=ScrapeRunListResponse)
async def scrape_runs(session: AsyncSession = Depends(get_session)) -> ScrapeRunListResponse:
    runs = await ScrapeRunsRepository(session).latest()
    return ScrapeRunListResponse(data=[ScrapeRunOut.model_validate(r) for r in runs])


@router.post("/scrape/trigger", response_model=ScrapeTriggerResponse, status_code=202)
async def trigger_scrape() -> ScrapeTriggerResponse:
    """Kick one full ingest cycle in the background (runs in the API process)."""
    from service.worker.ingest import run_all_slices_async

    task = asyncio.create_task(run_all_slices_async())

    def _log_result(t: asyncio.Task) -> None:
        exc = t.exception()
        if exc:
            logger.error("Manually triggered ingest failed: %s", exc)

    task.add_done_callback(_log_result)
    return ScrapeTriggerResponse(data={"status": "started"})
