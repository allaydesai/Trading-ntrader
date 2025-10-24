"""Tests for IBKR data database integration."""

import pytest
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch


@pytest.mark.integration
class TestIBKRDatabaseIntegration:
    """Test suite for IBKR data integration with database."""

    @pytest.mark.asyncio
    async def test_data_service_supports_ibkr_source(self):
        """INTEGRATION: DataService supports IBKR as data source."""
        from src.services.data_service import DataService

        # DataService should accept source parameter
        service_csv = DataService(source="csv")
        service_ibkr = DataService(source="ibkr")

        assert service_csv.source == "csv"
        assert service_ibkr.source == "ibkr"

    @pytest.mark.asyncio
    async def test_ibkr_data_source_is_optional(self):
        """INTEGRATION: IBKR source is optional, defaults to existing behavior."""
        from src.services.data_service import DataService

        # Default should maintain backward compatibility
        service = DataService()
        assert service.source == "database"  # Original behavior

    @pytest.mark.asyncio
    async def test_get_market_data_with_ibkr_source(self):
        """INTEGRATION: get_market_data works with IBKR source."""
        from src.services.data_service import DataService
        from nautilus_trader.adapters.interactive_brokers.historical.client import (
            HistoricInteractiveBrokersClient,
        )

        with patch.object(
            HistoricInteractiveBrokersClient, "__init__", return_value=None
        ):
            service = DataService(source="ibkr")

            # Mock IBKR fetch through the provider
            mock_bars = []
            with patch.object(
                service.ibkr_provider, "fetch_historical_data", new_callable=AsyncMock
            ) as mock_fetch:
                mock_fetch.return_value = mock_bars

                # Should not raise error
                await service.get_market_data(
                    symbol="AAPL",
                    start=datetime(2024, 1, 1),
                    end=datetime(2024, 1, 31),
                )

                mock_fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_ibkr_bars_convert_to_database_format(self):
        """INTEGRATION: Nautilus Bar objects convert to database format."""
        from src.services.ibkr_data_provider import IBKRDataProvider

        provider = IBKRDataProvider()

        # Mock Nautilus Bar object
        mock_bar = Mock()
        mock_bar.open.as_double.return_value = 150.50
        mock_bar.high.as_double.return_value = 155.00
        mock_bar.low.as_double.return_value = 149.00
        mock_bar.close.as_double.return_value = 154.00
        mock_bar.volume.as_double.return_value = 1000000
        mock_bar.ts_event = 1704067200000000000  # 2024-01-01 00:00:00 UTC

        # Convert to database record
        db_record = provider._bar_to_db_record(mock_bar, symbol="AAPL")

        assert db_record["symbol"] == "AAPL"
        assert db_record["open"] == Decimal("150.50")
        assert db_record["high"] == Decimal("155.00")
        assert db_record["low"] == Decimal("149.00")
        assert db_record["close"] == Decimal("154.00")
        assert db_record["volume"] == 1000000
        assert isinstance(db_record["timestamp"], datetime)

    @pytest.mark.asyncio
    async def test_csv_source_still_works(self):
        """INTEGRATION: Original CSV workflow is preserved."""
        from src.services.data_service import DataService

        service = DataService(source="database")

        # Mock the db_repo method directly to avoid DB connection
        with patch.object(
            service.db_repo, "fetch_market_data", new_callable=AsyncMock
        ) as mock_db:
            mock_db.side_effect = ValueError("No market data found")

            # Should use database (existing M2 logic)
            with pytest.raises(ValueError, match="No market data found"):
                await service.get_market_data(
                    symbol="AAPL",
                    start=datetime(2024, 1, 1),
                    end=datetime(2024, 1, 31),
                )

            # Verify database method was called (not IBKR)
            mock_db.assert_called_once()
