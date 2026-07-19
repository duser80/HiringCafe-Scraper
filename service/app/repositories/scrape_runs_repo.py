"""Scrape run bookkeeping."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ScrapeRun


class ScrapeRunsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start(self, slice_name: str) -> ScrapeRun:
        run = ScrapeRun(slice_name=slice_name, status="running")
        self.session.add(run)
        await self.session.flush()
        return run

    async def finish(
        self,
        run_id: uuid.UUID,
        *,
        status: str,
        pages_fetched: int,
        jobs_seen: int,
        jobs_new: int,
        jobs_updated: int,
        error: str = "",
    ) -> None:
        await self.session.execute(
            update(ScrapeRun)
            .where(ScrapeRun.id == run_id)
            .values(
                status=status,
                finished_at=datetime.now(UTC),
                pages_fetched=pages_fetched,
                jobs_seen=jobs_seen,
                jobs_new=jobs_new,
                jobs_updated=jobs_updated,
                error=error[:2000],
            )
        )

    async def latest(self, limit: int = 20) -> list[ScrapeRun]:
        result = await self.session.execute(
            select(ScrapeRun).order_by(ScrapeRun.started_at.desc()).limit(limit)
        )
        return list(result.scalars())

    async def last_successful(self) -> ScrapeRun | None:
        result = await self.session.execute(
            select(ScrapeRun)
            .where(ScrapeRun.status == "success")
            .order_by(ScrapeRun.finished_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
