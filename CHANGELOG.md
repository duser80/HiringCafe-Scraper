# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-07-19

Initial public release.

### Added

- **CLI scraper** (`main.py`): keyword/location/workplace/seniority/commitment
  filters, automatic Next.js buildId bootstrap, polite rate limiting with
  backoff, JSON / Excel / CSV export, OpenStreetMap-based location geocoding.
- **Data-quality validation** (`--validate`): verifies every scraped job
  against the search criteria (hard filter checks + soft query-relevance
  checks), per-filter match rates, `filter_match`/`validation_issues` export
  columns, and a JSON report.
- **Web UI** (`webapp/`): local Flask app with filter pills, live scrape
  progress, data-quality panel, inline results table, and file downloads.
- **Production API service** (`service/`): Docker Compose stack with FastAPI,
  Postgres 16, Redis 7, and a scheduled ingest worker.
  - Incremental ingestion with early-stop, upserts on `objectID`, Postgres
    advisory lock, soft deactivation of stale jobs, scrape-run observability.
  - Hashed API keys (shown once, revocable, optional expiry), per-key Redis
    rate limiting with `Retry-After`, usage audit trail.
  - `/v1/jobs` with Postgres full-text search, rich filters, and keyset cursor
    pagination; `/v1/stats`; `/health`; admin endpoints for keys, usage, and
    scrape control.
  - Alembic migrations, security headers, structured JSON logs, non-root
    containers, pinned images, healthchecks.
  - Residential proxy support (`PROXY_URL`) for cloud deployments.
- Test suite: 31 tests (unit + integration against real Postgres/Redis).

[1.0.0]: https://github.com/CJ7862/HiringCafe-Scraper/releases/tag/v1.0.0
