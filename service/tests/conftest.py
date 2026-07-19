"""Test fixtures.

Unit tests run standalone. Integration tests need real Postgres + Redis and
are skipped unless TEST_DATABASE_URL is set (see docker-compose stack).
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

os.environ.setdefault("ADMIN_TOKEN", "test-admin-token-0123456789")
os.environ.setdefault("ENV", "development")

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/1")

requires_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL not set (integration tests need the compose stack)",
)


def make_job_values(**overrides) -> dict:
    """Valid Job column values for inserting test rows."""
    suffix = uuid.uuid4().hex[:10]
    values = {
        "object_id": f"test___{suffix}",
        "title": f"Test Engineer {suffix}",
        "company": "TestCorp",
        "location_formatted": "Austin, Texas, United States",
        "country": "US",
        "state": "Texas, US",
        "city": "Austin, Texas, US",
        "workplace_type": "Remote",
        "seniority": "Senior Level",
        "commitment": ["Full Time"],
        "category": "Engineering",
        "role_type": "Individual Contributor",
        "salary_min_yearly": 100000.0,
        "salary_max_yearly": 150000.0,
        "salary_currency": "USD",
        "salary_frequency": "Yearly",
        "min_yoe": 5,
        "technical_tools": ["Python", "PostgreSQL"],
        "requirements_summary": "Build and test things.",
        "apply_url": "https://example.com/apply",
        "source_ats": "greenhouse",
        "board_token": "testcorp",
        "published_at": datetime.now(UTC) - timedelta(hours=1),
        "raw": {"objectID": f"test___{suffix}"},
    }
    values.update(overrides)
    return values
