"""Comprehensive unit tests for Historical Data Fetcher.

Tests HistoricalDataFetcher class with 80%+ coverage.
Following TDD principles and project testing standards.
"""

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nautilus_trader.adapters.interactive_brokers.common import IBContract
from nautilus_trader.model.data import Bar, QuoteTick, TradeTick


@pytest.fixture
def mock_ibkr_client():
    """Create mock IBKR client for testing."""
    from src.services.ibkr_client import IBKRHistoricalClient

    client = MagicMock(spec=IBKRHistoricalClient)
    client.is_connected = True
    client.rate_limiter = MagicMock()
    client.rate_limiter.acquire = AsyncMock()
    client.client = MagicMock()
    return client


@pytest.fixture
def mock_catalog():
    """Create mock ParquetDataCatalog for testing."""
    catalog = MagicMock()
    catalog.write_data = MagicMock()
    return catalog


@pytest.fixture
def sample_contracts():
    """Create sample IBContract list for testing."""
    contract = MagicMock(spec=IBContract)
    contract.symbol = "AAPL"
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    return [contract]


@pytest.fixture
def sample_bars():
    """Create sample Bar objects for testing."""
    bar1 = MagicMock(spec=Bar)
    bar1.open = 150.0
    bar1.high = 152.0
    bar1.low = 149.0
    bar1.close = 151.0
    bar1.volume = 1000000

    bar2 = MagicMock(spec=Bar)
    bar2.open = 151.0
    bar2.high = 153.0
    bar2.low = 150.0
    bar2.close = 152.0
    bar2.volume = 1100000

    return [bar1, bar2]


@pytest.fixture
def sample_ticks():
    """Create sample tick objects for testing."""
    tick1 = MagicMock(spec=TradeTick)
    tick1.price = 150.5
    tick1.size = 100
    tick1.ts_event = 1234567890

    tick2 = MagicMock(spec=QuoteTick)
    tick2.bid = 150.0
    tick2.ask = 150.5
    tick2.bid_size = 100
    tick2.ask_size = 200
    tick2.ts_event = 1234567891

    return [tick1, tick2]


class TestHistoricalDataFetcherInitialization:
    """Tests for HistoricalDataFetcher initialization."""

    @pytest.mark.component
    def test_initialization_default_catalog_path(self, mock_ibkr_client):
        """Test initialization with default catalog path."""
        from src.services.data_fetcher import HistoricalDataFetcher

        with patch("src.services.data_fetcher.ParquetDataCatalog") as mock_catalog_cls:
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)

            assert fetcher.client == mock_ibkr_client
            assert fetcher.catalog_path == Path("./data/catalog")
            # ParquetDataCatalog is called with str(Path),
            # which normalizes "./data/catalog" to "data/catalog"
            mock_catalog_cls.assert_called_once_with("data/catalog")

    @pytest.mark.component
    def test_initialization_custom_catalog_path(self, mock_ibkr_client):
        """Test initialization with custom catalog path."""
        from src.services.data_fetcher import HistoricalDataFetcher

        custom_path = "/custom/path/catalog"
        with patch("src.services.data_fetcher.ParquetDataCatalog") as mock_catalog_cls:
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client, catalog_path=custom_path)

            assert fetcher.catalog_path == Path(custom_path)
            mock_catalog_cls.assert_called_once_with(custom_path)


class TestFetchBars:
    """Tests for fetch_bars method."""

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_fetch_bars_success(
        self, mock_ibkr_client, mock_catalog, sample_contracts, sample_bars
    ):
        """Test successful bar fetching."""
        from src.services.data_fetcher import HistoricalDataFetcher

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)

            # Mock the request_bars method
            mock_ibkr_client.client.request_bars = AsyncMock(return_value=sample_bars)

            start_date = datetime(2024, 1, 1, 9, 30)
            end_date = datetime(2024, 1, 31, 16, 0)

            result = await fetcher.fetch_bars(
                contracts=sample_contracts,
                bar_specifications=["1-MINUTE-LAST"],
                start_date=start_date,
                end_date=end_date,
            )

            # Verify results
            assert result == sample_bars
            assert len(result) == 2

            # Verify rate limiter was used
            mock_ibkr_client.rate_limiter.acquire.assert_called_once()

            # Verify catalog write
            mock_catalog.write_data.assert_called_once_with(sample_bars, skip_disjoint_check=True)

            # Verify request_bars called with correct params
            mock_ibkr_client.client.request_bars.assert_called_once_with(
                bar_specifications=["1-MINUTE-LAST"],
                start_date_time=start_date,
                end_date_time=end_date,
                tz_name="America/New_York",
                contracts=sample_contracts,
                use_rth=True,
                timeout=120,
            )

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_fetch_bars_custom_parameters(
        self, mock_ibkr_client, mock_catalog, sample_contracts, sample_bars
    ):
        """Test bar fetching with custom parameters."""
        from src.services.data_fetcher import HistoricalDataFetcher

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)
            mock_ibkr_client.client.request_bars = AsyncMock(return_value=sample_bars)

            start_date = datetime(2024, 1, 1, 0, 0)
            end_date = datetime(2024, 1, 31, 23, 59)

            await fetcher.fetch_bars(
                contracts=sample_contracts,
                bar_specifications=["5-MINUTE-MID", "1-HOUR-LAST"],
                start_date=start_date,
                end_date=end_date,
                timezone="Europe/London",
                use_rth=False,
            )

            # Verify custom parameters were passed
            mock_ibkr_client.client.request_bars.assert_called_once()
            call_kwargs = mock_ibkr_client.client.request_bars.call_args[1]
            assert call_kwargs["tz_name"] == "Europe/London"
            assert call_kwargs["use_rth"] is False
            assert call_kwargs["bar_specifications"] == ["5-MINUTE-MID", "1-HOUR-LAST"]

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_fetch_bars_empty_result(self, mock_ibkr_client, mock_catalog, sample_contracts):
        """Test bar fetching with empty result."""
        from src.services.data_fetcher import HistoricalDataFetcher

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)
            mock_ibkr_client.client.request_bars = AsyncMock(return_value=[])

            start_date = datetime(2024, 1, 1)
            end_date = datetime(2024, 1, 2)

            result = await fetcher.fetch_bars(
                contracts=sample_contracts,
                bar_specifications=["1-MINUTE-LAST"],
                start_date=start_date,
                end_date=end_date,
            )

            assert result == []
            # Catalog should not be written for empty results
            mock_catalog.write_data.assert_not_called()

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_fetch_bars_client_not_connected(
        self, mock_ibkr_client, mock_catalog, sample_contracts
    ):
        """Test bar fetching fails when client not connected."""
        from src.services.data_fetcher import HistoricalDataFetcher

        mock_ibkr_client.is_connected = False

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)

            start_date = datetime(2024, 1, 1)
            end_date = datetime(2024, 1, 2)

            with pytest.raises(RuntimeError) as exc_info:
                await fetcher.fetch_bars(
                    contracts=sample_contracts,
                    bar_specifications=["1-MINUTE-LAST"],
                    start_date=start_date,
                    end_date=end_date,
                )

            assert "Client not connected" in str(exc_info.value)
            assert "Call connect() first" in str(exc_info.value)

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_fetch_bars_api_error(self, mock_ibkr_client, mock_catalog, sample_contracts):
        """Test bar fetching handles API errors."""
        from src.services.data_fetcher import HistoricalDataFetcher

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)
            mock_ibkr_client.client.request_bars = AsyncMock(side_effect=RuntimeError("API Error"))

            start_date = datetime(2024, 1, 1)
            end_date = datetime(2024, 1, 2)

            with pytest.raises(RuntimeError) as exc_info:
                await fetcher.fetch_bars(
                    contracts=sample_contracts,
                    bar_specifications=["1-MINUTE-LAST"],
                    start_date=start_date,
                    end_date=end_date,
                )

            assert "API Error" in str(exc_info.value)


class TestFetchTicks:
    """Tests for fetch_ticks method."""

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_fetch_ticks_success(
        self, mock_ibkr_client, mock_catalog, sample_contracts, sample_ticks
    ):
        """Test successful tick fetching."""
        from src.services.data_fetcher import HistoricalDataFetcher

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)
            mock_ibkr_client.client.request_ticks = AsyncMock(return_value=sample_ticks)

            start_date = datetime(2024, 1, 1, 9, 30)
            end_date = datetime(2024, 1, 1, 16, 0)

            result = await fetcher.fetch_ticks(
                contracts=sample_contracts,
                tick_types=["TRADES"],
                start_date=start_date,
                end_date=end_date,
            )

            # Verify results
            assert result == sample_ticks
            assert len(result) == 2

            # Verify rate limiter was used
            mock_ibkr_client.rate_limiter.acquire.assert_called_once()

            # Verify catalog write
            mock_catalog.write_data.assert_called_once_with(sample_ticks, skip_disjoint_check=True)

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_fetch_ticks_multiple_types(
        self, mock_ibkr_client, mock_catalog, sample_contracts, sample_ticks
    ):
        """Test tick fetching with multiple tick types."""
        from src.services.data_fetcher import HistoricalDataFetcher

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)
            mock_ibkr_client.client.request_ticks = AsyncMock(return_value=sample_ticks)

            start_date = datetime(2024, 1, 1, 9, 30)
            end_date = datetime(2024, 1, 1, 16, 0)

            await fetcher.fetch_ticks(
                contracts=sample_contracts,
                tick_types=["TRADES", "BID_ASK"],
                start_date=start_date,
                end_date=end_date,
            )

            # Verify tick_types parameter passed correctly
            mock_ibkr_client.client.request_ticks.assert_called_once()
            call_kwargs = mock_ibkr_client.client.request_ticks.call_args[1]
            assert call_kwargs["tick_type"] == ["TRADES", "BID_ASK"]

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_fetch_ticks_custom_timezone(
        self, mock_ibkr_client, mock_catalog, sample_contracts, sample_ticks
    ):
        """Test tick fetching with custom timezone."""
        from src.services.data_fetcher import HistoricalDataFetcher

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)
            mock_ibkr_client.client.request_ticks = AsyncMock(return_value=sample_ticks)

            start_date = datetime(2024, 1, 1, 9, 30)
            end_date = datetime(2024, 1, 1, 16, 0)

            await fetcher.fetch_ticks(
                contracts=sample_contracts,
                tick_types=["TRADES"],
                start_date=start_date,
                end_date=end_date,
                timezone="Asia/Tokyo",
                use_rth=False,
            )

            call_kwargs = mock_ibkr_client.client.request_ticks.call_args[1]
            assert call_kwargs["tz_name"] == "Asia/Tokyo"
            assert call_kwargs["use_rth"] is False

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_fetch_ticks_empty_result(self, mock_ibkr_client, mock_catalog, sample_contracts):
        """Test tick fetching with empty result."""
        from src.services.data_fetcher import HistoricalDataFetcher

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)
            mock_ibkr_client.client.request_ticks = AsyncMock(return_value=[])

            start_date = datetime(2024, 1, 1)
            end_date = datetime(2024, 1, 2)

            result = await fetcher.fetch_ticks(
                contracts=sample_contracts,
                tick_types=["TRADES"],
                start_date=start_date,
                end_date=end_date,
            )

            assert result == []
            mock_catalog.write_data.assert_not_called()

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_fetch_ticks_client_not_connected(
        self, mock_ibkr_client, mock_catalog, sample_contracts
    ):
        """Test tick fetching fails when client not connected."""
        from src.services.data_fetcher import HistoricalDataFetcher

        mock_ibkr_client.is_connected = False

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)

            start_date = datetime(2024, 1, 1)
            end_date = datetime(2024, 1, 2)

            with pytest.raises(RuntimeError) as exc_info:
                await fetcher.fetch_ticks(
                    contracts=sample_contracts,
                    tick_types=["TRADES"],
                    start_date=start_date,
                    end_date=end_date,
                )

            assert "Client not connected" in str(exc_info.value)


class TestRequestInstruments:
    """Tests for request_instruments method."""

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_request_instruments_success(
        self, mock_ibkr_client, mock_catalog, sample_contracts
    ):
        """Test successful instrument request."""
        from src.services.data_fetcher import HistoricalDataFetcher

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)

            mock_instrument = MagicMock()
            mock_instrument.symbol = "AAPL"
            mock_instrument.asset_class = "EQUITY"
            mock_instruments = [mock_instrument]

            mock_ibkr_client.client.request_instruments = AsyncMock(return_value=mock_instruments)

            result = await fetcher.request_instruments(contracts=sample_contracts)

            # Verify results
            assert result == mock_instruments
            assert len(result) == 1

            # Verify rate limiter was used
            mock_ibkr_client.rate_limiter.acquire.assert_called_once()

            # Verify catalog write
            mock_catalog.write_data.assert_called_once_with(
                mock_instruments, skip_disjoint_check=True
            )

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_request_instruments_multiple_contracts(self, mock_ibkr_client, mock_catalog):
        """Test instrument request with multiple contracts."""
        from src.services.data_fetcher import HistoricalDataFetcher

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)

            # Create multiple mock contracts
            contracts = [MagicMock(spec=IBContract) for _ in range(3)]

            mock_instruments = [MagicMock() for _ in range(3)]
            mock_ibkr_client.client.request_instruments = AsyncMock(return_value=mock_instruments)

            result = await fetcher.request_instruments(contracts=contracts)

            assert len(result) == 3
            mock_ibkr_client.client.request_instruments.assert_called_once_with(contracts=contracts)

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_request_instruments_empty_result(
        self, mock_ibkr_client, mock_catalog, sample_contracts
    ):
        """Test instrument request with empty result."""
        from src.services.data_fetcher import HistoricalDataFetcher

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)
            mock_ibkr_client.client.request_instruments = AsyncMock(return_value=[])

            result = await fetcher.request_instruments(contracts=sample_contracts)

            assert result == []
            mock_catalog.write_data.assert_not_called()

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_request_instruments_client_not_connected(
        self, mock_ibkr_client, mock_catalog, sample_contracts
    ):
        """Test instrument request fails when client not connected."""
        from src.services.data_fetcher import HistoricalDataFetcher

        mock_ibkr_client.is_connected = False

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)

            with pytest.raises(RuntimeError) as exc_info:
                await fetcher.request_instruments(contracts=sample_contracts)

            assert "Client not connected" in str(exc_info.value)

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_request_instruments_api_error(
        self, mock_ibkr_client, mock_catalog, sample_contracts
    ):
        """Test instrument request handles API errors."""
        from src.services.data_fetcher import HistoricalDataFetcher

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)
            mock_ibkr_client.client.request_instruments = AsyncMock(
                side_effect=RuntimeError("Invalid contract")
            )

            with pytest.raises(RuntimeError) as exc_info:
                await fetcher.request_instruments(contracts=sample_contracts)

            assert "Invalid contract" in str(exc_info.value)


class TestRateLimitingIntegration:
    """Tests for rate limiting integration."""

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_rate_limiter_called_for_bars(
        self, mock_ibkr_client, mock_catalog, sample_contracts, sample_bars
    ):
        """Test rate limiter is called before fetching bars."""
        from src.services.data_fetcher import HistoricalDataFetcher

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)
            mock_ibkr_client.client.request_bars = AsyncMock(return_value=sample_bars)

            await fetcher.fetch_bars(
                contracts=sample_contracts,
                bar_specifications=["1-MINUTE-LAST"],
                start_date=datetime(2024, 1, 1),
                end_date=datetime(2024, 1, 2),
            )

            # Rate limiter should be called exactly once
            mock_ibkr_client.rate_limiter.acquire.assert_called_once()

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_rate_limiter_called_for_ticks(
        self, mock_ibkr_client, mock_catalog, sample_contracts, sample_ticks
    ):
        """Test rate limiter is called before fetching ticks."""
        from src.services.data_fetcher import HistoricalDataFetcher

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)
            mock_ibkr_client.client.request_ticks = AsyncMock(return_value=sample_ticks)

            await fetcher.fetch_ticks(
                contracts=sample_contracts,
                tick_types=["TRADES"],
                start_date=datetime(2024, 1, 1),
                end_date=datetime(2024, 1, 2),
            )

            mock_ibkr_client.rate_limiter.acquire.assert_called_once()

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_rate_limiter_called_for_instruments(
        self, mock_ibkr_client, mock_catalog, sample_contracts
    ):
        """Test rate limiter is called before requesting instruments."""
        from src.services.data_fetcher import HistoricalDataFetcher

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)
            mock_ibkr_client.client.request_instruments = AsyncMock(return_value=[MagicMock()])

            await fetcher.request_instruments(contracts=sample_contracts)

            mock_ibkr_client.rate_limiter.acquire.assert_called_once()


class TestCatalogIntegration:
    """Tests for catalog integration."""

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_catalog_writes_bars_with_skip_disjoint(
        self, mock_ibkr_client, mock_catalog, sample_contracts, sample_bars
    ):
        """Test catalog writes bars with skip_disjoint_check=True."""
        from src.services.data_fetcher import HistoricalDataFetcher

        with patch("src.services.data_fetcher.ParquetDataCatalog", return_value=mock_catalog):
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client)
            mock_ibkr_client.client.request_bars = AsyncMock(return_value=sample_bars)

            await fetcher.fetch_bars(
                contracts=sample_contracts,
                bar_specifications=["1-MINUTE-LAST"],
                start_date=datetime(2024, 1, 1),
                end_date=datetime(2024, 1, 2),
            )

            # Verify skip_disjoint_check parameter
            mock_catalog.write_data.assert_called_once_with(sample_bars, skip_disjoint_check=True)

    @pytest.mark.component
    @pytest.mark.asyncio
    async def test_catalog_path_created_correctly(self, mock_ibkr_client):
        """Test catalog path is created correctly."""
        from src.services.data_fetcher import HistoricalDataFetcher

        with patch("src.services.data_fetcher.ParquetDataCatalog") as mock_catalog_cls:
            custom_path = "/my/custom/path"
            fetcher = HistoricalDataFetcher(client=mock_ibkr_client, catalog_path=custom_path)

            # Verify catalog was initialized with correct path
            mock_catalog_cls.assert_called_once_with(custom_path)
            assert fetcher.catalog_path == Path(custom_path)
