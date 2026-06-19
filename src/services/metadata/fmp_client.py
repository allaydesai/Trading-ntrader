"""Sync FMP client: rate-limit throttle + graceful degradation.

A thin, provider-specific HTTP client that fetches a single ticker profile from
Financial Modeling Prep (FMP) ``/stable/profile``. It throttles bulk resolution
to the configured per-minute quota and degrades gracefully to ``None`` on any
provider failure so a single bad ticker (or a transient outage) never aborts the
whole import.

Scope (Story 1.3): throttle + one GET + retry/backoff + return raw JSON or
``None``. No FMP-to-domain mapping, no ``N/A``/venue normalization, no cache
check — those live in Stories 1.4/1.5. The client returns the raw FMP profile
dict (``data[0]``) or ``None``; ``None`` covers both the empty-array
"unknown ticker" case and the degraded-error case.
"""

import time
from collections import deque
from typing import Any, Callable

import httpx
import structlog

from src.config import FMPSettings, get_settings

logger = structlog.get_logger(__name__)

# Sliding-window span for the per-minute FMP quota.
_RATE_WINDOW_SECONDS = 60.0


class _FMPRateLimiter:
    """Sync sliding-window rate limiter (per minute).

    Replicates the IBKR/Kraken ``RateLimiter`` sliding-window deque *algorithm*
    in a synchronous form (``time.monotonic`` + ``time.sleep``) because
    ``FMPClient`` runs on the sync CLI path and cannot ``await`` the async
    in-repo limiters. ``_now``/``_sleep`` are injectable test seams so throttle
    behavior is verifiable without real 60-second waits.
    """

    def __init__(
        self,
        requests_per_minute: int = 300,
        *,
        _now: Callable[[], float] = time.monotonic,
        _sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.requests_per_minute = requests_per_minute
        self.window = _RATE_WINDOW_SECONDS
        self.requests: deque[float] = deque()
        self._now = _now
        self._sleep = _sleep

    def acquire(self) -> None:
        """Block until a request slot is free, then record this request."""
        while True:
            now = self._now()
            # Drop timestamps that have aged out of the sliding window.
            while self.requests and self.requests[0] < now - self.window:
                self.requests.popleft()
            if len(self.requests) < self.requests_per_minute:
                self.requests.append(now)
                return
            # At capacity: wait until the oldest request leaves the window.
            sleep_time = self.requests[0] + self.window - now
            if sleep_time > 0:
                self._sleep(sleep_time)
            # Loop back to re-check after sleeping.


class FMPClient:
    """Sync httpx client for FMP ticker profiles with graceful degradation.

    Lazily builds its ``httpx.Client`` on first use. ``fetch_profile`` throttles,
    issues one ``GET /stable/profile``, retries transient failures with
    exponential backoff, and returns the raw profile dict or ``None`` — it never
    raises a network error in a way that aborts the caller's import loop.
    """

    def __init__(
        self,
        settings: FMPSettings | None = None,
        *,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings or get_settings().fmp
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._rate_limiter = _FMPRateLimiter(self._settings.fmp_rate_limit)
        self._transport = transport
        self._client: httpx.Client | None = None  # lazy — built on first use

    def _get_client(self) -> httpx.Client:
        """Lazily build and cache the underlying httpx client."""
        if self._client is None:
            self._client = httpx.Client(
                base_url=self._settings.fmp_base_url,
                timeout=self._settings.fmp_request_timeout,
                transport=self._transport,
            )
        return self._client

    def close(self) -> None:
        """Close the underlying httpx client; reset so a later use rebuilds it."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "FMPClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def fetch_profile(self, ticker: str) -> dict[str, Any] | None:
        """Fetch a ticker profile, or ``None`` on no-data/degradation; never raises (AC #4)."""
        for attempt in range(self.max_retries + 1):
            self._rate_limiter.acquire()  # throttle covers retries too
            try:
                resp = self._get_client().get(
                    "/profile",
                    params={"symbol": ticker, "apikey": self._settings.fmp_api_key},
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 429 or status >= 500:  # transient
                    if self._retry(attempt):
                        continue
                    return self._degraded(ticker, status_code=status, error=f"HTTP {status}")
                # Non-transient 4xx (e.g. 401/403/404): config/data error, no retry.
                return self._degraded(
                    ticker, level="error", status_code=status, error=f"HTTP {status}"
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if self._retry(attempt):
                    continue
                return self._degraded(ticker, error=str(exc))
            return self._parse_payload(resp, ticker)
        return None

    def _retry(self, attempt: int) -> bool:
        """Sleep with exponential backoff and return True if a retry remains."""
        if attempt >= self.max_retries:
            return False
        time.sleep(self.backoff_base * 2**attempt)
        return True

    def _degraded(
        self, ticker: str, *, level: str = "warning", **fields: Any
    ) -> dict[str, Any] | None:
        """Log a degradation (apikey never included) and return the ``None`` signal."""
        getattr(logger, level)("fmp_profile_failed", ticker=ticker, provider="FMP", **fields)
        return None

    def _parse_payload(self, resp: httpx.Response, ticker: str) -> dict[str, Any] | None:
        """Map the FMP top-level array to ``data[0]`` or ``None`` ("no data")."""
        try:
            data = resp.json()
        except ValueError:  # malformed HTTP-200 body (proxy HTML, truncated) → degrade
            return self._degraded(ticker, level="error", error="invalid JSON body")
        if not isinstance(data, list) or not data:
            # Empty array (or unexpected shape) → unknown ticker, not an error.
            logger.debug("fmp_profile_no_data", ticker=ticker, provider="FMP")
            return None
        logger.debug("fmp_profile_fetched", ticker=ticker, provider="FMP")
        return data[0]
