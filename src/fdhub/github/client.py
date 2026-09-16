"""
GitHub REST API client.

Features:
- Authenticated requests (GITHUB_TOKEN or PAT)
- Conditional GET with ETag / If-None-Match
- Exponential backoff with jitter
- Precise rate-limit detection:
    401 → authentication failure
    403 + X-RateLimit-Remaining=0 → rate limit
    403 (other) → permission denied
    404 → not found
    429 → secondary rate limit
    5xx → GitHub server error
- Request accounting (for scaling validation)
- Pagination support
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
DEFAULT_ACCEPT = "application/vnd.github+json"
API_VERSION_HEADER = "X-GitHub-Api-Version"
API_VERSION = "2022-11-28"


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class GitHubError(Exception):
    """Base GitHub client error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubAuthError(GitHubError):
    """401 — bad credentials."""


class GitHubPermissionError(GitHubError):
    """403 — not a rate limit issue."""


class GitHubRateLimitError(GitHubError):
    """403/429 — rate limited. retry_after contains seconds to wait."""

    def __init__(self, message: str, retry_after: float = 60.0) -> None:
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class GitHubNotFoundError(GitHubError):
    """404 — resource does not exist or is private."""


class GitHubServerError(GitHubError):
    """5xx — GitHub infrastructure error."""


# ---------------------------------------------------------------------------
# Response container
# ---------------------------------------------------------------------------

@dataclass
class GitHubResponse:
    """Wrapper around a GitHub API response."""

    status_code: int
    data: Any  # parsed JSON, or None for 304
    headers: dict[str, str]
    etag: str | None = None
    not_modified: bool = False  # True for 304

    @property
    def rate_limit_remaining(self) -> int | None:
        v = self.headers.get("x-ratelimit-remaining")
        return int(v) if v is not None else None

    @property
    def rate_limit_reset(self) -> int | None:
        v = self.headers.get("x-ratelimit-reset")
        return int(v) if v is not None else None


# ---------------------------------------------------------------------------
# Request accounting
# ---------------------------------------------------------------------------

@dataclass
class ApiAccounting:
    total_requests: int = 0
    conditional_304s: int = 0
    successful: int = 0
    rate_limit_events: int = 0
    auth_errors: int = 0
    not_found: int = 0
    server_errors: int = 0
    retries: int = 0


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class GitHubClient:
    """
    Async GitHub API client.

    Usage:
        async with GitHubClient(token="ghp_...") as client:
            response = await client.get("/repos/owner/repo")
    """

    def __init__(
        self,
        token: str | None = None,
        base_url: str = GITHUB_API_BASE,
        max_retries: int = 5,
        backoff_base: float = 1.0,
        backoff_max: float = 60.0,
        inter_request_delay: float = 0.2,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._inter_request_delay = inter_request_delay
        self._last_request_time: float = 0.0
        self.accounting = ApiAccounting()
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> GitHubClient:
        headers = {
            "Accept": DEFAULT_ACCEPT,
            API_VERSION_HEADER: API_VERSION,
            "User-Agent": "FDHub/0.1 (https://github.com/fdhub/fdhub)",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _throttle(self) -> None:
        """Enforce inter-request delay to stay within rate limits."""
        if self._inter_request_delay > 0:
            elapsed = time.monotonic() - self._last_request_time
            wait = self._inter_request_delay - elapsed
            if wait > 0:
                await asyncio.sleep(wait)

    def _classify_error(self, response: httpx.Response) -> GitHubError:
        """Convert an HTTP error response to a typed GitHubError."""
        status = response.status_code
        try:
            body = response.json()
            message = body.get("message", response.text)
        except Exception:
            message = response.text

        if status == 401:
            return GitHubAuthError(f"Authentication failed: {message}", status)

        if status == 403:
            remaining = response.headers.get("x-ratelimit-remaining", "")
            if remaining == "0":
                reset = response.headers.get("x-ratelimit-reset")
                retry_after = 60.0
                if reset:
                    retry_after = max(0.0, float(reset) - time.time()) + 5.0
                return GitHubRateLimitError(
                    f"Rate limit exceeded. Reset in {retry_after:.0f}s.",
                    retry_after=retry_after,
                )
            return GitHubPermissionError(f"Permission denied: {message}", status)

        if status == 404:
            return GitHubNotFoundError(f"Not found: {message}", status)

        if status == 429:
            retry_after = float(response.headers.get("retry-after", "60"))
            return GitHubRateLimitError(
                f"Secondary rate limit. Retry after {retry_after}s.",
                retry_after=retry_after,
            )

        if status >= 500:
            return GitHubServerError(f"GitHub server error {status}: {message}", status)

        return GitHubError(f"Unexpected HTTP {status}: {message}", status)

    async def _request(
        self,
        method: str,
        path: str,
        etag: str | None = None,
        params: dict | None = None,
        **kwargs: Any,
    ) -> GitHubResponse:
        """
        Execute a single GitHub API request with retry/backoff logic.
        """
        assert self._client is not None, "Client not started (use async with)"

        url = path if path.startswith("http") else path
        extra_headers: dict[str, str] = {}
        if etag:
            extra_headers["If-None-Match"] = etag

        attempt = 0
        while True:
            await self._throttle()
            self.accounting.total_requests += 1
            self._last_request_time = time.monotonic()

            try:
                response = await self._client.request(
                    method,
                    url,
                    params=params,
                    headers=extra_headers,
                    **kwargs,
                )
            except httpx.TransportError as exc:
                logger.warning("Transport error (attempt %d): %s", attempt + 1, exc)
                if attempt >= self._max_retries:
                    raise GitHubServerError(f"Network error after {attempt + 1} attempts: {exc}")
                await self._backoff(attempt)
                attempt += 1
                self.accounting.retries += 1
                continue

            # 304 Not Modified
            if response.status_code == 304:
                self.accounting.conditional_304s += 1
                logger.debug("304 Not Modified: %s", path)
                return GitHubResponse(
                    status_code=304,
                    data=None,
                    headers=dict(response.headers),
                    etag=response.headers.get("etag"),
                    not_modified=True,
                )

            # Success
            if response.status_code < 400:
                self.accounting.successful += 1
                try:
                    data = response.json()
                except Exception:
                    data = response.text
                return GitHubResponse(
                    status_code=response.status_code,
                    data=data,
                    headers=dict(response.headers),
                    etag=response.headers.get("etag"),
                )

            # Classify error
            error = self._classify_error(response)

            # Rate limit — wait and retry
            if isinstance(error, GitHubRateLimitError):
                self.accounting.rate_limit_events += 1
                if attempt >= self._max_retries:
                    raise error
                wait = min(error.retry_after, self._backoff_max)
                logger.warning(
                    "Rate limited. Waiting %.0fs before retry (attempt %d/%d).",
                    wait, attempt + 1, self._max_retries,
                )
                await asyncio.sleep(wait)
                attempt += 1
                self.accounting.retries += 1
                continue

            # Server error — exponential backoff
            if isinstance(error, GitHubServerError):
                self.accounting.server_errors += 1
                if attempt >= self._max_retries:
                    raise error
                await self._backoff(attempt)
                attempt += 1
                self.accounting.retries += 1
                continue

            # Auth/permission/not-found — do not retry
            if isinstance(error, GitHubAuthError):
                self.accounting.auth_errors += 1
            if isinstance(error, GitHubNotFoundError):
                self.accounting.not_found += 1

            raise error

    async def _backoff(self, attempt: int) -> None:
        """Exponential backoff with jitter."""
        base = self._backoff_base * (2 ** attempt)
        jitter = random.uniform(0, base * 0.3)
        wait = min(base + jitter, self._backoff_max)
        logger.debug("Backoff %.1fs (attempt %d)", wait, attempt + 1)
        await asyncio.sleep(wait)

    async def get(
        self,
        path: str,
        etag: str | None = None,
        params: dict | None = None,
    ) -> GitHubResponse:
        """Perform an authenticated GET request."""
        return await self._request("GET", path, etag=etag, params=params)

    async def get_paginated(
        self,
        path: str,
        params: dict | None = None,
        max_pages: int = 100,
    ) -> list[Any]:
        """
        Fetch all pages of a paginated GitHub API endpoint.
        Returns a flat list of all items.
        """
        items: list[Any] = []
        page = 1
        per_page = 100

        base_params = dict(params or {})
        base_params["per_page"] = per_page

        while page <= max_pages:
            base_params["page"] = page
            response = await self.get(path, params=base_params)

            if response.not_modified:
                break

            page_data = response.data
            if not isinstance(page_data, list):
                break

            items.extend(page_data)

            if len(page_data) < per_page:
                # Last page
                break

            page += 1

        return items
