"""HTTP layer for the HiringCafe scraper.

Talks to HiringCafe's Next.js SSR data route:

    GET https://hiringcafe.com/_next/data/{buildId}/index.json?searchState=...&page=N

The buildId rotates on every site deployment, so it is bootstrapped from the
homepage's embedded __NEXT_DATA__ payload and refreshed automatically when the
data route starts returning 404.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
import urllib.parse

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://hiringcafe.com"

# Browser-like headers. HiringCafe's edge (Vercel/Cloudflare) challenges bare
# HTTP clients; a realistic desktop-browser profile passes from residential IPs.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE_URL}/",
}

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)

RETRYABLE_STATUS_CODES = {403, 429, 500, 502, 503, 504}


class HiringCafeError(Exception):
    """Raised when HiringCafe cannot be reached or returns unusable data."""


class HiringCafeClient:
    """Rate-limited HTTP client for HiringCafe's Next.js data routes."""

    def __init__(
        self,
        delay: float = 1.0,
        max_retries: int = 4,
        timeout: float = 30.0,
        proxy_url: str | None = None,
    ) -> None:
        self.delay = delay
        self.max_retries = max_retries
        self.timeout = timeout
        self._build_id: str | None = None
        self._last_request_at = 0.0

        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        if proxy_url:
            # Residential proxy (e.g. DataImpulse). Use a sticky session URL so
            # the exit IP stays consistent for the whole scrape run.
            self.session.proxies = {"http": proxy_url, "https": proxy_url}

    # ------------------------------------------------------------------
    # buildId bootstrap
    # ------------------------------------------------------------------

    @property
    def build_id(self) -> str:
        if self._build_id is None:
            self._build_id = self._fetch_build_id()
        return self._build_id

    def refresh_build_id(self) -> str:
        """Force a re-fetch of the buildId (used after a 404 on a data route)."""
        logger.info("Refreshing HiringCafe buildId...")
        self._build_id = self._fetch_build_id()
        return self._build_id

    def _fetch_build_id(self) -> str:
        """Fetch the homepage and extract the Next.js buildId from __NEXT_DATA__."""
        response = self._request("GET", f"{BASE_URL}/", headers={"Accept": "text/html"})
        match = NEXT_DATA_RE.search(response.text)
        if not match:
            raise HiringCafeError(
                "Could not find __NEXT_DATA__ on the HiringCafe homepage. "
                "The site structure may have changed, or the request was served "
                "a bot challenge page."
            )
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise HiringCafeError(f"Failed to parse __NEXT_DATA__ JSON: {exc}") from exc

        build_id = payload.get("buildId")
        if not build_id:
            raise HiringCafeError("__NEXT_DATA__ did not contain a buildId.")
        logger.info("HiringCafe buildId: %s", build_id)
        return build_id

    # ------------------------------------------------------------------
    # Search data route
    # ------------------------------------------------------------------

    def search_page(self, search_state: dict, page: int) -> dict:
        """Fetch one page of search results.

        Returns the ``pageProps`` dict containing ``ssrHits``, ``ssrTotalCount``,
        ``ssrPageSize``, ``ssrIsLastPage`` and ``ssrError``.
        """
        encoded_state = urllib.parse.quote(json.dumps(search_state, separators=(",", ":")))
        refreshed_build_id = False

        while True:
            url = (
                f"{BASE_URL}/_next/data/{self.build_id}/index.json"
                f"?searchState={encoded_state}&page={page}"
            )
            response = self._request("GET", url, headers={"x-nextjs-data": "1"})

            if response.status_code == 404 and not refreshed_build_id:
                # buildId rotated (site was redeployed); refresh once and retry.
                refreshed_build_id = True
                self.refresh_build_id()
                continue

            if response.status_code != 200:
                raise HiringCafeError(
                    f"Search data route returned HTTP {response.status_code} "
                    f"for page {page}: {response.text[:200]}"
                )

            try:
                payload = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise HiringCafeError(
                    f"Search data route returned non-JSON content for page {page} "
                    f"(likely a bot challenge page): {response.text[:200]}"
                ) from exc

            page_props = payload.get("pageProps")
            if page_props is None:
                raise HiringCafeError(
                    f"Search response for page {page} did not contain pageProps."
                )
            return page_props

    # ------------------------------------------------------------------
    # Low-level request with rate limiting and retries
    # ------------------------------------------------------------------

    def _request(self, method: str, url: str, headers: dict | None = None) -> requests.Response:
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                response = self.session.request(
                    method, url, headers=headers, timeout=self.timeout
                )
            except requests.RequestException as exc:
                last_error = exc
                logger.warning(
                    "Request error on %s (attempt %d/%d): %s",
                    url, attempt + 1, self.max_retries + 1, exc,
                )
                self._backoff(attempt)
                continue

            if response.status_code in RETRYABLE_STATUS_CODES and attempt < self.max_retries:
                logger.warning(
                    "HTTP %d on %s (attempt %d/%d); backing off...",
                    response.status_code, url, attempt + 1, self.max_retries + 1,
                )
                self._backoff(attempt)
                continue

            return response

        raise HiringCafeError(f"Request to {url} failed after retries: {last_error}")

    def _throttle(self) -> None:
        """Enforce a polite delay (with jitter) between consecutive requests."""
        if self.delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        wait = self.delay + random.uniform(0.0, self.delay * 0.5) - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _backoff(self, attempt: int) -> None:
        """Exponential backoff with jitter: ~2s, 4s, 8s, 16s..."""
        wait = (2 ** (attempt + 1)) + random.uniform(0.0, 1.0)
        time.sleep(wait)
