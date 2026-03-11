"""Unit tests for service exception classes."""

from datetime import datetime, timezone

import pytest

from src.services.exceptions import (
    CatalogCorruptionError,
    CatalogError,
    DataNotFoundError,
    IBKRConnectionError,
    KrakenConnectionError,
    KrakenRateLimitError,
    RateLimitExceededError,
)


class TestExceptionHierarchy:
    """All service exceptions inherit from CatalogError."""

    @pytest.mark.parametrize(
        "exc_class",
        [
            DataNotFoundError,
            IBKRConnectionError,
            CatalogCorruptionError,
            RateLimitExceededError,
            KrakenConnectionError,
            KrakenRateLimitError,
        ],
    )
    def test_subclass_of_catalog_error(self, exc_class):
        assert issubclass(exc_class, CatalogError)

    def test_catalog_error_is_exception(self):
        assert issubclass(CatalogError, Exception)


class TestKrakenConnectionError:
    """Test KrakenConnectionError construction and message."""

    def test_default_message(self):
        err = KrakenConnectionError()
        assert str(err) == "Kraken connection unavailable"

    def test_custom_message(self):
        err = KrakenConnectionError("API key invalid")
        assert str(err) == "API key invalid"

    def test_catchable_as_catalog_error(self):
        with pytest.raises(CatalogError):
            raise KrakenConnectionError("test")


class TestKrakenRateLimitError:
    """Test KrakenRateLimitError construction and attributes."""

    def test_default_retry_after(self):
        err = KrakenRateLimitError()
        assert err.retry_after == 2
        assert err.request_count is None

    def test_custom_retry_after(self):
        err = KrakenRateLimitError(retry_after=5)
        assert err.retry_after == 5

    def test_with_request_count(self):
        err = KrakenRateLimitError(retry_after=3, request_count=100)
        assert err.retry_after == 3
        assert err.request_count == 100
        assert "100" in str(err)

    def test_message_includes_retry_seconds(self):
        err = KrakenRateLimitError(retry_after=10)
        assert "10 seconds" in str(err)


class TestDataNotFoundError:
    """Test DataNotFoundError message formatting and attributes."""

    def test_message_format(self):
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, tzinfo=timezone.utc)
        err = DataNotFoundError("BTC/USD.KRAKEN", start, end)
        msg = str(err)
        assert "BTC/USD.KRAKEN" in msg
        assert "2024-01-01" in msg
        assert "2024-01-02" in msg

    def test_attributes_stored(self):
        start = datetime(2024, 6, 1, tzinfo=timezone.utc)
        end = datetime(2024, 6, 30, tzinfo=timezone.utc)
        err = DataNotFoundError("ETH/USD.KRAKEN", start, end)
        assert err.instrument_id == "ETH/USD.KRAKEN"
        assert err.start == start
        assert err.end == end
