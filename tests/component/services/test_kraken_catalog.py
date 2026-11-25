"""DataCatalogService and Kraken ids (crypto M7).

``--data-source kraken`` no longer goes through ``DataCatalogService``: it reads Kraken spot
OHLC directly (``src.services.kraken_client.fetch_kraken_spot``) and persists nothing. The
service refuses ``data_source="kraken"`` before reading the default catalog, whose Kraken series
(if a user ever fetched one before M7) are perpetual prices.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Price, Quantity

from src.services.data_catalog import DataCatalogService


def _make_bar(instrument_id_str="BTC/USD.KRAKEN", bar_spec="1-HOUR-LAST"):
    """Helper to create a minimal Bar for testing."""
    bar_type = BarType.from_str(f"{instrument_id_str}-{bar_spec}-EXTERNAL")
    return Bar(
        bar_type=bar_type,
        open=Price.from_str("100.0"),
        high=Price.from_str("110.0"),
        low=Price.from_str("90.0"),
        close=Price.from_str("105.0"),
        volume=Quantity.from_str("0"),
        ts_event=1678888800000000000,
        ts_init=1678888800000000000,
    )


class TestDataCatalogServiceKrakenIds:
    """The catalog service with Kraken ids: the kraken source is refused, ids still work."""

    @pytest.fixture
    def mock_catalog(self):
        return MagicMock()

    @pytest.fixture
    def service(self, mock_catalog, tmp_path):
        with patch(
            "src.services.data_catalog.ParquetDataCatalog",
            return_value=mock_catalog,
        ):
            svc = DataCatalogService(catalog_path=tmp_path)
            svc.catalog = mock_catalog
            return svc

    @pytest.mark.asyncio
    async def test_the_kraken_source_is_refused_before_reading_the_catalog(
        self, service, mock_catalog
    ):
        with patch.object(service, "get_availability") as availability:
            with pytest.raises(ValueError, match="fetch_kraken_spot"):
                await service.fetch_or_load(
                    instrument_id="BTC/USD.KRAKEN",
                    start=datetime(2023, 3, 15, tzinfo=timezone.utc),
                    end=datetime(2023, 3, 16, tzinfo=timezone.utc),
                    bar_type_spec="1-HOUR-LAST",
                    data_source="kraken",
                )
        availability.assert_not_called()
        mock_catalog.write_data.assert_not_called()

    def test_the_service_has_no_kraken_client(self, service):
        assert not hasattr(service, "kraken_client")

    @pytest.mark.asyncio
    async def test_ibkr_data_source_backward_compat(self, mock_catalog, tmp_path):
        """data_source='ibkr' still uses existing IBKR path."""
        mock_ibkr_client = AsyncMock()
        mock_ibkr_client.is_connected = True

        bar = _make_bar("AAPL.NASDAQ", "1-MINUTE-LAST")
        mock_instrument = MagicMock()
        mock_ibkr_client.fetch_bars = AsyncMock(return_value=([bar], mock_instrument))

        with patch(
            "src.services.data_catalog.ParquetDataCatalog",
            return_value=mock_catalog,
        ):
            svc = DataCatalogService(
                catalog_path=tmp_path,
                ibkr_client=mock_ibkr_client,
            )
            svc.catalog = mock_catalog
            mock_catalog.write_data = MagicMock()

            with patch.object(svc, "_rebuild_availability_cache"):
                with patch.object(svc, "_is_ibkr_available", return_value=True):
                    bars = await svc.fetch_or_load(
                        instrument_id="AAPL.NASDAQ",
                        start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                        end=datetime(2024, 1, 31, tzinfo=timezone.utc),
                        data_source="ibkr",
                    )

            assert len(bars) == 1
            mock_ibkr_client.fetch_bars.assert_called_once()

    @pytest.mark.asyncio
    async def test_gap_detection_works_with_kraken(self, service):
        """detect_gaps still works for Kraken instruments."""
        start = datetime(2023, 1, 1, tzinfo=timezone.utc)
        end = datetime(2023, 12, 31, tzinfo=timezone.utc)

        gaps = service.detect_gaps("BTC/USD.KRAKEN", "1-HOUR-LAST", start, end)
        # No data in cache → entire range is a gap
        assert len(gaps) == 1
        assert gaps[0]["start"] == start
        assert gaps[0]["end"] == end
