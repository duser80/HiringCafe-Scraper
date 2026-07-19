"""FastAPI application factory with production middleware."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import dispose_engine
from .errors import ApiError
from .ratelimit import close_redis
from .routes import admin, health, jobs

logger = logging.getLogger("service.api")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )
    logger.info("API starting (env=%s)", settings.env)
    yield
    await close_redis()
    await dispose_engine()
    logger.info("API stopped")


def create_app() -> FastAPI:
    # Validate configuration at import time - fail fast on missing secrets.
    get_settings()

    app = FastAPI(
        title="HiringCafe Jobs API",
        version="1.0.0",
        description=(
            "Structured job listings aggregated from HiringCafe. "
            "Authenticate with the X-API-Key header."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        debug=False,
    )

    # ------------------------------------------------------------------
    # Middleware: request id + timing + security headers
    # ------------------------------------------------------------------

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        response.headers["X-Request-ID"] = request_id
        for header, value in SECURITY_HEADERS.items():
            response.headers[header] = value

        rate_limit = getattr(request.state, "rate_limit", None)
        if rate_limit is not None:
            response.headers["X-RateLimit-Limit"] = str(rate_limit)
            response.headers["X-RateLimit-Remaining"] = str(
                getattr(request.state, "rate_limit_remaining", 0)
            )

        logger.info(
            "request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
            request_id, request.method, request.url.path,
            response.status_code, duration_ms,
        )
        return response

    # ------------------------------------------------------------------
    # Error handling: consistent {"error": {...}} shape, no stack traces
    # ------------------------------------------------------------------

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
            for err in exc.errors()[:5]
        )
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "validation_error", "message": details}},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "Internal server error."}},
        )

    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(admin.router)
    return app


app = create_app()
