"""Public health endpoint for load balancers and monitoring."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ..db import get_session_factory
from ..ratelimit import get_redis

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> JSONResponse:
    checks = {"database": "ok", "redis": "ok"}
    healthy = True

    try:
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        checks["database"] = "unavailable"
        healthy = False

    try:
        await get_redis().ping()
    except Exception:
        checks["redis"] = "unavailable"
        healthy = False

    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ok" if healthy else "degraded", "checks": checks},
    )
