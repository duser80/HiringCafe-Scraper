"""Per-API-key rate limiting backed by Redis.

Fixed-window counter per key per minute: INCR + EXPIRE, atomic via pipeline.
Simple, O(1) memory per key, and accurate enough for per-minute API quotas.
Returns Retry-After so clients can back off correctly.
"""

from __future__ import annotations

import time

import redis.asyncio as aioredis
from fastapi import Depends, Request

from .auth import require_api_key
from .config import get_settings
from .errors import ApiError
from .models import ApiKey

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            get_settings().redis_url, encoding="utf-8", decode_responses=True
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


async def enforce_rate_limit(
    request: Request,
    api_key: ApiKey = Depends(require_api_key),
) -> ApiKey:
    """Dependency composing auth + rate limiting. Returns the authenticated key."""
    window = int(time.time() // 60)
    counter_key = f"ratelimit:{api_key.id}:{window}"

    redis = get_redis()
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(counter_key)
        pipe.expire(counter_key, 120)
        count, _ = await pipe.execute()

    limit = api_key.rate_limit_per_minute
    request.state.rate_limit = limit
    request.state.rate_limit_remaining = max(0, limit - int(count))

    if int(count) > limit:
        retry_after = 60 - int(time.time() % 60)
        raise ApiError(
            429,
            "rate_limited",
            f"Rate limit of {limit} requests/minute exceeded.",
            headers={"Retry-After": str(retry_after)},
        )
    return api_key
