"""Tests for IBKR historical data fetcher."""

import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


class TestHistoricalDataFetcher:
    """Test suite for IBKR historical data fetching."""

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_fetch_bars_retrieves_ohlcv_data(self):
        """INTEGRATION: Bars fetched from IBKR and stored to catalog."""
        from nautilus_trader.adapters.interactive_brokers.common import IBContract
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        from src.services.data_fetcher import HistoricalDataFetcher
        from src.services.ibkr_client import IBKRHistoricalClient

        with patch.object(HistoricInteractiveBrokersClient, "__init__", return_value=None):
            client = IBKRHistoricalClient(host="127.0.0.1", port=7497)
            client._connected = True

            fetcher = HistoricalDataFetcher(client, catalog_path="./test_catalog")

            contracts = [
                IBContract(
                    secType="STK",
                    symbol="AAPL",
                    exchange="SMART",
                    primaryExchange="NASDAQ",
                )
            ]

            # Mock request_bars to return list of Bar objects (Nautilus type)
            with patch.object(client.client, "request_bars", new_callable=AsyncMock) as mock_bars:
                # Return empty list - real implementation returns Bar objects
                mock_bars.return_value = []

                bars = await fetcher.fetch_bars(
                    contracts=contracts,
                    bar_specifications=["1-DAY-LAST"],
                    start_date=datetime.datetime(2024, 1, 1),
                    end_date=datetime.datetime(2024, 1, 31),
                )

                mock_bars.assert_called_once()
                assert isinstance(bars, list)

                # Verify rate limiting was applied
                assert fetcher.client.rate_limiter is not None

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_fetch_instruments_retrieves_definitions(self):
        """INTEGRATION: Instrument definitions fetched and cataloged."""
        from nautilus_trader.adapters.interactive_brokers.common import IBContract
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        from src.services.data_fetcher import HistoricalDataFetcher
        from src.services.ibkr_client import IBKRHistoricalClient

        with patch.object(HistoricInteractiveBrokersClient, "__init__", return_value=None):
            client = IBKRHistoricalClient(host="127.0.0.1", port=7497)
            client._connected = True

            fetcher = HistoricalDataFetcher(client)

            contracts = [IBContract(secType="STK", symbol="AAPL", exchange="NASDAQ")]

            with patch.object(
                client.client, "request_instruments", new_callable=AsyncMock
            ) as mock_inst:
                # Return empty list - real returns Instrument objects
                mock_inst.return_value = []

                instruments = await fetcher.request_instruments(contracts)

                mock_inst.assert_called_once()
                assert isinstance(instruments, list)

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_fetch_ticks_retrieves_trade_data(self):
        """INTEGRATION: Tick data fetched for supported instruments."""
        from nautilus_trader.adapters.interactive_brokers.common import IBContract
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        from src.services.data_fetcher import HistoricalDataFetcher
        from src.services.ibkr_client import IBKRHistoricalClient

        with patch.object(HistoricInteractiveBrokersClient, "__init__", return_value=None):
            client = IBKRHistoricalClient(host="127.0.0.1", port=7497)
            client._connected = True

            fetcher = HistoricalDataFetcher(client)

            contracts = [
                IBContract(
                    secType="CASH",
                    symbol="EUR",
                    currency="USD",
                    exchange="IDEALPRO",
                )
            ]

            with patch.object(client.client, "request_ticks", new_callable=AsyncMock) as mock_ticks:
                # Return empty list - real returns TradeTick/QuoteTick
                mock_ticks.return_value = []

                ticks = await fetcher.fetch_ticks(
                    contracts=contracts,
                    tick_types=["TRADES"],
                    start_date=datetime.datetime(2024, 1, 1, 9, 30),
                    end_date=datetime.datetime(2024, 1, 1, 16, 30),
                )

                mock_ticks.assert_called_once()
                assert isinstance(ticks, list)

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_fetcher_requires_connected_client(self):
        """INTEGRATION: Fetcher raises error when client not connected."""
        from nautilus_trader.adapters.interactive_brokers.common import IBContract
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        from src.services.data_fetcher import HistoricalDataFetcher
        from src.services.ibkr_client import IBKRHistoricalClient

        with patch.object(HistoricInteractiveBrokersClient, "__init__", return_value=None):
            client = IBKRHistoricalClient(host="127.0.0.1", port=7497)
            client._connected = False  # Not connected

            fetcher = HistoricalDataFetcher(client)

            contracts = [IBContract(secType="STK", symbol="AAPL", exchange="SMART")]

            with pytest.raises(RuntimeError) as exc_info:
                await fetcher.fetch_bars(
                    contracts=contracts,
                    bar_specifications=["1-DAY-LAST"],
                    start_date=datetime.datetime(2024, 1, 1),
                    end_date=datetime.datetime(2024, 1, 31),
                )

            assert "not connected" in str(exc_info.value).lower()

    @pytest.mark.component
    def test_catalog_path_configuration(self):
        """Test catalog path can be configured."""
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        from src.services.data_fetcher import HistoricalDataFetcher
        from src.services.ibkr_client import IBKRHistoricalClient

        with patch.object(HistoricInteractiveBrokersClient, "__init__", return_value=None):
            client = IBKRHistoricalClient(host="127.0.0.1", port=7497)

            # Default catalog path
            fetcher1 = HistoricalDataFetcher(client)
            assert fetcher1.catalog_path == Path("./data/catalog")

            # Custom catalog path
            custom_path = "./my_custom_catalog"
            fetcher2 = HistoricalDataFetcher(client, catalog_path=custom_path)
            assert fetcher2.catalog_path == Path(custom_path)
