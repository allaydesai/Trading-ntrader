"""Component tests for DataCatalogService Kraken integration."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.instruments import CurrencyPair
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


class TestDataCatalogServiceKrakenIntegration:
    """Component tests for fetch_or_load with data_source='kraken'."""

    @pytest.fixture
    def mock_catalog(self):
        return MagicMock()

    @pytest.fixture
    def mock_kraken_client(self):
        client = AsyncMock()
        client.is_connected = True
        client.connect = AsyncMock(return_value={"connected": True})
        bar = _make_bar()
        mock_cp = MagicMock(spec=CurrencyPair)
        client.fetch_bars = AsyncMock(return_value=([bar], mock_cp))
        return client

    @pytest.fixture
    def service(self, mock_catalog, mock_kraken_client, tmp_path):
        with patch(
            "src.services.data_catalog.ParquetDataCatalog",
            return_value=mock_catalog,
        ):
            svc = DataCatalogService(
                catalog_path=tmp_path,
                kraken_client=mock_kraken_client,
            )
            svc.catalog = mock_catalog
            return svc

    @pytest.mark.asyncio
    async def test_fetch_or_load_kraken_calls_client(
        self, service, mock_kraken_client, mock_catalog
    ):
        """fetch_or_load with data_source='kraken' calls kraken client."""
        mock_catalog.write_data = MagicMock()
        start = datetime(2023, 3, 15, tzinfo=timezone.utc)
        end = datetime(2023, 3, 16, tzinfo=timezone.utc)

        bars = await service.fetch_or_load(
            instrument_id="BTC/USD.KRAKEN",
            start=start,
            end=end,
            bar_type_spec="1-HOUR-LAST",
            data_source="kraken",
        )

        assert len(bars) == 1
        mock_kraken_client.fetch_bars.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_or_load_kraken_writes_to_catalog(
        self, service, mock_kraken_client, mock_catalog
    ):
        """fetch_or_load with data_source='kraken' writes bars to catalog."""
        mock_catalog.write_data = MagicMock()
        start = datetime(2023, 3, 15, tzinfo=timezone.utc)
        end = datetime(2023, 3, 16, tzinfo=timezone.utc)

        with patch.object(service, "_rebuild_availability_cache"):
            await service.fetch_or_load(
                instrument_id="BTC/USD.KRAKEN",
                start=start,
                end=end,
                data_source="kraken",
            )

        # Should write instrument + bars
        assert mock_catalog.write_data.call_count >= 1

    @pytest.mark.asyncio
    async def test_cache_hit_skips_api(self, service, mock_kraken_client, mock_catalog):
        """When data is already in catalog, skip Kraken API call."""
        from src.models.catalog_metadata import CatalogAvailability

        start = datetime(2023, 3, 15, tzinfo=timezone.utc)
        end = datetime(2023, 3, 16, tzinfo=timezone.utc)

        availability = CatalogAvailability(
            instrument_id="BTC/USD.KRAKEN",
            bar_type_spec="1-HOUR-LAST",
            start_date=datetime(2023, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2023, 12, 31, tzinfo=timezone.utc),
            file_count=10,
            total_rows=1000,
            last_updated=datetime.now(),
        )
        # Cache keys use Nautilus catalog format (slash stripped)
        service.availability_cache["BTCUSD.KRAKEN_1-HOUR-LAST"] = availability

        mock_bar = Mock()
        mock_catalog.bars.return_value = [mock_bar]

        bars = await service.fetch_or_load(
            instrument_id="BTC/USD.KRAKEN",
            start=start,
            end=end,
            bar_type_spec="1-HOUR-LAST",
            data_source="kraken",
        )

        # Should load from catalog, NOT call kraken client
        mock_kraken_client.fetch_bars.assert_not_called()
        assert len(bars) == 1

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
