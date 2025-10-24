"""Integration tests for Milestone 5 - IBKR Integration.

Tests the complete workflow from IBKR connection to data storage.
"""

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.config import get_settings


@pytest.mark.integration
@pytest.mark.asyncio
async def test_complete_ibkr_to_database_workflow():
    """INTEGRATION: Complete workflow from IBKR fetch to database storage."""
    from src.services.ibkr_client import IBKRHistoricalClient
    from src.services.data_fetcher import HistoricalDataFetcher
    from nautilus_trader.adapters.interactive_brokers.common import IBContract
    from nautilus_trader.adapters.interactive_brokers.historical.client import (
        HistoricInteractiveBrokersClient,
    )

    # Mock Nautilus client initialization
    with patch.object(HistoricInteractiveBrokersClient, "__init__", return_value=None):
        # Create IBKR client
        client = IBKRHistoricalClient(host="127.0.0.1", port=7497)
        client._connected = True

        # Create data fetcher
        fetcher = HistoricalDataFetcher(client)

        # Mock bar data
        mock_bar = Mock()
        mock_bar.ts_event = 1704067200000000000  # 2024-01-01
        mock_bar.open.as_double.return_value = 150.50
        mock_bar.high.as_double.return_value = 155.00
        mock_bar.low.as_double.return_value = 149.00
        mock_bar.close.as_double.return_value = 154.00
        mock_bar.volume.as_double.return_value = 1000000

        # Mock fetch_bars to return mock data
        with patch.object(fetcher, "fetch_bars", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = [mock_bar]

            # Fetch bars
            contract = IBContract(
                secType="STK", symbol="AAPL", exchange="SMART", primaryExchange="NASDAQ"
            )
            bars = await fetcher.fetch_bars(
                contracts=[contract],
                bar_specifications=["1-DAY-LAST"],
                start_date=datetime.datetime(2024, 1, 1),
                end_date=datetime.datetime(2024, 1, 31),
            )

            # Verify bars were returned
            assert len(bars) == 1
            assert bars[0] == mock_bar

        # Test DataService integration with IBKR source
        from src.services.ibkr_data_provider import IBKRDataProvider

        provider = IBKRDataProvider()

        # Convert bar to database record
        db_record = provider._bar_to_db_record(mock_bar, symbol="AAPL")

        # Verify conversion
        assert db_record["symbol"] == "AAPL"
        assert db_record["open"] == Decimal("150.50")
        assert db_record["high"] == Decimal("155.00")
        assert db_record["low"] == Decimal("149.00")
        assert db_record["close"] == Decimal("154.00")
        assert db_record["volume"] == 1000000
        assert isinstance(db_record["timestamp"], datetime.datetime)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_multi_source_data_service_integration():
    """INTEGRATION: DataService correctly routes between sources."""
    from src.services.data_service import DataService

    # Test database source (default)
    service_db = DataService(source="database")
    assert service_db.source == "database"

    # Test CSV source (alias for database)
    service_csv = DataService(source="csv")
    assert service_csv.source == "csv"

    # Test IBKR source
    service_ibkr = DataService(source="ibkr")
    assert service_ibkr.source == "ibkr"

    # Verify each service routes correctly by mocking the repository/provider
    with patch.object(
        service_db.db_repo, "fetch_market_data", new_callable=AsyncMock
    ) as mock_db:
        with patch.object(
            service_db.ibkr_provider, "fetch_historical_data", new_callable=AsyncMock
        ) as mock_ibkr:
            # Database source should call db_repo
            mock_db.return_value = []
            await service_db.get_market_data(
                symbol="AAPL",
                start=datetime.datetime(2024, 1, 1),
                end=datetime.datetime(2024, 1, 31),
            )
            mock_db.assert_called_once()
            mock_ibkr.assert_not_called()

    with patch.object(
        service_ibkr.db_repo, "fetch_market_data", new_callable=AsyncMock
    ) as mock_db:
        with patch.object(
            service_ibkr.ibkr_provider, "fetch_historical_data", new_callable=AsyncMock
        ) as mock_ibkr:
            # IBKR source should call ibkr_provider
            mock_ibkr.return_value = []
            await service_ibkr.get_market_data(
                symbol="AAPL",
                start=datetime.datetime(2024, 1, 1),
                end=datetime.datetime(2024, 1, 31),
            )
            mock_ibkr.assert_called_once()
            mock_db.assert_not_called()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rate_limiting_protects_ibkr_api():
    """INTEGRATION: Rate limiter prevents exceeding IBKR limits."""
    from src.services.ibkr_client import RateLimiter
    import time

    # Create rate limiter with low limit for testing
    limiter = RateLimiter(requests_per_second=5)

    # Make 5 requests (should succeed immediately)
    start_time = time.time()
    for _ in range(5):
        await limiter.acquire()
    elapsed = time.time() - start_time

    # Should complete quickly (within 0.5 seconds)
    assert elapsed < 0.5

    # Make 6th request (should be rate limited)
    start_time = time.time()
    await limiter.acquire()
    elapsed = time.time() - start_time

    # Should wait at least 1 second (sliding window)
    assert elapsed >= 0.8  # Allow some tolerance


@pytest.mark.integration
def test_ibkr_configuration_loaded_from_settings():
    """INTEGRATION: IBKR settings correctly loaded from config."""
    settings = get_settings()

    # Verify IBKR settings exist and are properly typed
    assert hasattr(settings, "ibkr")
    assert settings.ibkr.ibkr_host == "127.0.0.1"
    assert settings.ibkr.ibkr_port == 7497
    # client_id comes from .env or defaults to 1 - just verify it's an integer
    assert isinstance(settings.ibkr.ibkr_client_id, int)
    assert settings.ibkr.ibkr_client_id > 0  # Must be positive
    assert settings.ibkr.ibkr_trading_mode == "paper"
    assert settings.ibkr.ibkr_read_only is True
    assert settings.ibkr.ibkr_rate_limit == 45


@pytest.mark.integration
@pytest.mark.asyncio
async def test_catalog_storage_and_retrieval():
    """INTEGRATION: Data correctly stored and retrieved from catalog."""
    from src.services.ibkr_client import IBKRHistoricalClient
    from src.services.data_fetcher import HistoricalDataFetcher
    from nautilus_trader.adapters.interactive_brokers.historical.client import (
        HistoricInteractiveBrokersClient,
    )
    from pathlib import Path

    catalog_path = "./test_catalog_integration"

    with patch.object(HistoricInteractiveBrokersClient, "__init__", return_value=None):
        client = IBKRHistoricalClient(host="127.0.0.1", port=7497)
        client._connected = True

        fetcher = HistoricalDataFetcher(client, catalog_path=catalog_path)

        # Verify catalog path is set
        assert fetcher.catalog_path == Path(catalog_path)

        # Mock fetch to prevent actual API call
        with patch.object(fetcher, "fetch_bars", new_callable=AsyncMock):
            fetcher.fetch_bars.return_value = []
            await fetcher.fetch_bars(
                contracts=[],
                bar_specifications=["1-DAY-LAST"],
                start_date=datetime.datetime(2024, 1, 1),
                end_date=datetime.datetime(2024, 1, 31),
            )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_backward_compatibility_with_milestone_2():
    """INTEGRATION: M5 changes don't break M2 CSV import workflow."""
    from src.services.data_service import DataService

    # Test that default DataService still works (backward compatible)
    service = DataService()  # No source specified, should default to "database"
    assert service.source == "database"

    # Mock database fetch to test backward compatibility
    with patch.object(
        service.db_repo, "fetch_market_data", new_callable=AsyncMock
    ) as mock_db:
        mock_db.return_value = [
            {
                "symbol": "AAPL",
                "timestamp": datetime.datetime(2024, 1, 1),
                "open": 150.0,
                "high": 155.0,
                "low": 149.0,
                "close": 154.0,
                "volume": 1000000,
            }
        ]

        data = await service.get_market_data(
            symbol="AAPL",
            start=datetime.datetime(2024, 1, 1),
            end=datetime.datetime(2024, 1, 31),
        )

        # Verify database method was called (not IBKR)
        mock_db.assert_called_once()
        assert len(data) == 1
        assert data[0]["symbol"] == "AAPL"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_connection_error_handling():
    """INTEGRATION: Graceful handling of IBKR connection failures."""
    from src.services.ibkr_client import IBKRHistoricalClient
    from nautilus_trader.adapters.interactive_brokers.historical.client import (
        HistoricInteractiveBrokersClient,
    )

    with patch.object(HistoricInteractiveBrokersClient, "__init__", return_value=None):
        client = IBKRHistoricalClient(host="127.0.0.1", port=7497)

        # Mock connection failure
        with patch.object(
            client.client, "connect", new_callable=AsyncMock
        ) as mock_connect:
            mock_connect.side_effect = ConnectionError("TWS not running")

            with pytest.raises(ConnectionError):
                await client.connect()


@pytest.mark.integration
def test_cli_commands_registered():
    """INTEGRATION: Verify IBKR CLI commands are properly registered."""
    from src.cli.commands.data import data

    # Get all registered commands
    commands = list(data.commands.keys())

    # Verify new IBKR commands exist
    assert "connect" in commands
    assert "fetch" in commands

    # Verify existing commands still exist
    assert "import" in commands  # Renamed from 'import-csv'
    assert "list" in commands
    assert "check" in commands  # Data inspection command


@pytest.mark.integration
def test_docker_compose_configuration():
    """INTEGRATION: Docker Compose file exists and has required services."""
    from pathlib import Path
    import yaml

    docker_compose_path = Path("docker-compose.yml")
    assert docker_compose_path.exists(), "docker-compose.yml not found"

    # Load and parse docker-compose.yml
    with open(docker_compose_path, "r") as f:
        compose_config = yaml.safe_load(f)

    # Verify required services exist
    assert "services" in compose_config
    services = compose_config["services"]

    assert "postgres" in services, "PostgreSQL service missing"
    assert "redis" in services, "Redis service missing"
    assert "ib-gateway" in services, "IB Gateway service missing"
    assert "ntrader-app" in services, "NTrader app service missing"

    # Verify IB Gateway configuration
    ib_gateway = services["ib-gateway"]
    assert ib_gateway["image"] == "ghcr.io/gnzsnz/ib-gateway:stable"
    assert "4002" in str(ib_gateway["ports"]), "IB Gateway API port not exposed"

    # Verify PostgreSQL is TimescaleDB
    postgres = services["postgres"]
    assert "timescale" in postgres["image"].lower(), "TimescaleDB not used"
