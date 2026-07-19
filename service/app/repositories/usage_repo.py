"""API usage logging and reporting."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ApiKey, ApiUsage


class UsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self, *, key_id: uuid.UUID, endpoint: str, status_code: int, ip: str
    ) -> None:
        self.session.add(
            ApiUsage(key_id=key_id, endpoint=endpoint, status_code=status_code, ip=ip)
        )
        await self.session.flush()

    async def usage_by_key(self) -> list[dict]:
        now = datetime.now(UTC)
        day_ago = now - timedelta(hours=24)
        month_ago = now - timedelta(days=30)

        last_24h = (
            select(
                ApiUsage.key_id,
                func.count().label("requests_last_24h"),
            )
            .where(ApiUsage.requested_at >= day_ago)
            .group_by(ApiUsage.key_id)
            .subquery()
        )
        last_30d = (
            select(
                ApiUsage.key_id,
                func.count().label("requests_last_30d"),
            )
            .where(ApiUsage.requested_at >= month_ago)
            .group_by(ApiUsage.key_id)
            .subquery()
        )

        stmt = (
            select(
                ApiKey.id,
                ApiKey.name,
                func.coalesce(last_24h.c.requests_last_24h, 0),
                func.coalesce(last_30d.c.requests_last_30d, 0),
            )
            .outerjoin(last_24h, last_24h.c.key_id == ApiKey.id)
            .outerjoin(last_30d, last_30d.c.key_id == ApiKey.id)
            .order_by(ApiKey.created_at.desc())
        )
        rows = await self.session.execute(stmt)
        return [
            {
                "key_id": key_id,
                "key_name": name,
                "requests_last_24h": last24,
                "requests_last_30d": last30,
            }
            for key_id, name, last24, last30 in rows
        ]
