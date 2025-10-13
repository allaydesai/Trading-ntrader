"""Tests for data service."""

from datetime import datetime, timezone
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
    @patch("src.services.database_repository.get_session")
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
    @patch("src.services.database_repository.get_session")
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
    @patch("src.services.database_repository.get_session")
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
    @patch("src.services.database_repository.get_session")
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
    @patch("src.services.database_repository.get_session")
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
    @patch("src.services.database_repository.get_session")
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

        with patch.object(service.db_repo, "get_data_range", return_value=None):
            with patch.object(
                service.db_repo, "get_available_symbols", return_value=["GOOGL", "MSFT"]
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
        from datetime import timezone

        service = DataService()

        mock_range = {
            "start": datetime(2024, 1, 15, tzinfo=timezone.utc),
            "end": datetime(2024, 1, 31, tzinfo=timezone.utc),
        }

        with patch.object(service.db_repo, "get_data_range", return_value=mock_range):
            start = datetime(2024, 1, 1, tzinfo=timezone.utc)  # Too early
            end = datetime(2024, 1, 31, tzinfo=timezone.utc)

            result = await service.validate_data_availability("AAPL", start, end)

            assert result["valid"] is False
            assert "Start date" in result["reason"]
            assert "before available data start" in result["reason"]
            assert result["available_range"] == mock_range

    @pytest.mark.asyncio
    async def test_validate_data_availability_end_too_late(self):
        """Test validate_data_availability when end date is too late."""
        from datetime import timezone

        service = DataService()

        mock_range = {
            "start": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "end": datetime(2024, 1, 15, tzinfo=timezone.utc),
        }

        with patch.object(service.db_repo, "get_data_range", return_value=mock_range):
            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            end = datetime(2024, 1, 31, tzinfo=timezone.utc)  # Too late

            result = await service.validate_data_availability("AAPL", start, end)

            assert result["valid"] is False
            assert "End date" in result["reason"]
            assert "after available data end" in result["reason"]
            assert result["available_range"] == mock_range

    @pytest.mark.asyncio
    async def test_validate_data_availability_valid(self):
        """Test validate_data_availability with valid parameters."""
        from datetime import timezone

        service = DataService()

        mock_range = {
            "start": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "end": datetime(2024, 1, 31, tzinfo=timezone.utc),
        }

        with patch.object(service.db_repo, "get_data_range", return_value=mock_range):
            start = datetime(2024, 1, 10, tzinfo=timezone.utc)
            end = datetime(2024, 1, 20, tzinfo=timezone.utc)

            result = await service.validate_data_availability("AAPL", start, end)

            assert result["valid"] is True
            assert result["available_range"] == mock_range

    @pytest.mark.asyncio
    async def test_validate_data_availability_exception(self):
        """Test validate_data_availability when exception occurs."""
        service = DataService()

        with patch.object(
            service.db_repo, "get_data_range", side_effect=Exception("Database error")
        ):
            start = datetime(2024, 1, 1)
            end = datetime(2024, 1, 31)

            result = await service.validate_data_availability("AAPL", start, end)

            assert result["valid"] is False
            assert "Error validating data availability" in result["reason"]
            assert result["available_symbols"] == []

    @pytest.mark.asyncio
    async def test_convert_to_nautilus_bars_empty_data(self):
        """Test convert_to_nautilus_bars with empty data."""
        service = DataService()
        from nautilus_trader.model.identifiers import InstrumentId

        instrument_id = InstrumentId.from_str("AAPL.SIM")
        result = service.convert_to_nautilus_bars([], instrument_id)

        assert result == []

    @pytest.mark.asyncio
    async def test_convert_to_nautilus_bars_with_instrument(self):
        """Test convert_to_nautilus_bars with provided instrument."""
        service = DataService()
        from src.utils.mock_data import create_test_instrument

        instrument, instrument_id = create_test_instrument("AAPL")

        data = [
            {
                "timestamp": datetime(2024, 1, 1, 9, 30),
                "open": 100.50,
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            }
        ]

        bars = service.convert_to_nautilus_bars(data, instrument_id, instrument)

        assert isinstance(bars, list)
        assert len(bars) == 1

        # Verify it's a proper Nautilus Bar object
        from nautilus_trader.model.data import Bar

        assert isinstance(bars[0], Bar)

    @pytest.mark.asyncio
    async def test_convert_to_nautilus_bars_without_instrument(self):
        """Test convert_to_nautilus_bars without provided instrument."""
        service = DataService()
        from nautilus_trader.model.identifiers import InstrumentId

        instrument_id = InstrumentId.from_str("AAPL.SIM")

        data = [
            {
                "timestamp": datetime(2024, 1, 1, 9, 30),
                "open": 100.50,
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            }
        ]

        bars = service.convert_to_nautilus_bars(data, instrument_id)

        assert isinstance(bars, list)
        assert len(bars) == 1

    @pytest.mark.asyncio
    async def test_convert_to_nautilus_bars_import_error(self):
        """Test convert_to_nautilus_bars with import error fallback."""
        service = DataService()
        from nautilus_trader.model.identifiers import InstrumentId

        instrument_id = InstrumentId.from_str("AAPL.SIM")

        data = [
            {
                "timestamp": datetime(2024, 1, 1, 9, 30),
                "open": 100.50,
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            }
        ]

        # Test the fallback method directly
        bars = service.converter._convert_to_nautilus_bars_fallback(data, instrument_id)

        # Should use fallback implementation and create bars
        assert isinstance(bars, list)
        assert len(bars) == 1

    @pytest.mark.asyncio
    async def test_convert_to_nautilus_bars_processing_error(self):
        """Test convert_to_nautilus_bars with processing error fallback."""
        service = DataService()
        from nautilus_trader.model.identifiers import InstrumentId

        instrument_id = InstrumentId.from_str("AAPL.SIM")

        data = [
            {
                "timestamp": datetime(2024, 1, 1, 9, 30),
                "open": 100.50,
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            }
        ]

        # Mock processing error to trigger fallback
        with patch("src.utils.data_wrangler.MarketDataWrangler") as mock_wrangler_class:
            mock_wrangler = MagicMock()
            mock_wrangler.process.side_effect = ValueError("Processing failed")
            mock_wrangler_class.return_value = mock_wrangler

            bars = service.convert_to_nautilus_bars(data, instrument_id)

            # Should fall back to original implementation and still create bars
            assert isinstance(bars, list)
            assert len(bars) == 1  # Fallback creates bars successfully

    @pytest.mark.asyncio
    async def test_convert_to_nautilus_bars_no_bars_created(self):
        """Test convert_to_nautilus_bars when no bars are created."""
        service = DataService()
        from nautilus_trader.model.identifiers import InstrumentId

        instrument_id = InstrumentId.from_str("AAPL.SIM")

        data = [
            {
                "timestamp": datetime(2024, 1, 1, 9, 30),
                "open": 100.50,
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            }
        ]

        # Mock wrangler to return empty bars (which raises ValueError, then falls back)
        with patch("src.utils.data_wrangler.MarketDataWrangler") as mock_wrangler_class:
            mock_wrangler = MagicMock()
            mock_wrangler.process.return_value = []
            mock_wrangler_class.return_value = mock_wrangler

            bars = service.convert_to_nautilus_bars(data, instrument_id)

            # Should fall back to original implementation and create bars
            assert isinstance(bars, list)
            assert len(bars) == 1  # Fallback creates bars successfully

    @pytest.mark.asyncio
    async def test_get_adjusted_date_range_no_data(self):
        """Test get_adjusted_date_range when no data exists."""
        service = DataService()

        with patch.object(service.db_repo, "get_data_range", return_value=None):
            start = datetime(2024, 1, 1)
            end = datetime(2024, 1, 31)

            result = await service.get_adjusted_date_range("AAPL", start, end)

            assert result is None

    @pytest.mark.asyncio
    @patch("src.services.database_repository.get_session")
    async def test_get_adjusted_date_range_midnight_dates(self, mock_get_session):
        """Test get_adjusted_date_range with midnight dates."""
        service = DataService()

        # Mock data range
        mock_data_range = {
            "start": datetime(2024, 1, 1, 9, 30),
            "end": datetime(2024, 1, 31, 16, 0),
        }

        # Mock session for specific timestamp queries
        mock_session = AsyncMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session

        # Mock first timestamp query result
        mock_result_first = MagicMock()
        mock_result_first.fetchone.return_value = MagicMock(
            first_timestamp=datetime(2024, 1, 1, 9, 30)
        )

        # Mock last timestamp query result
        mock_result_last = MagicMock()
        mock_result_last.fetchone.return_value = MagicMock(
            last_timestamp=datetime(2024, 1, 31, 16, 0)
        )

        mock_session.execute.side_effect = [mock_result_first, mock_result_last]

        with patch.object(
            service.db_repo, "get_data_range", return_value=mock_data_range
        ):
            # Test with midnight dates (should be adjusted)
            start = datetime(2024, 1, 1, 0, 0)  # Midnight
            end = datetime(2024, 1, 31, 0, 0)  # Midnight

            result = await service.get_adjusted_date_range("AAPL", start, end)

            assert result is not None
            assert result["start"] == datetime(2024, 1, 1, 9, 30)
            assert result["end"] == datetime(2024, 1, 31, 16, 0)

    @pytest.mark.asyncio
    @patch("src.services.database_repository.get_session")
    async def test_get_adjusted_date_range_no_data_on_date(self, mock_get_session):
        """Test get_adjusted_date_range when no data exists on specific dates."""
        service = DataService()

        # Mock data range
        mock_data_range = {
            "start": datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
            "end": datetime(2024, 1, 30, 16, 0, tzinfo=timezone.utc),
        }

        # Mock session for specific timestamp queries
        mock_session = AsyncMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session

        # Mock no data on start date
        mock_result_start = MagicMock()
        mock_result_start.fetchone.return_value = MagicMock(first_timestamp=None)

        # Mock no data on end date
        mock_result_end = MagicMock()
        mock_result_end.fetchone.return_value = MagicMock(last_timestamp=None)

        mock_session.execute.side_effect = [mock_result_start, mock_result_end]

        with patch.object(
            service.db_repo, "get_data_range", return_value=mock_data_range
        ):
            # Test with midnight dates where no data exists on exact dates
            start = datetime(2024, 1, 1, 0, 0)  # Before data start
            end = datetime(2024, 1, 31, 0, 0)  # After data end

            result = await service.get_adjusted_date_range("AAPL", start, end)

            assert result is not None
            assert result["start"] == datetime(
                2024, 1, 2, 9, 30, tzinfo=timezone.utc
            )  # Uses overall data start
            assert result["end"] == datetime(
                2024, 1, 30, 16, 0, tzinfo=timezone.utc
            )  # Uses overall data end

    @pytest.mark.asyncio
    async def test_get_adjusted_date_range_non_midnight_dates(self):
        """Test get_adjusted_date_range with non-midnight dates."""
        service = DataService()

        # Mock data range
        mock_data_range = {
            "start": datetime(2024, 1, 1, 9, 30),
            "end": datetime(2024, 1, 31, 16, 0),
        }

        with patch.object(
            service.db_repo, "get_data_range", return_value=mock_data_range
        ):
            # Test with non-midnight dates (should not be adjusted)
            start = datetime(2024, 1, 5, 10, 30)
            end = datetime(2024, 1, 25, 15, 30)

            result = await service.get_adjusted_date_range("AAPL", start, end)

            assert result is not None
            # Dates should have timezone info added but times remain the same
            assert result["start"].replace(tzinfo=None) == start
            assert result["end"].replace(tzinfo=None) == end

    @pytest.mark.asyncio
    async def test_get_adjusted_date_range_timezone_handling(self):
        """Test get_adjusted_date_range timezone handling."""
        service = DataService()
        from datetime import timezone

        # Mock data range
        mock_data_range = {
            "start": datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc),
            "end": datetime(2024, 1, 31, 16, 0, tzinfo=timezone.utc),
        }

        with patch.object(
            service.db_repo, "get_data_range", return_value=mock_data_range
        ):
            # Test with naive datetime (should be converted to UTC)
            start = datetime(2024, 1, 5, 10, 30)  # Naive datetime
            end = datetime(2024, 1, 25, 15, 30)  # Naive datetime

            result = await service.get_adjusted_date_range("AAPL", start, end)

            assert result is not None
            # Should have timezone info added
            assert result["start"].tzinfo == timezone.utc
            assert result["end"].tzinfo == timezone.utc
