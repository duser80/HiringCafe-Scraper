"""Service configuration. Every setting comes from environment variables.

Fail-fast: required settings (ADMIN_TOKEN) raise at startup when missing.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Environment
    env: str = Field(default="production", alias="ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Datastores
    database_url: str = Field(
        default="postgresql+asyncpg://hiringcafe:hiringcafe@localhost:5432/hiringcafe",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # Auth
    admin_token: str = Field(alias="ADMIN_TOKEN")

    # Rate limiting defaults (per API key, per minute)
    default_rate_limit_per_minute: int = Field(default=120, alias="DEFAULT_RATE_LIMIT_PER_MINUTE")

    # Worker / ingestion
    scrape_interval_minutes: int = Field(default=180, alias="SCRAPE_INTERVAL_MINUTES")
    scrape_max_pages_per_slice: int = Field(default=60, alias="SCRAPE_MAX_PAGES_PER_SLICE")
    scrape_delay_seconds: float = Field(default=1.0, alias="SCRAPE_DELAY_SECONDS")
    deactivate_after_days: int = Field(default=14, alias="DEACTIVATE_AFTER_DAYS")
    slices_json: str = Field(default="", alias="SLICES_JSON")
    proxy_url: str = Field(default="", alias="PROXY_URL")

    @field_validator("admin_token")
    @classmethod
    def _admin_token_strong_enough(cls, value: str) -> str:
        if len(value) < 16:
            raise ValueError("ADMIN_TOKEN must be at least 16 characters long.")
        return value

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"

    @property
    def sync_database_url(self) -> str:
        """Alembic and psql-style tools want a non-async driver URL."""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
