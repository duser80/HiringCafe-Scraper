# HiringCafe Jobs API Service

Production-grade, self-contained job-data API. Background workers scrape HiringCafe on a schedule into Postgres; a FastAPI service serves the data to consumers via hashed API keys with per-key rate limiting.

```
consumers --X-API-Key--> FastAPI (api) --reads--> Postgres <--upserts-- worker --scrapes--> hiringcafe.com
                              |                                            
                              +--rate-limit counters--> Redis
```

## Quick start

```bash
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD and ADMIN_TOKEN
#   openssl rand -hex 24   # good POSTGRES_PASSWORD
#   openssl rand -hex 32   # good ADMIN_TOKEN

docker compose up -d --build
```

That's it. On first start:

1. Postgres and Redis come up with healthchecks.
2. The `api` container runs Alembic migrations, then serves on port 8000 (`API_PORT`).
3. The `worker` container waits for the schema, runs an immediate ingest cycle, then repeats every `SCRAPE_INTERVAL_MINUTES` (default 180).

Check it: `curl http://localhost:8000/health` — interactive API docs at `http://localhost:8000/docs`.

## Issuing API keys (admin)

```bash
curl -X POST http://localhost:8000/v1/admin/keys \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "customer-acme", "rate_limit_per_minute": 120}'
```

The response contains `data.api_key` (format `hc_live_...`) — **shown exactly once**; only its SHA-256 hash is stored. Other admin operations:

| Action | Request |
| --- | --- |
| List keys | `GET /v1/admin/keys` |
| Revoke a key | `POST /v1/admin/keys/{id}/revoke` |
| Usage per key (24h / 30d) | `GET /v1/admin/usage` |
| Recent scrape runs | `GET /v1/admin/scrape/runs` |
| Trigger an ingest now | `POST /v1/admin/scrape/trigger` |

All admin routes require `Authorization: Bearer <ADMIN_TOKEN>`.

## Consumer API (v1)

Authenticate every request with the `X-API-Key` header.

### `GET /v1/jobs`

| Param | Description |
| --- | --- |
| `q` | Full-text search over title + company (Postgres websearch syntax: `"data engineer" -intern`) |
| `location` | Substring match on location fields (`Texas`, `Bangalore`, ...) |
| `country` | ISO code exact match (`US`, `IN`) |
| `workplace_type` | `Remote` / `Hybrid` / `Onsite` / `Field` |
| `seniority` | e.g. `Senior Level` |
| `commitment` | e.g. `Full Time` |
| `category` | e.g. `Engineering` |
| `company` | Substring match |
| `salary_min` | Minimum yearly salary |
| `posted_after` | ISO datetime |
| `limit` | 1–100 (default 25) |
| `cursor` | Opaque keyset cursor from `meta.next_cursor` |

Response:

```json
{
  "data": [ { "id": "...", "title": "...", "company": "...", "apply_url": "...", ... } ],
  "meta": { "next_cursor": "MjAyNi0wNy0...", "count": 25 }
}
```

Paginate by passing `meta.next_cursor` back as `cursor` until it is `null`. Cursor (keyset) pagination stays fast at any depth — there is no page-number offset.

Other endpoints: `GET /v1/jobs/{id}`, `GET /v1/stats`, `GET /health` (public).

Errors are always `{"error": {"code": "...", "message": "..."}}` with proper status codes; `429` includes a `Retry-After` header. Every response carries `X-Request-ID`, `X-RateLimit-Limit`, and `X-RateLimit-Remaining`.

## Configuration (env)

See [.env.example](../.env.example) for the full annotated list. Highlights:

| Var | Default | Meaning |
| --- | --- | --- |
| `POSTGRES_PASSWORD` | (required) | DB password for the stack |
| `ADMIN_TOKEN` | (required, ≥16 chars) | Bearer token for admin routes |
| `SCRAPE_INTERVAL_MINUTES` | 180 | Time between ingest cycles |
| `SCRAPE_MAX_PAGES_PER_SLICE` | 60 | Page cap per slice per cycle (~100-160 jobs/page) |
| `SLICES_JSON` | one broad newest-first slice | Custom search slices (JSON array) |
| `PROXY_URL` | empty | Residential proxy for cloud deployment |
| `DEACTIVATE_AFTER_DAYS` | 14 | Jobs unseen this long become `is_active=false` |

### Custom slices

Each slice is one HiringCafe search; keep each under ~10k results (the upstream pagination window). `sortBy: date` is enforced automatically so incremental early-stop works:

```bash
SLICES_JSON='[
  {"name":"us-eng","search_state":{"searchQuery":"engineer"},"max_pages":40},
  {"name":"india-all","search_state":{"locations":[{"formatted_address":"India","types":["country"],"id":"x","address_components":[{"long_name":"India","short_name":"IN","types":["country"]}],"options":{}}]},"max_pages":40}
]'
```

## How ingestion stays incremental (10k+ jobs/day)

- Slices are walked newest-first; each page is upserted on `object_id` (idempotent).
- A slice stops early once a whole page is already known — steady-state cycles only pay for what's new.
- The full cycle runs under a Postgres advisory lock: the scheduler, a manual trigger, and multiple worker replicas can never run overlapping ingests.
- Every run is recorded in `scrape_runs` (visible via the admin API); jobs unseen for `DEACTIVATE_AFTER_DAYS` are soft-deactivated, never deleted.
- Politeness: ~1 request/second with jitter and exponential backoff on 403/429.

## Deploying to a VPS

1. Install Docker + Compose on the server, copy the repo (or set up a deploy pipeline), create `.env` with fresh secrets.
2. **Set `PROXY_URL`** to your DataImpulse sticky-session gateway (e.g. `http://USER:PASS@gw.dataimpulse.com:823`). Datacenter IPs are blocked by HiringCafe's edge — this is mandatory on a VPS, and a sticky session (stable exit IP per run) looks far more natural than per-request rotation.
3. `docker compose up -d --build`.
4. Put TLS in front with Caddy (automatic Let's Encrypt). Install Caddy on the host and use this `Caddyfile`:

```
api.yourdomain.com {
    reverse_proxy 127.0.0.1:8000
}
```

Caddy handles certificates, renewal, and HTTP->HTTPS redirects automatically. Point your DNS A record at the server first. (Alternatively run Caddy as a fourth compose service on ports 80/443.)

5. Never expose Postgres/Redis publicly — the compose file binds them to `127.0.0.1` only.

## Operations

| Task | Command |
| --- | --- |
| Tail API logs | `docker compose logs -f api` |
| Tail worker logs | `docker compose logs -f worker` |
| Service status | `docker compose ps` |
| Restart everything | `docker compose restart` |
| Apply new code | `docker compose up -d --build` |
| DB shell | `docker compose exec postgres psql -U hiringcafe hiringcafe` |
| Backup DB | `docker compose exec postgres pg_dump -U hiringcafe hiringcafe > backup.sql` |
| New migration | `alembic -c service/alembic.ini revision -m "desc"` (runs on next deploy) |

Monitoring: point an uptime checker at `GET /health` (returns 503 when Postgres or Redis is down). Scrape freshness is exposed at `GET /v1/stats` (`last_successful_scrape_at`) and `GET /v1/admin/scrape/runs`.

## Tests

```bash
# Unit tests (no services needed)
.venv/bin/python -m pytest service/tests -k unit

# Full suite incl. integration (compose stack must be up)
set -a; source .env; set +a
TEST_DATABASE_URL="postgresql+asyncpg://hiringcafe:${POSTGRES_PASSWORD}@localhost:5433/hiringcafe" \
TEST_REDIS_URL="redis://localhost:6380/1" \
.venv/bin/python -m pytest service/tests
```

## Security notes

- API keys: 43-char base62 secrets, SHA-256 hashed at rest, shown once, revocable, optional expiry, per-key rate limits, full usage audit trail.
- Admin token: constant-time comparison; set a long random value.
- Input validation at the boundary (Pydantic); all SQL parameterized via SQLAlchemy; responses serialized through DTOs (the raw scrape payload is never exposed).
- Containers run as a non-root user; base images are version-pinned; debug mode off; stack traces never returned to clients; security headers on every response.
- Legal note: this serves data scraped from HiringCafe (which aggregates public company ATS postings). Their terms likely restrict large-scale automated access — evaluate that risk before offering this commercially.
