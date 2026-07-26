"""Unit tests for FMPClient.fetch_historical_eod (daily EOD price history).

No real network calls — every HTTP interaction is served by an
``httpx.MockTransport`` injected through the ``transport=`` constructor seam,
mirroring ``test_fmp_client.py``'s pattern for ``fetch_profile``.
"""

from datetime import date
from unittest.mock import patch

import httpx
import pytest

from src.services.metadata.fmp_client import FMPClient

_ROW = {
    "symbol": "SPY",
    "date": "2026-07-24",
    "open": 738.51,
    "high": 743.72,
    "low": 737.29,
    "close": 738.93,
    "volume": 44781983,
}

_START = date(2026, 7, 20)
_END = date(2026, 7, 24)


def _client(handler, **kwargs) -> FMPClient:
    """Build an FMPClient whose HTTP layer is a MockTransport over ``handler``."""
    return FMPClient(transport=httpx.MockTransport(handler), **kwargs)


class TestFetchHistoricalEodHappyPath:
    """Request shape + successful parsing."""

    def test_returns_list_of_rows(self):
        from src.config import FMPSettings

        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["path"] = request.url.path
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json=[_ROW])

        settings = FMPSettings(fmp_api_key="test-key")
        result = _client(handler, settings=settings).fetch_historical_eod("SPY", _START, _END)

        assert result == [_ROW]
        assert captured["path"] == "/stable/historical-price-eod/full"
        assert captured["params"]["symbol"] == "SPY"
        assert captured["params"]["from"] == "2026-07-20"
        assert captured["params"]["to"] == "2026-07-24"
        assert captured["params"]["apikey"] == "test-key"

    def test_single_request_on_success(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json=[_ROW])

        _client(handler).fetch_historical_eod("SPY", _START, _END)
        assert calls["n"] == 1

    def test_multiple_rows_preserved_in_order(self):
        rows = [_ROW, {**_ROW, "date": "2026-07-23", "close": 738.18}]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=rows)

        result = _client(handler).fetch_historical_eod("SPY", _START, _END)
        assert result == rows

    def test_apikey_not_logged(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[_ROW])

        with patch("src.services.metadata.fmp_client.logger") as mock_logger:
            _client(handler).fetch_historical_eod("SPY", _START, _END)

        for call in mock_logger.mock_calls:
            assert "apikey" not in str(call).lower()


class TestFetchHistoricalEodEmptyArrayDivergence:
    """The one deliberate divergence from fetch_profile's empty-array handling.

    /profile's empty array means "unknown ticker" -> None. This endpoint's
    empty array means "zero trading days in this window" -> a valid, non-
    degraded [] so the caller can tell it apart from a real fetch failure.
    """

    def test_empty_array_returns_empty_list_not_none(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json=[])

        result = _client(handler).fetch_historical_eod("SPY", _START, _END)

        assert result == []
        assert result is not None
        assert calls["n"] == 1  # not retried — this is a valid result, not an error


class TestFetchHistoricalEodMalformedPayload:
    def test_non_list_payload_returns_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "unexpected shape"})

        assert _client(handler).fetch_historical_eod("SPY", _START, _END) is None

    def test_malformed_200_body_degrades_to_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>upstream error</html>")

        with patch("src.services.metadata.fmp_client.logger") as mock_logger:
            result = _client(handler).fetch_historical_eod("SPY", _START, _END)

        assert result is None
        assert mock_logger.error.called


class TestFetchHistoricalEodRetry:
    def test_retries_on_transient_5xx_then_succeeds(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503)
            return httpx.Response(200, json=[_ROW])

        with patch("src.services.metadata.fmp_client.time.sleep") as mock_sleep:
            result = _client(handler).fetch_historical_eod("SPY", _START, _END)

        assert result == [_ROW]
        assert calls["n"] == 3
        assert mock_sleep.call_count == 2

    def test_retries_on_timeout_then_succeeds(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 2:
                raise httpx.ReadTimeout("slow", request=request)
            return httpx.Response(200, json=[_ROW])

        with patch("src.services.metadata.fmp_client.time.sleep"):
            result = _client(handler).fetch_historical_eod("SPY", _START, _END)

        assert result == [_ROW]
        assert calls["n"] == 2

    def test_429_is_transient_and_retried(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 2:
                return httpx.Response(429)
            return httpx.Response(200, json=[_ROW])

        with patch("src.services.metadata.fmp_client.time.sleep"):
            result = _client(handler).fetch_historical_eod("SPY", _START, _END)

        assert result == [_ROW]
        assert calls["n"] == 2


class TestFetchHistoricalEodGracefulDegradation:
    def test_timeout_exhausted_returns_none(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            raise httpx.ConnectTimeout("timeout", request=request)

        with (
            patch("src.services.metadata.fmp_client.time.sleep"),
            patch("src.services.metadata.fmp_client.logger") as mock_logger,
        ):
            result = _client(handler, max_retries=3).fetch_historical_eod("SPY", _START, _END)

        assert result is None
        assert calls["n"] == 4
        assert mock_logger.warning.called or mock_logger.error.called

    def test_5xx_exhausted_returns_none(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(500)

        with patch("src.services.metadata.fmp_client.time.sleep"):
            result = _client(handler, max_retries=3).fetch_historical_eod("SPY", _START, _END)

        assert result is None
        assert calls["n"] == 4

    def test_non_transient_401_not_retried_and_logged_loud(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(401)

        with (
            patch("src.services.metadata.fmp_client.time.sleep"),
            patch("src.services.metadata.fmp_client.logger") as mock_logger,
        ):
            result = _client(handler).fetch_historical_eod("SPY", _START, _END)

        assert result is None
        assert calls["n"] == 1
        assert mock_logger.error.called

    def test_404_not_retried_returns_none(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404)

        with patch("src.services.metadata.fmp_client.time.sleep"):
            assert _client(handler).fetch_historical_eod("SPY", _START, _END) is None
        assert calls["n"] == 1

    def test_programming_error_is_not_swallowed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise ValueError("bug, not a provider degradation")

        with pytest.raises(ValueError):
            _client(handler).fetch_historical_eod("SPY", _START, _END)
