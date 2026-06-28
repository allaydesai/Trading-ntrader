"""Unit tests for the FMP client: sync rate limiter + graceful-degradation client.

AC #6: NO real network calls are made. Every HTTP interaction is served by an
``httpx.MockTransport`` injected through the ``transport=`` constructor seam, and
``time.sleep`` is patched so retry/backoff and throttle behavior run instantly.
"""

from unittest.mock import patch

import httpx
import pytest

from src.services.metadata.fmp_client import FMPClient, _FMPRateLimiter


class _FakeClock:
    """Deterministic monotonic clock for rate-limiter tests.

    ``sleep`` records the requested duration and advances the clock so tests run
    instantly. It overshoots by a tiny epsilon, mirroring real ``time.sleep``
    (which always advances at least the requested amount) so a request sitting
    exactly on the window boundary ages out instead of spinning.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._t = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._t += seconds + 1e-9


# ---------------------------------------------------------------------------
# Task 2 / AC #2: _FMPRateLimiter sliding-window tests
# ---------------------------------------------------------------------------


class TestFMPRateLimiter:
    """Sync sliding-window rate limiter (mirrors the IBKR/Kraken algorithm)."""

    def test_default_rate(self):
        limiter = _FMPRateLimiter()
        assert limiter.requests_per_minute == 300

    def test_custom_rate(self):
        limiter = _FMPRateLimiter(requests_per_minute=5)
        assert limiter.requests_per_minute == 5

    def test_within_limit_proceeds_without_sleeping(self):
        clock = _FakeClock()
        limiter = _FMPRateLimiter(10, _now=clock.now, _sleep=clock.sleep)
        for _ in range(5):  # 5 requests, well under the limit of 10
            limiter.acquire()
        assert clock.sleeps == []
        assert clock.now() == 0.0

    def test_exceeding_limit_sleeps_for_window_remainder(self):
        clock = _FakeClock()
        limiter = _FMPRateLimiter(2, _now=clock.now, _sleep=clock.sleep)
        for _ in range(3):  # 3rd request exceeds the limit of 2
            limiter.acquire()
        assert len(clock.sleeps) == 1
        # Oldest request was at t=0; it must wait the full 60s window.
        assert clock.sleeps[0] == pytest.approx(60.0, abs=1e-6)

    def test_window_decays_so_later_requests_proceed(self):
        clock = _FakeClock()
        limiter = _FMPRateLimiter(2, _now=clock.now, _sleep=clock.sleep)
        limiter.acquire()
        limiter.acquire()
        # Advance past the window without going through acquire().
        clock._t = 61.0
        limiter.acquire()  # both prior timestamps have aged out
        assert clock.sleeps == []  # no throttling needed after decay


# ---------------------------------------------------------------------------
# Task 3 / Task 4: FMPClient tests via httpx.MockTransport (no network)
# ---------------------------------------------------------------------------

_PROFILE = {"symbol": "SPY", "companyName": "SPDR S&P 500 ETF Trust", "exchange": "NASDAQ"}


def _client(handler, **kwargs) -> FMPClient:
    """Build an FMPClient whose HTTP layer is a MockTransport over ``handler``."""
    return FMPClient(transport=httpx.MockTransport(handler), **kwargs)


class TestFMPClientFetchProfile:
    """fetch_profile happy path + empty-array handling (AC #1, #5)."""

    def test_returns_first_profile_element(self):
        from src.config import FMPSettings

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json=[_PROFILE])

        # Inject an explicit key so the assertion is hermetic — it must not
        # depend on a developer's ambient FMP_API_KEY / .env to pass.
        settings = FMPSettings(fmp_api_key="test-key")
        result = _client(handler, settings=settings).fetch_profile("SPY")

        assert result == _PROFILE  # AC #1: returns data[0], the single profile dict
        assert captured["path"] == "/stable/profile"  # base /stable + /profile
        assert captured["params"]["symbol"] == "SPY"
        assert captured["params"]["apikey"] == "test-key"  # apikey auth sent as query param

    def test_single_request_on_success(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json=[_PROFILE])

        _client(handler).fetch_profile("SPY")
        assert calls["n"] == 1  # exactly one GET, no retry on success

    def test_empty_array_returns_none_without_retry(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json=[])  # unknown ticker → []

        result = _client(handler).fetch_profile("NOPE")

        assert result is None  # AC #5: "no data", not an error
        assert calls["n"] == 1  # not retried

    def test_non_list_payload_returns_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "unexpected shape"})

        assert _client(handler).fetch_profile("SPY") is None

    def test_apikey_not_logged(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[_PROFILE])

        with patch("src.services.metadata.fmp_client.logger") as mock_logger:
            _client(handler).fetch_profile("SPY")

        # No log call (any level) may include the apikey value.
        for call in mock_logger.mock_calls:
            assert "apikey" not in str(call).lower()


class TestFMPClientRetry:
    """Retry-with-backoff on transient failures (AC #3)."""

    def test_retries_on_transient_5xx_then_succeeds(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503)  # transient
            return httpx.Response(200, json=[_PROFILE])

        with patch("src.services.metadata.fmp_client.time.sleep") as mock_sleep:
            result = _client(handler).fetch_profile("SPY")

        assert result == _PROFILE
        assert calls["n"] == 3  # two failures retried, third succeeded
        assert mock_sleep.call_count == 2  # backoff slept before each retry

    def test_retries_on_timeout_then_succeeds(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 2:
                raise httpx.ReadTimeout("slow", request=request)
            return httpx.Response(200, json=[_PROFILE])

        with patch("src.services.metadata.fmp_client.time.sleep"):
            result = _client(handler).fetch_profile("SPY")

        assert result == _PROFILE
        assert calls["n"] == 2

    def test_backoff_is_exponential(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        with patch("src.services.metadata.fmp_client.time.sleep") as mock_sleep:
            _client(handler, max_retries=3, backoff_base=0.5).fetch_profile("SPY")

        slept = [c.args[0] for c in mock_sleep.call_args_list]
        assert slept == [0.5, 1.0, 2.0]  # base * 2**attempt for attempts 0,1,2


class TestFMPClientGracefulDegradation:
    """Specific-exception degradation to None — never raises (AC #4)."""

    def test_timeout_exhausted_returns_none(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            raise httpx.ConnectTimeout("timeout", request=request)

        with (
            patch("src.services.metadata.fmp_client.time.sleep"),
            patch("src.services.metadata.fmp_client.logger") as mock_logger,
        ):
            result = _client(handler, max_retries=3).fetch_profile("SPY")

        assert result is None  # degraded, not raised
        assert calls["n"] == 4  # max_retries + 1 attempts, then gave up
        assert mock_logger.warning.called or mock_logger.error.called

    def test_transport_error_exhausted_returns_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with patch("src.services.metadata.fmp_client.time.sleep"):
            assert _client(handler, max_retries=2).fetch_profile("SPY") is None

    def test_5xx_exhausted_returns_none(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(500)

        with patch("src.services.metadata.fmp_client.time.sleep"):
            result = _client(handler, max_retries=3).fetch_profile("SPY")

        assert result is None
        assert calls["n"] == 4  # max_retries + 1

    def test_non_transient_401_not_retried_and_logged_loud(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(401)  # bad/expired key → config error

        with (
            patch("src.services.metadata.fmp_client.time.sleep"),
            patch("src.services.metadata.fmp_client.logger") as mock_logger,
        ):
            result = _client(handler).fetch_profile("SPY")

        assert result is None
        assert calls["n"] == 1  # 4xx is non-transient → no retry
        assert mock_logger.error.called  # loud, so a misconfigured key is visible

    def test_404_not_retried_returns_none(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404)

        with patch("src.services.metadata.fmp_client.time.sleep"):
            assert _client(handler).fetch_profile("SPY") is None
        assert calls["n"] == 1

    def test_429_is_transient_and_retried(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 2:
                return httpx.Response(429)  # rate limited → transient
            return httpx.Response(200, json=[_PROFILE])

        with patch("src.services.metadata.fmp_client.time.sleep"):
            result = _client(handler).fetch_profile("SPY")

        assert result == _PROFILE
        assert calls["n"] == 2

    def test_programming_error_is_not_swallowed(self):
        """A non-httpx bug (e.g. ValueError) must surface, not degrade to None."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise ValueError("bug, not a provider degradation")

        with pytest.raises(ValueError):
            _client(handler).fetch_profile("SPY")

    def test_malformed_200_body_degrades_to_none(self):
        """A 200 with a non-JSON body (proxy HTML, truncated) degrades, never aborts."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>upstream error</html>")

        with patch("src.services.metadata.fmp_client.logger") as mock_logger:
            result = _client(handler).fetch_profile("SPY")

        assert result is None  # degraded, not raised
        assert mock_logger.error.called  # loud, like a non-transient failure


class TestFMPClientLifecycle:
    """Lazy init + context-manager / close semantics."""

    def test_no_httpx_client_built_on_init(self):
        client = FMPClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
        assert client._client is None  # lazy — nothing built until first use

    def test_close_is_safe_before_any_use(self):
        client = FMPClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
        client.close()  # must not raise even though no client was built

    def test_context_manager_fetches_and_closes(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[_PROFILE])

        with _client(handler) as client:
            assert client.fetch_profile("SPY") == _PROFILE
            built = client._client
        assert built is not None and built.is_closed  # closed on __exit__


class TestFMPClientConstruction:
    """Settings wiring (AC #1)."""

    def test_rate_limiter_uses_configured_quota(self):
        client = FMPClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
        # Default FMPSettings.fmp_rate_limit is 300/min.
        assert client._rate_limiter.requests_per_minute == 300

    def test_explicit_settings_override(self):
        from src.config import FMPSettings

        settings = FMPSettings(fmp_rate_limit=120)
        client = FMPClient(
            settings=settings,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])),
        )
        assert client._rate_limiter.requests_per_minute == 120
