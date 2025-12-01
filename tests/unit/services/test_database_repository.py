"""Unit tests for DatabaseRepository."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import Row

from src.services.database_repository import DatabaseRepository


class TestFetchMarketData:
    """Test suite for fetch_market_data method."""

    @pytest.fixture
    def mock_session(self):
        """Create mock database session."""
        session = AsyncMock()
        return session

    @pytest.fixture
    def repository(self):
        """Create DatabaseRepository instance."""
        return DatabaseRepository()

    @pytest.mark.asyncio
    async def test_fetch_market_data_returns_data_successfully(self, repository, mock_session):
        """Fetch market data returns list of dictionaries."""
        # Arrange
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, tzinfo=timezone.utc)

        # Create mock rows with proper attribute access
        mock_row1 = MagicMock(spec=Row)
        mock_row1.symbol = "AAPL"
        mock_row1.timestamp = datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc)
        mock_row1.open = Decimal("150.00")
        mock_row1.high = Decimal("155.00")
        mock_row1.low = Decimal("149.00")
        mock_row1.close = Decimal("154.00")
        mock_row1.volume = 1000000

        mock_row2 = MagicMock(spec=Row)
        mock_row2.symbol = "AAPL"
        mock_row2.timestamp = datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc)
        mock_row2.open = Decimal("154.00")
        mock_row2.high = Decimal("156.00")
        mock_row2.low = Decimal("153.00")
        mock_row2.close = Decimal("155.00")
        mock_row2.volume = 1100000

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [mock_row1, mock_row2]
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.services.database_repository.get_session") as mock_get_session:
            mock_get_session.return_value.__aenter__.return_value = mock_session

            # Act
            result = await repository.fetch_market_data("AAPL", start, end)

            # Assert
            assert len(result) == 2
            assert result[0]["symbol"] == "AAPL"
            assert result[0]["open"] == 150.00
            assert result[0]["close"] == 154.00
            assert result[1]["volume"] == 1100000

    @pytest.mark.asyncio
    async def test_fetch_market_data_raises_error_when_no_data(self, repository, mock_session):
        """Fetch market data raises ValueError when no data found."""
        # Arrange
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, tzinfo=timezone.utc)

        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.services.database_repository.get_session") as mock_get_session:
            mock_get_session.return_value.__aenter__.return_value = mock_session

            # Act & Assert
            with pytest.raises(ValueError) as exc_info:
                await repository.fetch_market_data("AAPL", start, end)

            assert "No market data found" in str(exc_info.value)
            assert "AAPL" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_fetch_market_data_converts_symbol_to_uppercase(self, repository, mock_session):
        """Fetch market data converts symbol to uppercase."""
        # Arrange
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, tzinfo=timezone.utc)

        mock_row = MagicMock(spec=Row)
        mock_row.symbol = "AAPL"
        mock_row.timestamp = datetime(2024, 1, 1, 9, 30, tzinfo=timezone.utc)
        mock_row.open = Decimal("150.00")
        mock_row.high = Decimal("155.00")
        mock_row.low = Decimal("149.00")
        mock_row.close = Decimal("154.00")
        mock_row.volume = 1000000

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [mock_row]
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.services.database_repository.get_session") as mock_get_session:
            mock_get_session.return_value.__aenter__.return_value = mock_session

            # Act
            await repository.fetch_market_data("aapl", start, end)

            # Assert - verify the query was called with uppercase symbol
            call_args = mock_session.execute.call_args
            assert call_args[0][1]["symbol"] == "AAPL"


class TestGetAvailableSymbols:
    """Test suite for get_available_symbols method."""

    @pytest.fixture
    def repository(self):
        """Create DatabaseRepository instance."""
        return DatabaseRepository()

    @pytest.mark.asyncio
    async def test_get_available_symbols_returns_sorted_list(self, repository):
        """Get available symbols returns sorted list of symbols."""
        # Arrange
        mock_row1 = MagicMock(spec=Row)
        mock_row1.symbol = "TSLA"
        mock_row2 = MagicMock(spec=Row)
        mock_row2.symbol = "AAPL"
        mock_row3 = MagicMock(spec=Row)
        mock_row3.symbol = "MSFT"

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [mock_row1, mock_row2, mock_row3]

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.services.database_repository.get_session") as mock_get_session:
            mock_get_session.return_value.__aenter__.return_value = mock_session

            # Act
            result = await repository.get_available_symbols()

            # Assert - SQLite ORDER BY should have already sorted it
            assert result == ["TSLA", "AAPL", "MSFT"]
            assert len(result) == 3

    @pytest.mark.asyncio
    async def test_get_available_symbols_returns_empty_list_when_no_data(self, repository):
        """Get available symbols returns empty list when no data."""
        # Arrange
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.services.database_repository.get_session") as mock_get_session:
            mock_get_session.return_value.__aenter__.return_value = mock_session

            # Act
            result = await repository.get_available_symbols()

            # Assert
            assert result == []


class TestGetDataRange:
    """Test suite for get_data_range method."""

    @pytest.fixture
    def repository(self):
        """Create DatabaseRepository instance."""
        return DatabaseRepository()

    @pytest.mark.asyncio
    async def test_get_data_range_returns_start_and_end_dates(self, repository):
        """Get data range returns dictionary with start and end dates."""
        # Arrange
        start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)

        # Create mock row - MagicMock with spec=Row evaluates to False by default
        # So we use a simple Mock instead
        mock_row = MagicMock()
        mock_row.start_date = start_date
        mock_row.end_date = end_date
        mock_row.__bool__ = MagicMock(return_value=True)  # Make it truthy

        mock_result = MagicMock()
        mock_result.fetchone.return_value = mock_row

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_context = AsyncMock()
        mock_context.__aenter__.return_value = mock_session
        mock_context.__aexit__.return_value = None

        with patch("src.services.database_repository.get_session") as mock_get_session:
            mock_get_session.return_value = mock_context

            # Act
            result = await repository.get_data_range("AAPL")

            # Assert
            assert result is not None
            assert result["start"] == start_date
            assert result["end"] == end_date

    @pytest.mark.asyncio
    async def test_get_data_range_returns_none_when_no_data(self, repository):
        """Get data range returns None when no data exists."""
        # Arrange
        mock_row = MagicMock(spec=Row)
        mock_row.start_date = None
        mock_row.end_date = None

        mock_result = MagicMock()
        mock_result.fetchone.return_value = mock_row

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.services.database_repository.get_session") as mock_get_session:
            mock_get_session.return_value.__aenter__.return_value = mock_session

            # Act
            result = await repository.get_data_range("NONEXISTENT")

            # Assert
            assert result is None

    @pytest.mark.asyncio
    async def test_get_data_range_converts_symbol_to_uppercase(self, repository):
        """Get data range converts symbol to uppercase."""
        # Arrange
        start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)

        mock_row = MagicMock(spec=Row)
        mock_row.start_date = start_date
        mock_row.end_date = end_date

        mock_result = MagicMock()
        mock_result.fetchone.return_value = mock_row

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.services.database_repository.get_session") as mock_get_session:
            mock_get_session.return_value.__aenter__.return_value = mock_session

            # Act
            await repository.get_data_range("aapl")

            # Assert
            call_args = mock_session.execute.call_args
            assert call_args[0][1]["symbol"] == "AAPL"


class TestGetAdjustedDateRange:
    """Test suite for get_adjusted_date_range method."""

    @pytest.fixture
    def repository(self):
        """Create DatabaseRepository instance."""
        return DatabaseRepository()

    @pytest.mark.asyncio
    async def test_get_adjusted_date_range_adjusts_midnight_start_date(self, repository):
        """Get adjusted date range adjusts midnight start date to actual data start."""
        # Arrange - input is midnight (date-only)
        start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, 23, 59, 59, tzinfo=timezone.utc)

        # Data range - broader than requested
        data_start = datetime(2024, 1, 1, 6, 0, 0, tzinfo=timezone.utc)
        data_end = datetime(2024, 12, 31, 23, 0, 0, tzinfo=timezone.utc)

        # First data point on the start date
        first_timestamp = datetime(2024, 1, 1, 9, 30, 0, tzinfo=timezone.utc)

        # Mock get_data_range to return data range as async
        mock_get_data_range = AsyncMock(return_value={"start": data_start, "end": data_end})

        with patch.object(repository, "get_data_range", mock_get_data_range):
            # Mock the first_timestamp query
            mock_first_row = MagicMock()
            mock_first_row.first_timestamp = first_timestamp
            mock_first_row.__bool__ = MagicMock(return_value=True)  # Make it truthy

            mock_result = MagicMock()
            mock_result.fetchone.return_value = mock_first_row

            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_context = AsyncMock()
            mock_context.__aenter__.return_value = mock_session
            mock_context.__aexit__.return_value = None

            with patch("src.services.database_repository.get_session") as mock_get_session:
                mock_get_session.return_value = mock_context

                # Act
                result = await repository.get_adjusted_date_range("AAPL", start, end)

                # Assert
                assert result is not None
                assert result["start"] == first_timestamp
                assert result["end"] == end

    @pytest.mark.asyncio
    async def test_get_adjusted_date_range_adjusts_midnight_end_date(self, repository):
        """Get adjusted date range adjusts midnight end date to last data point."""
        # Arrange
        start = datetime(2024, 1, 1, 9, 30, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, 0, 0, 0, tzinfo=timezone.utc)  # Midnight

        data_start = datetime(2024, 1, 1, 6, 0, 0, tzinfo=timezone.utc)
        data_end = datetime(2024, 12, 31, 23, 0, 0, tzinfo=timezone.utc)

        last_timestamp = datetime(2024, 1, 31, 16, 0, 0, tzinfo=timezone.utc)

        # Mock get_data_range to return data range as async
        mock_get_data_range = AsyncMock(return_value={"start": data_start, "end": data_end})

        with patch.object(repository, "get_data_range", mock_get_data_range):
            # Mock last_timestamp query
            mock_last_row = MagicMock()
            mock_last_row.last_timestamp = last_timestamp
            mock_last_row.__bool__ = MagicMock(return_value=True)  # Make it truthy

            mock_result = MagicMock()
            mock_result.fetchone.return_value = mock_last_row

            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=mock_result)
            mock_context = AsyncMock()
            mock_context.__aenter__.return_value = mock_session
            mock_context.__aexit__.return_value = None

            with patch("src.services.database_repository.get_session") as mock_get_session:
                mock_get_session.return_value = mock_context

                # Act
                result = await repository.get_adjusted_date_range("AAPL", start, end)

                # Assert
                assert result is not None
                assert result["start"] == start
                assert result["end"] == last_timestamp

    @pytest.mark.asyncio
    async def test_get_adjusted_date_range_returns_none_when_no_data(self, repository):
        """Get adjusted date range returns None when symbol has no data."""
        # Arrange
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, tzinfo=timezone.utc)

        mock_session = AsyncMock()

        # Mock get_data_range returning None
        mock_row = MagicMock(spec=Row)
        mock_row.start_date = None
        mock_row.end_date = None

        mock_result = MagicMock()
        mock_result.fetchone.return_value = mock_row

        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.services.database_repository.get_session") as mock_get_session:
            mock_get_session.return_value.__aenter__.return_value = mock_session

            # Act
            result = await repository.get_adjusted_date_range("NONEXISTENT", start, end)

            # Assert
            assert result is None


class TestValidateDataAvailability:
    """Test suite for validate_data_availability method."""

    @pytest.fixture
    def repository(self):
        """Create DatabaseRepository instance."""
        return DatabaseRepository()

    @pytest.mark.asyncio
    async def test_validate_data_availability_returns_valid_when_data_exists(self, repository):
        """Validate data availability returns valid=True when data covers range."""
        # Arrange
        start = datetime(2024, 2, 1, tzinfo=timezone.utc)
        end = datetime(2024, 2, 28, tzinfo=timezone.utc)

        data_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        data_end = datetime(2024, 12, 31, tzinfo=timezone.utc)

        # Mock get_data_range to return valid range
        with patch.object(
            repository, "get_data_range", return_value={"start": data_start, "end": data_end}
        ):
            # Act
            result = await repository.validate_data_availability("AAPL", start, end)

            # Assert
            assert result["valid"] is True
            assert "available_range" in result

    @pytest.mark.asyncio
    async def test_validate_data_availability_returns_invalid_when_no_symbol(self, repository):
        """Validate data availability returns invalid when symbol not found."""
        # Arrange
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, tzinfo=timezone.utc)

        mock_row = MagicMock(spec=Row)
        mock_row.start_date = None
        mock_row.end_date = None

        mock_result = MagicMock()
        mock_result.fetchone.return_value = mock_row

        # Mock for get_available_symbols
        mock_symbols_result = MagicMock()
        mock_symbols_result.fetchall.return_value = []

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[mock_result, mock_symbols_result])

        with patch("src.services.database_repository.get_session") as mock_get_session:
            mock_get_session.return_value.__aenter__.return_value = mock_session

            # Act
            result = await repository.validate_data_availability("NONEXISTENT", start, end)

            # Assert
            assert result["valid"] is False
            assert "No data available" in result["reason"]
            assert "available_symbols" in result

    @pytest.mark.asyncio
    async def test_validate_data_availability_returns_invalid_when_start_too_early(
        self, repository
    ):
        """Validate data availability returns invalid when start date too early."""
        # Arrange
        start = datetime(2023, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 12, 31, tzinfo=timezone.utc)

        data_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        data_end = datetime(2024, 12, 31, tzinfo=timezone.utc)

        # Mock get_data_range to return valid range
        with patch.object(
            repository, "get_data_range", return_value={"start": data_start, "end": data_end}
        ):
            # Act
            result = await repository.validate_data_availability("AAPL", start, end)

            # Assert
            assert result["valid"] is False
            assert "before available data start" in result["reason"]
            assert "available_range" in result

    @pytest.mark.asyncio
    async def test_validate_data_availability_returns_invalid_when_end_too_late(self, repository):
        """Validate data availability returns invalid when end date too late."""
        # Arrange
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2025, 12, 31, tzinfo=timezone.utc)

        data_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        data_end = datetime(2024, 12, 31, tzinfo=timezone.utc)

        # Mock get_data_range to return valid range
        with patch.object(
            repository, "get_data_range", return_value={"start": data_start, "end": data_end}
        ):
            # Act
            result = await repository.validate_data_availability("AAPL", start, end)

            # Assert
            assert result["valid"] is False
            assert "after available data end" in result["reason"]
            assert "available_range" in result

    @pytest.mark.asyncio
    async def test_validate_data_availability_handles_naive_datetimes(self, repository):
        """Validate data availability handles naive datetimes by adding UTC timezone."""
        # Arrange - naive datetimes (no timezone)
        start = datetime(2024, 1, 1)
        end = datetime(2024, 12, 31)

        data_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        data_end = datetime(2024, 12, 31, tzinfo=timezone.utc)

        # Mock get_data_range to return valid range
        with patch.object(
            repository, "get_data_range", return_value={"start": data_start, "end": data_end}
        ):
            # Act
            result = await repository.validate_data_availability("AAPL", start, end)

            # Assert - should handle naive datetimes without error
            assert result["valid"] is True
