"""Authentication: customer API keys and the admin token.

API keys:
- Format ``hc_live_<43 chars base62>``; generated with the ``secrets`` module.
- Only the SHA-256 hash is stored; lookup is by hash (indexed unique column).
- Revocation and expiry are enforced on every request.

Admin:
- Single ADMIN_TOKEN from the environment, compared in constant time.
"""

from __future__ import annotations

import hashlib
import secrets
import string
from datetime import UTC, datetime

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_session
from .errors import ApiError
from .models import ApiKey
from .repositories.keys_repo import KeysRepository

KEY_PREFIX = "hc_live_"
_BASE62 = string.ascii_letters + string.digits


def generate_api_key() -> str:
    secret = "".join(secrets.choice(_BASE62) for _ in range(43))
    return f"{KEY_PREFIX}{secret}"


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


async def require_api_key(
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> ApiKey:
    """FastAPI dependency: authenticate the request via the X-API-Key header."""
    if not x_api_key:
        raise ApiError(401, "unauthenticated", "Missing X-API-Key header.")
    if not x_api_key.startswith(KEY_PREFIX):
        raise ApiError(401, "unauthenticated", "Malformed API key.")

    repo = KeysRepository(session)
    api_key = await repo.get_by_hash(hash_api_key(x_api_key))
    if api_key is None:
        raise ApiError(401, "unauthenticated", "Invalid API key.")
    if api_key.revoked_at is not None:
        raise ApiError(403, "key_revoked", "This API key has been revoked.")
    if api_key.expires_at is not None and api_key.expires_at <= datetime.now(UTC):
        raise ApiError(403, "key_expired", "This API key has expired.")

    request.state.api_key = api_key
    return api_key


async def require_admin(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    """FastAPI dependency: authenticate admin routes via Bearer ADMIN_TOKEN."""
    settings = get_settings()
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "unauthenticated", "Missing Authorization: Bearer token.")
    supplied = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(supplied, settings.admin_token):
        raise ApiError(403, "forbidden", "Invalid admin token.")
