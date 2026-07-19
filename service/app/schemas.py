"""Pydantic DTOs: request validation and response serialization.

The DB models are never returned directly - responses go through these DTOs.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

WORKPLACE_TYPES = ["Remote", "Hybrid", "Onsite", "Field"]
SENIORITY_LEVELS = [
    "No Prior Experience Required",
    "Entry Level",
    "Mid Level",
    "Senior Level",
]


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    company: str
    location_formatted: str
    country: str
    state: str
    city: str
    workplace_type: str
    seniority: str
    commitment: list[str]
    category: str
    role_type: str
    salary_min_yearly: float | None
    salary_max_yearly: float | None
    salary_currency: str
    salary_frequency: str
    min_yoe: int | None
    technical_tools: list[str]
    requirements_summary: str
    apply_url: str
    source_ats: str
    published_at: datetime
    first_seen_at: datetime
    last_seen_at: datetime
    is_active: bool


class JobListMeta(BaseModel):
    next_cursor: str | None
    count: int


class JobListResponse(BaseModel):
    data: list[JobOut]
    meta: JobListMeta


class JobResponse(BaseModel):
    data: JobOut


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class StatsOut(BaseModel):
    active_jobs: int
    companies: int
    last_successful_scrape_at: datetime | None
    last_scrape_status: str | None


class StatsResponse(BaseModel):
    data: StatsOut


# ---------------------------------------------------------------------------
# Admin: API keys
# ---------------------------------------------------------------------------

class ApiKeyCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    owner: str = Field(default="", max_length=200)
    rate_limit_per_minute: int = Field(default=120, ge=1, le=10_000)
    expires_at: datetime | None = None


class ApiKeyCreatedOut(BaseModel):
    id: uuid.UUID
    api_key: str  # raw key - shown exactly once
    prefix: str
    last_four: str
    name: str
    rate_limit_per_minute: int
    expires_at: datetime | None


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    prefix: str
    last_four: str
    name: str
    owner: str
    rate_limit_per_minute: int
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class ApiKeyListResponse(BaseModel):
    data: list[ApiKeyOut]


class ApiKeyCreatedResponse(BaseModel):
    data: ApiKeyCreatedOut


# ---------------------------------------------------------------------------
# Admin: usage & scrape runs
# ---------------------------------------------------------------------------

class KeyUsageOut(BaseModel):
    key_id: uuid.UUID
    key_name: str
    requests_last_24h: int
    requests_last_30d: int


class UsageResponse(BaseModel):
    data: list[KeyUsageOut]


class ScrapeRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slice_name: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    pages_fetched: int
    jobs_seen: int
    jobs_new: int
    jobs_updated: int
    error: str


class ScrapeRunListResponse(BaseModel):
    data: list[ScrapeRunOut]


class ScrapeTriggerResponse(BaseModel):
    data: dict


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
