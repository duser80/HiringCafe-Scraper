"""Job queries: full-text search, filters, keyset pagination, upserts."""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import ApiError
from ..models import Job

# Matches the ix_jobs_fts GIN index expression exactly so Postgres can use it.
FTS_PREDICATE = (
    "to_tsvector('english', coalesce(title,'') || ' ' || coalesce(company,'')) "
    "@@ websearch_to_tsquery('english', :fts_query)"
)


def encode_cursor(published_at: datetime, job_id: uuid.UUID) -> str:
    raw = f"{published_at.isoformat()}|{job_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        published_str, id_str = raw.split("|", 1)
        return datetime.fromisoformat(published_str), uuid.UUID(id_str)
    except (ValueError, binascii.Error) as exc:
        raise ApiError(400, "invalid_cursor", "The cursor parameter is malformed.") from exc


class JobsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Reads (public API)
    # ------------------------------------------------------------------

    def _apply_filters(
        self,
        stmt: Select,
        *,
        q: str | None,
        location: str | None,
        country: str | None,
        workplace_type: str | None,
        seniority: str | None,
        commitment: str | None,
        category: str | None,
        company: str | None,
        salary_min: float | None,
        posted_after: datetime | None,
        include_inactive: bool,
    ) -> Select:
        if not include_inactive:
            stmt = stmt.where(Job.is_active.is_(True))
        if q:
            stmt = stmt.where(text(FTS_PREDICATE).bindparams(fts_query=q))
        if location:
            pattern = f"%{location}%"
            stmt = stmt.where(
                Job.location_formatted.ilike(pattern)
                | Job.state.ilike(pattern)
                | Job.city.ilike(pattern)
                | Job.country.ilike(pattern)
            )
        if country:
            stmt = stmt.where(func.upper(Job.country) == country.upper())
        if workplace_type:
            stmt = stmt.where(Job.workplace_type == workplace_type)
        if seniority:
            stmt = stmt.where(Job.seniority == seniority)
        if commitment:
            stmt = stmt.where(Job.commitment.op("?")(commitment))
        if category:
            stmt = stmt.where(Job.category == category)
        if company:
            stmt = stmt.where(Job.company.ilike(f"%{company}%"))
        if salary_min is not None:
            stmt = stmt.where(Job.salary_max_yearly >= salary_min)
        if posted_after is not None:
            stmt = stmt.where(Job.published_at >= posted_after)
        return stmt

    async def list_jobs(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        q: str | None = None,
        location: str | None = None,
        country: str | None = None,
        workplace_type: str | None = None,
        seniority: str | None = None,
        commitment: str | None = None,
        category: str | None = None,
        company: str | None = None,
        salary_min: float | None = None,
        posted_after: datetime | None = None,
        include_inactive: bool = False,
    ) -> tuple[list[Job], str | None]:
        """Keyset-paginated job listing. Returns (jobs, next_cursor)."""
        stmt = select(Job)
        stmt = self._apply_filters(
            stmt,
            q=q, location=location, country=country,
            workplace_type=workplace_type, seniority=seniority,
            commitment=commitment, category=category, company=company,
            salary_min=salary_min, posted_after=posted_after,
            include_inactive=include_inactive,
        )

        if cursor:
            cursor_published, cursor_id = decode_cursor(cursor)
            stmt = stmt.where(
                tuple_(Job.published_at, Job.id) < (cursor_published, cursor_id)
            )

        stmt = stmt.order_by(Job.published_at.desc(), Job.id.desc()).limit(limit + 1)
        rows = list((await self.session.execute(stmt)).scalars())

        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            last = rows[-1]
            next_cursor = encode_cursor(last.published_at, last.id)
        return rows, next_cursor

    async def get_by_id(self, job_id: uuid.UUID) -> Job | None:
        return await self.session.get(Job, job_id)

    async def stats(self) -> dict:
        active_jobs = (
            await self.session.execute(
                select(func.count()).select_from(Job).where(Job.is_active.is_(True))
            )
        ).scalar_one()
        companies = (
            await self.session.execute(
                select(func.count(func.distinct(Job.company))).where(Job.is_active.is_(True))
            )
        ).scalar_one()
        return {"active_jobs": active_jobs, "companies": companies}

    # ------------------------------------------------------------------
    # Writes (worker)
    # ------------------------------------------------------------------

    async def existing_object_ids(self, object_ids: list[str]) -> set[str]:
        if not object_ids:
            return set()
        rows = await self.session.execute(
            select(Job.object_id).where(Job.object_id.in_(object_ids))
        )
        return {r[0] for r in rows}

    async def upsert_job(self, values: dict) -> bool:
        """Insert or refresh one job. Returns True when the row was newly inserted."""
        now = datetime.now(UTC)
        insert_values = {**values, "last_seen_at": now, "is_active": True}

        update_values = {
            key: insert_values[key]
            for key in insert_values
            if key not in ("object_id", "first_seen_at")
        }
        update_values["updated_at"] = now

        stmt = (
            pg_insert(Job)
            .values(**insert_values)
            .on_conflict_do_update(index_elements=[Job.object_id], set_=update_values)
            .returning(Job.created_at, Job.updated_at)
        )
        created_at, updated_at = (await self.session.execute(stmt)).one()
        return created_at == updated_at

    async def deactivate_stale(self, older_than_days: int) -> int:
        threshold = datetime.now(UTC) - timedelta(days=older_than_days)
        result = await self.session.execute(
            update(Job)
            .where(Job.is_active.is_(True), Job.last_seen_at < threshold)
            .values(is_active=False, updated_at=datetime.now(UTC))
        )
        return result.rowcount or 0
