"""Tests for data service."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pandas as pd

from src.services.data_service import DataService


class TestDataService:
    """Test cases for DataService."""

    def test_init(self):
        """Test DataService initialization."""
        service = DataService()
        assert service._cache == {}
        assert service.settings is not None

    @pytest.mark.asyncio
    @patch("src.services.data_service.get_session")
    async def test_get_market_data_success(self, mock_get_session):
        """Test get_market_data with successful database query."""
        # Mock session and query result
        mock_session = AsyncMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session

        # Mock database rows
        mock_row1 = MagicMock()
        mock_row1.symbol = "AAPL"
        mock_row1.timestamp = datetime(2024, 1, 1, 9, 30)
        mock_row1.open = 100.50
        mock_row1.high = 101.00
        mock_row1.low = 100.25
        mock_row1.close = 100.75
        mock_row1.volume = 10000

        mock_row2 = MagicMock()
        mock_row2.symbol = "AAPL"
        mock_row2.timestamp = datetime(2024, 1, 1, 9, 31)
        mock_row2.open = 100.75
        mock_row2.high = 101.25
        mock_row2.low = 100.50
        mock_row2.close = 101.00
        mock_row2.volume = 8500

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [mock_row1, mock_row2]
        mock_session.execute.return_value = mock_result

        service = DataService()
        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 2)

        data = await service.get_market_data("AAPL", start, end)

        assert len(data) == 2
        assert data[0]["symbol"] == "AAPL"
        assert data[0]["open"] == 100.50
        assert data[0]["high"] == 101.00
        assert data[0]["low"] == 100.25
        assert data[0]["close"] == 100.75
        assert data[0]["volume"] == 10000

        assert data[1]["symbol"] == "AAPL"
        assert data[1]["volume"] == 8500

        # Verify query was called with correct parameters
        mock_session.execute.assert_called_once()
        call_args = mock_session.execute.call_args
        assert "AAPL" in str(call_args)

    @pytest.mark.asyncio
    @patch("src.services.data_service.get_session")
    async def test_get_market_data_no_data(self, mock_get_session):
        """Test get_market_data with no data found."""
        # Mock session with empty result
        mock_session = AsyncMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session.execute.return_value = mock_result

        service = DataService()
        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 2)

        with pytest.raises(ValueError, match="No market data found"):
            await service.get_market_data("NONEXISTENT", start, end)

    @pytest.mark.asyncio
    @patch("src.services.data_service.get_session")
    async def test_get_market_data_caching(self, mock_get_session):
        """Test get_market_data caching functionality."""
        # Mock session and query result
        mock_session = AsyncMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session

        mock_row = MagicMock()
        mock_row.symbol = "AAPL"
        mock_row.timestamp = datetime(2024, 1, 1, 9, 30)
        mock_row.open = 100.50
        mock_row.high = 101.00
        mock_row.low = 100.25
        mock_row.close = 100.75
        mock_row.volume = 10000

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [mock_row]
        mock_session.execute.return_value = mock_result

        service = DataService()
        start = datetime(2024, 1, 1)
        end = datetime(2024, 1, 2)

        # First call - should hit database
        data1 = await service.get_market_data("AAPL", start, end)
        assert len(data1) == 1

        # Second call - should use cache
        data2 = await service.get_market_data("AAPL", start, end)
        assert len(data2) == 1
        assert data1 == data2

        # Verify database was only called once
        assert mock_session.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_get_data_as_dataframe_success(self):
        """Test get_data_as_dataframe with data."""
        service = DataService()

        # Mock get_market_data
        mock_data = [
            {
                "symbol": "AAPL",
                "timestamp": datetime(2024, 1, 1, 9, 30),
                "open": 100.50,
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            }
        ]

        with patch.object(service, "get_market_data", return_value=mock_data):
            start = datetime(2024, 1, 1)
            end = datetime(2024, 1, 2)

            df = await service.get_data_as_dataframe("AAPL", start, end)

            assert isinstance(df, pd.DataFrame)
            assert len(df) == 1
            assert "open" in df.columns
            assert "high" in df.columns
            assert "low" in df.columns
            assert "close" in df.columns
            assert "volume" in df.columns
            assert df.index.name == "timestamp"

    @pytest.mark.asyncio
    async def test_get_data_as_dataframe_empty(self):
        """Test get_data_as_dataframe with no data."""
        service = DataService()

        # Mock get_market_data to return empty list
        with patch.object(service, "get_market_data", return_value=[]):
            start = datetime(2024, 1, 1)
            end = datetime(2024, 1, 2)

            df = await service.get_data_as_dataframe("AAPL", start, end)

            assert isinstance(df, pd.DataFrame)
            assert len(df) == 0

    def test_convert_to_nautilus_format(self):
        """Test convert_to_nautilus_format."""
        service = DataService()

        data = [
            {
                "symbol": "AAPL",
                "timestamp": datetime(2024, 1, 1, 9, 30),
                "open": 100.50,
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            }
        ]

        nautilus_data = service.convert_to_nautilus_format(data)

        assert len(nautilus_data) == 1
        record = nautilus_data[0]

        assert record["instrument_id"] == "AAPL.SIM"
        assert record["bar_type"] == "AAPL.SIM-1-MINUTE-MID-EXTERNAL"
        assert record["open"] == int(100.50 * 100000)
        assert record["high"] == int(101.00 * 100000)
        assert record["low"] == int(100.25 * 100000)
        assert record["close"] == int(100.75 * 100000)
        assert record["volume"] == 10000
        assert "ts_event" in record
        assert "ts_init" in record

    @pytest.mark.asyncio
    @patch("src.services.data_service.get_session")
    async def test_get_available_symbols(self, mock_get_session):
        """Test get_available_symbols."""
        # Mock session and query result
        mock_session = AsyncMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session

        mock_row1 = MagicMock()
        mock_row1.symbol = "AAPL"
        mock_row2 = MagicMock()
        mock_row2.symbol = "GOOGL"

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [mock_row1, mock_row2]
        mock_session.execute.return_value = mock_result

        service = DataService()
        symbols = await service.get_available_symbols()

        assert symbols == ["AAPL", "GOOGL"]
        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.services.data_service.get_session")
    async def test_get_data_range_success(self, mock_get_session):
        """Test get_data_range with data."""
        # Mock session and query result
        mock_session = AsyncMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session

        mock_row = MagicMock()
        mock_row.start_date = datetime(2024, 1, 1)
        mock_row.end_date = datetime(2024, 1, 31)

        mock_result = MagicMock()
        mock_result.fetchone.return_value = mock_row
        mock_session.execute.return_value = mock_result

        service = DataService()
        result = await service.get_data_range("AAPL")

        assert result is not None
        assert result["start"] == datetime(2024, 1, 1)
        assert result["end"] == datetime(2024, 1, 31)

    @pytest.mark.asyncio
    @patch("src.services.data_service.get_session")
    async def test_get_data_range_no_data(self, mock_get_session):
        """Test get_data_range with no data."""
        # Mock session with no result
        mock_session = AsyncMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session

        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_session.execute.return_value = mock_result

        service = DataService()
        result = await service.get_data_range("NONEXISTENT")

        assert result is None

    def test_clear_cache(self):
        """Test clear_cache."""
        service = DataService()
        service._cache["test_key"] = "test_value"

        assert len(service._cache) == 1

        service.clear_cache()

        assert len(service._cache) == 0

    @pytest.mark.asyncio
    async def test_validate_data_availability_no_data(self):
        """Test validate_data_availability when no data exists."""
        service = DataService()

        with patch.object(service, "get_data_range", return_value=None):
            with patch.object(
                service, "get_available_symbols", return_value=["GOOGL", "MSFT"]
            ):
                start = datetime(2024, 1, 1)
                end = datetime(2024, 1, 31)

                result = await service.validate_data_availability("AAPL", start, end)

                assert result["valid"] is False
                assert "No data available for symbol AAPL" in result["reason"]
                assert result["available_symbols"] == ["GOOGL", "MSFT"]

    @pytest.mark.asyncio
    async def test_validate_data_availability_start_too_early(self):
        """Test validate_data_availability when start date is too early."""
        service = DataService()

        mock_range = {"start": datetime(2024, 1, 15), "end": datetime(2024, 1, 31)}

        with patch.object(service, "get_data_range", return_value=mock_range):
            start = datetime(2024, 1, 1)  # Too early
            end = datetime(2024, 1, 31)

            result = await service.validate_data_availability("AAPL", start, end)

            assert result["valid"] is False
            assert "Start date" in result["reason"]
            assert "before available data start" in result["reason"]
            assert result["available_range"] == mock_range

    @pytest.mark.asyncio
    async def test_validate_data_availability_end_too_late(self):
        """Test validate_data_availability when end date is too late."""
        service = DataService()

        mock_range = {"start": datetime(2024, 1, 1), "end": datetime(2024, 1, 15)}

        with patch.object(service, "get_data_range", return_value=mock_range):
            start = datetime(2024, 1, 1)
            end = datetime(2024, 1, 31)  # Too late

            result = await service.validate_data_availability("AAPL", start, end)

            assert result["valid"] is False
            assert "End date" in result["reason"]
            assert "after available data end" in result["reason"]
            assert result["available_range"] == mock_range

    @pytest.mark.asyncio
    async def test_validate_data_availability_valid(self):
        """Test validate_data_availability with valid parameters."""
        service = DataService()

        mock_range = {"start": datetime(2024, 1, 1), "end": datetime(2024, 1, 31)}

        with patch.object(service, "get_data_range", return_value=mock_range):
            start = datetime(2024, 1, 10)
            end = datetime(2024, 1, 20)

            result = await service.validate_data_availability("AAPL", start, end)

            assert result["valid"] is True
            assert result["available_range"] == mock_range

    @pytest.mark.asyncio
    async def test_validate_data_availability_exception(self):
        """Test validate_data_availability when exception occurs."""
        service = DataService()

        with patch.object(
            service, "get_data_range", side_effect=Exception("Database error")
        ):
            start = datetime(2024, 1, 1)
            end = datetime(2024, 1, 31)

            result = await service.validate_data_availability("AAPL", start, end)

            assert result["valid"] is False
            assert "Error validating data availability" in result["reason"]
            assert result["available_symbols"] == []
