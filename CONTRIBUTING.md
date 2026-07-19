# Contributing to HiringCafe Scraper

Thanks for your interest in contributing! This document covers everything you need to get started.

## Development setup

```bash
git clone https://github.com/CJ7862/HiringCafe-Scraper.git
cd HiringCafe-Scraper
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r service/requirements.txt
```

For work on the API service, bring up the full stack:

```bash
cp .env.example .env    # set POSTGRES_PASSWORD and ADMIN_TOKEN
docker compose up -d --build
```

## Running tests

```bash
# Unit tests (no services needed)
python -m pytest service/tests -k unit

# Full suite including integration tests (compose stack must be running)
set -a; source .env; set +a
TEST_DATABASE_URL="postgresql+asyncpg://hiringcafe:${POSTGRES_PASSWORD}@localhost:5433/hiringcafe" \
TEST_REDIS_URL="redis://localhost:6380/1" \
python -m pytest service/tests
```

## Linting

We use [ruff](https://docs.astral.sh/ruff/):

```bash
ruff check .
```

CI runs lint + tests on every pull request; both must pass before merge.

## Pull request guidelines

1. **Open an issue first** for significant changes, so we can discuss the approach.
2. Keep PRs focused — one logical change per PR.
3. Add or update tests for any behavior change.
4. Update documentation (README, service/README.md) when user-facing behavior changes.
5. Follow the existing code style: type hints, docstrings on public functions, repository pattern for DB access in the service.

## Commit style

Use concise, imperative commit messages with a conventional prefix where it fits:

```
feat: add salary frequency filter to /v1/jobs
fix: handle NUL characters in upstream job data
docs: clarify proxy configuration for VPS deployments
test: cover cursor pagination edge cases
```

## Ground rules for scraping changes

This project deliberately scrapes politely. PRs that remove or weaken the rate limiting, jitter, backoff, or other politeness mechanisms will not be accepted. See the Disclaimer in the README.

## Questions?

Open a [GitHub Discussion or Issue](https://github.com/CJ7862/HiringCafe-Scraper/issues) — happy to help.
