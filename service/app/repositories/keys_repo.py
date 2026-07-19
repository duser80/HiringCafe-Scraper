"""API key persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ApiKey


class KeysRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_hash(self, key_hash: str) -> ApiKey | None:
        result = await self.session.execute(
            select(ApiKey).where(ApiKey.key_hash == key_hash)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, key_id: uuid.UUID) -> ApiKey | None:
        return await self.session.get(ApiKey, key_id)

    async def create(
        self,
        *,
        key_hash: str,
        prefix: str,
        last_four: str,
        name: str,
        owner: str,
        rate_limit_per_minute: int,
        expires_at: datetime | None,
    ) -> ApiKey:
        api_key = ApiKey(
            key_hash=key_hash,
            prefix=prefix,
            last_four=last_four,
            name=name,
            owner=owner,
            rate_limit_per_minute=rate_limit_per_minute,
            expires_at=expires_at,
        )
        self.session.add(api_key)
        await self.session.flush()
        return api_key

    async def list_all(self) -> list[ApiKey]:
        result = await self.session.execute(
            select(ApiKey).order_by(ApiKey.created_at.desc())
        )
        return list(result.scalars())

    async def revoke(self, key_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            update(ApiKey)
            .where(ApiKey.id == key_id, ApiKey.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        return (result.rowcount or 0) > 0

    async def touch_last_used(self, key_id: uuid.UUID) -> None:
        await self.session.execute(
            update(ApiKey)
            .where(ApiKey.id == key_id)
            .values(last_used_at=datetime.now(UTC))
        )
