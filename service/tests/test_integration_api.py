"""Integration tests: full API flows against real Postgres + Redis.

Requires the compose stack (or any Postgres/Redis) and env vars:
    TEST_DATABASE_URL=postgresql+asyncpg://hiringcafe:...@localhost:5432/hiringcafe
    TEST_REDIS_URL=redis://localhost:6379/1
"""

from __future__ import annotations

import os
import uuid

import pytest

from .conftest import TEST_DATABASE_URL, TEST_REDIS_URL, make_job_values, requires_db

if TEST_DATABASE_URL:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    os.environ["REDIS_URL"] = TEST_REDIS_URL

pytestmark = [requires_db, pytest.mark.asyncio]

ADMIN_HEADERS = {"Authorization": f"Bearer {os.environ['ADMIN_TOKEN']}"}


@pytest.fixture()
async def client():
    from httpx import ASGITransport, AsyncClient

    from service.app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client

    from service.app.db import dispose_engine
    from service.app.ratelimit import close_redis

    await close_redis()
    await dispose_engine()


async def _seed_jobs(count: int = 3, **overrides) -> list[str]:
    from service.app.db import get_session_factory
    from service.app.repositories.jobs_repo import JobsRepository

    object_ids = []
    factory = get_session_factory()
    async with factory() as session:
        async with session.begin():
            repo = JobsRepository(session)
            for _ in range(count):
                values = make_job_values(**overrides)
                await repo.upsert_job(values)
                object_ids.append(values["object_id"])
    return object_ids


async def _create_key(client, rate_limit: int = 120) -> tuple[str, str]:
    response = await client.post(
        "/v1/admin/keys",
        json={"name": f"test-{uuid.uuid4().hex[:6]}", "rate_limit_per_minute": rate_limit},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    return data["api_key"], data["id"]


class TestHealth:
    async def test_health_ok(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestAuth:
    async def test_missing_key_rejected(self, client):
        response = await client.get("/v1/jobs")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"

    async def test_invalid_key_rejected(self, client):
        response = await client.get(
            "/v1/jobs", headers={"X-API-Key": "hc_live_" + "x" * 43}
        )
        assert response.status_code == 401

    async def test_admin_requires_token(self, client):
        response = await client.get("/v1/admin/keys")
        assert response.status_code == 401
        response = await client.get(
            "/v1/admin/keys", headers={"Authorization": "Bearer wrong-token-000000"}
        )
        assert response.status_code == 403


class TestKeyLifecycle:
    async def test_create_list_revoke(self, client):
        raw_key, key_id = await _create_key(client)
        assert raw_key.startswith("hc_live_")

        listing = await client.get("/v1/admin/keys", headers=ADMIN_HEADERS)
        assert listing.status_code == 200
        listed = [k for k in listing.json()["data"] if k["id"] == key_id]
        assert listed and listed[0]["last_four"] == raw_key[-4:]
        # Raw key is never exposed in listings
        assert "api_key" not in listed[0]

        works = await client.get("/v1/jobs", headers={"X-API-Key": raw_key})
        assert works.status_code == 200

        revoke = await client.post(
            f"/v1/admin/keys/{key_id}/revoke", headers=ADMIN_HEADERS
        )
        assert revoke.status_code == 200

        after_revoke = await client.get("/v1/jobs", headers={"X-API-Key": raw_key})
        assert after_revoke.status_code == 403
        assert after_revoke.json()["error"]["code"] == "key_revoked"

        double_revoke = await client.post(
            f"/v1/admin/keys/{key_id}/revoke", headers=ADMIN_HEADERS
        )
        assert double_revoke.status_code == 409


class TestJobs:
    async def test_list_filters_and_shape(self, client):
        await _seed_jobs(2, workplace_type="Remote", seniority="Senior Level")
        raw_key, _ = await _create_key(client)
        headers = {"X-API-Key": raw_key}

        response = await client.get(
            "/v1/jobs",
            params={"workplace_type": "Remote", "seniority": "Senior Level", "limit": 5},
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert "data" in body and "meta" in body
        assert body["data"], "seeded jobs should be returned"
        for job in body["data"]:
            assert job["workplace_type"] == "Remote"
            assert job["seniority"] == "Senior Level"
            assert "raw" not in job  # internal column never serialized

    async def test_invalid_filter_rejected(self, client):
        raw_key, _ = await _create_key(client)
        response = await client.get(
            "/v1/jobs", params={"workplace_type": "Moon"}, headers={"X-API-Key": raw_key}
        )
        assert response.status_code == 400

    async def test_limit_capped(self, client):
        raw_key, _ = await _create_key(client)
        response = await client.get(
            "/v1/jobs", params={"limit": 5000}, headers={"X-API-Key": raw_key}
        )
        assert response.status_code == 400  # over the max of 100

    async def test_keyset_pagination_no_overlap(self, client):
        await _seed_jobs(5, company="PaginationCorp")
        raw_key, _ = await _create_key(client)
        headers = {"X-API-Key": raw_key}

        first = await client.get(
            "/v1/jobs", params={"limit": 2, "company": "PaginationCorp"}, headers=headers
        )
        assert first.status_code == 200
        page1 = first.json()
        cursor = page1["meta"]["next_cursor"]
        assert cursor

        second = await client.get(
            "/v1/jobs",
            params={"limit": 2, "company": "PaginationCorp", "cursor": cursor},
            headers=headers,
        )
        assert second.status_code == 200
        page2 = second.json()

        ids_page1 = {j["id"] for j in page1["data"]}
        ids_page2 = {j["id"] for j in page2["data"]}
        assert not ids_page1 & ids_page2

    async def test_get_single_and_404(self, client):
        await _seed_jobs(1, company="SingleCorp")
        raw_key, _ = await _create_key(client)
        headers = {"X-API-Key": raw_key}

        listing = await client.get(
            "/v1/jobs", params={"company": "SingleCorp", "limit": 1}, headers=headers
        )
        job_id = listing.json()["data"][0]["id"]

        single = await client.get(f"/v1/jobs/{job_id}", headers=headers)
        assert single.status_code == 200
        assert single.json()["data"]["id"] == job_id

        missing = await client.get(f"/v1/jobs/{uuid.uuid4()}", headers=headers)
        assert missing.status_code == 404

    async def test_stats(self, client):
        raw_key, _ = await _create_key(client)
        response = await client.get("/v1/stats", headers={"X-API-Key": raw_key})
        assert response.status_code == 200
        data = response.json()["data"]
        assert isinstance(data["active_jobs"], int)
        assert isinstance(data["companies"], int)


class TestRateLimit:
    async def test_rate_limit_enforced(self, client):
        raw_key, _ = await _create_key(client, rate_limit=3)
        headers = {"X-API-Key": raw_key}

        statuses = []
        for _ in range(5):
            response = await client.get("/v1/jobs", params={"limit": 1}, headers=headers)
            statuses.append(response.status_code)

        assert statuses.count(200) == 3
        assert statuses.count(429) == 2

        limited = await client.get("/v1/jobs", headers=headers)
        assert limited.status_code == 429
        assert "Retry-After" in limited.headers
        assert limited.json()["error"]["code"] == "rate_limited"
