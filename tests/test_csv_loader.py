"""Tests for CSV loader service."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal
from datetime import datetime
import pandas as pd
import pytest

from src.services.csv_loader import CSVLoader
from src.models.market_data import MarketDataCreate


class TestCSVLoader:
    """Test cases for CSVLoader service."""

    def test_init_without_session(self):
        """Test CSV loader initialization without session."""
        loader = CSVLoader()
        assert loader.session is None

    def test_init_with_session(self):
        """Test CSV loader initialization with session."""
        mock_session = MagicMock()
        loader = CSVLoader(session=mock_session)
        assert loader.session == mock_session

    @pytest.mark.asyncio
    async def test_load_file_not_exists(self):
        """Test load_file with non-existent file."""
        loader = CSVLoader()
        non_existent_file = Path("/non/existent/file.csv")

        with pytest.raises(FileNotFoundError, match="CSV file not found"):
            await loader.load_file(non_existent_file, "AAPL")

    def test_validate_columns_valid(self):
        """Test _validate_columns with valid DataFrame."""
        loader = CSVLoader()
        df = pd.DataFrame(
            {
                "timestamp": ["2024-01-01"],
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.5],
                "volume": [1000],
            }
        )

        # Should not raise any exception
        loader._validate_columns(df)

    def test_validate_columns_missing_required(self):
        """Test _validate_columns with missing required columns."""
        loader = CSVLoader()
        df = pd.DataFrame(
            {
                "timestamp": ["2024-01-01"],
                "open": [100.0],
                "high": [101.0],
                # Missing 'low', 'close', 'volume'
            }
        )

        with pytest.raises(ValueError, match="Missing required columns"):
            loader._validate_columns(df)

    def test_transform_to_records_valid(self):
        """Test _transform_to_records with valid data."""
        loader = CSVLoader()
        df = pd.DataFrame(
            {
                "timestamp": ["2024-01-01 09:30:00"],
                "open": [100.50],
                "high": [101.00],
                "low": [100.25],
                "close": [100.75],
                "volume": [10000],
            }
        )

        records = loader._transform_to_records(df, "AAPL")

        assert len(records) == 1
        record = records[0]
        assert isinstance(record, MarketDataCreate)
        assert record.symbol == "AAPL"
        assert record.open == Decimal("100.50")
        assert record.high == Decimal("101.00")
        assert record.low == Decimal("100.25")
        assert record.close == Decimal("100.75")
        assert record.volume == 10000

    def test_transform_to_records_invalid_data(self):
        """Test _transform_to_records with invalid data."""
        loader = CSVLoader()
        df = pd.DataFrame(
            {
                "timestamp": ["invalid-date"],
                "open": ["not-a-number"],
                "high": [101.00],
                "low": [100.25],
                "close": [100.75],
                "volume": [10000],
            }
        )

        with pytest.raises(ValueError, match="Invalid data in row"):
            loader._transform_to_records(df, "AAPL")

    def test_transform_to_records_timezone_handling(self):
        """Test _transform_to_records handles timezone correctly."""
        loader = CSVLoader()
        df = pd.DataFrame(
            {
                "timestamp": ["2024-01-01 09:30:00"],
                "open": [100.50],
                "high": [101.00],
                "low": [100.25],
                "close": [100.75],
                "volume": [10000],
            }
        )

        records = loader._transform_to_records(df, "AAPL")

        assert len(records) == 1
        record = records[0]
        # Should have timezone info (UTC)
        assert record.timestamp.tzinfo is not None

    @pytest.mark.asyncio
    @patch("src.services.csv_loader.get_session")
    async def test_bulk_insert_records_no_session(self, mock_get_session):
        """Test _bulk_insert_records without provided session."""
        mock_session = AsyncMock()
        mock_get_session.return_value.__aenter__.return_value = mock_session

        # Mock execute result
        mock_result = MagicMock()
        mock_result.rowcount = 2
        mock_session.execute.return_value = mock_result

        loader = CSVLoader()  # No session provided
        records = [
            MarketDataCreate(
                symbol="AAPL",
                timestamp=datetime.now(),
                open=Decimal("100.0"),
                high=Decimal("101.0"),
                low=Decimal("99.0"),
                close=Decimal("100.5"),
                volume=1000,
            ),
            MarketDataCreate(
                symbol="AAPL",
                timestamp=datetime.now(),
                open=Decimal("101.0"),
                high=Decimal("102.0"),
                low=Decimal("100.0"),
                close=Decimal("101.5"),
                volume=1500,
            ),
        ]

        result = await loader._bulk_insert_records(records)

        assert result["inserted"] == 2
        assert result["skipped"] == 0
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_insert_records_with_session(self):
        """Test _bulk_insert_records with provided session."""
        mock_session = AsyncMock()

        # Mock execute result
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute.return_value = mock_result

        loader = CSVLoader(session=mock_session)
        records = [
            MarketDataCreate(
                symbol="AAPL",
                timestamp=datetime.now(),
                open=Decimal("100.0"),
                high=Decimal("101.0"),
                low=Decimal("99.0"),
                close=Decimal("100.5"),
                volume=1000,
            )
        ]

        result = await loader._bulk_insert_records(records)

        assert result["inserted"] == 1
        assert result["skipped"] == 0
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_bulk_insert_records_with_duplicates(self):
        """Test _bulk_insert_records with duplicate handling."""
        mock_session = AsyncMock()

        # Mock execute result with fewer inserts (duplicates skipped)
        mock_result = MagicMock()
        mock_result.rowcount = 1  # Only 1 inserted out of 2 records
        mock_session.execute.return_value = mock_result

        loader = CSVLoader(session=mock_session)
        records = [
            MarketDataCreate(
                symbol="AAPL",
                timestamp=datetime.now(),
                open=Decimal("100.0"),
                high=Decimal("101.0"),
                low=Decimal("99.0"),
                close=Decimal("100.5"),
                volume=1000,
            ),
            MarketDataCreate(  # Duplicate
                symbol="AAPL",
                timestamp=datetime.now(),
                open=Decimal("100.0"),
                high=Decimal("101.0"),
                low=Decimal("99.0"),
                close=Decimal("100.5"),
                volume=1000,
            ),
        ]

        result = await loader._bulk_insert_records(records)

        assert result["inserted"] == 1
        assert result["skipped"] == 1

    @pytest.mark.asyncio
    @patch("src.services.csv_loader.pd.read_csv")
    async def test_load_file_complete_workflow(self, mock_read_csv):
        """Test complete load_file workflow."""
        # Create a temporary file for testing
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            csv_file = Path(f.name)

        try:
            # Mock pandas read_csv
            mock_df = pd.DataFrame(
                {
                    "timestamp": ["2024-01-01 09:30:00"],
                    "open": [100.50],
                    "high": [101.00],
                    "low": [100.25],
                    "close": [100.75],
                    "volume": [10000],
                }
            )
            mock_read_csv.return_value = mock_df

            # Mock the session and database operations
            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.rowcount = 1
            mock_session.execute.return_value = mock_result

            loader = CSVLoader(session=mock_session)

            result = await loader.load_file(csv_file, "AAPL")

            # Verify result structure
            assert result["file"] == str(csv_file)
            assert result["symbol"] == "AAPL"
            assert result["records_processed"] == 1
            assert result["records_inserted"] == 1
            assert result["duplicates_skipped"] == 0

            # Verify pandas was called
            mock_read_csv.assert_called_once_with(csv_file)

            # Verify database operations
            mock_session.execute.assert_called_once()
            mock_session.commit.assert_called_once()

        finally:
            # Clean up temp file
            csv_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    @patch("src.services.csv_loader.pd.read_csv")
    async def test_load_file_with_validation_error(self, mock_read_csv):
        """Test load_file with CSV validation error."""
        # Create a temporary file for testing
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            csv_file = Path(f.name)

        try:
            # Mock pandas read_csv with invalid DataFrame
            mock_df = pd.DataFrame(
                {
                    "timestamp": ["2024-01-01 09:30:00"],
                    "open": [100.50],
                    # Missing required columns
                }
            )
            mock_read_csv.return_value = mock_df

            loader = CSVLoader()

            with pytest.raises(ValueError, match="Missing required columns"):
                await loader.load_file(csv_file, "AAPL")

        finally:
            # Clean up temp file
            csv_file.unlink(missing_ok=True)
