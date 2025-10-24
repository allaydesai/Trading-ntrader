"""Tests for CSV loader service."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
import pandas as pd
import pytest

from src.services.csv_loader import CSVLoader, ValidationError
from nautilus_trader.model.data import Bar


class TestCSVLoader:
    """Test cases for CSVLoader service."""

    @patch("src.services.csv_loader.DataCatalogService")
    @pytest.mark.component
    def test_init_default(self, mock_catalog_service_class):
        """Test CSV loader initialization with defaults."""
        mock_catalog_service_class.return_value = MagicMock()
        loader = CSVLoader()
        assert loader.conflict_mode == "skip"
        assert loader.catalog_service is not None

    @patch("src.services.csv_loader.DataCatalogService")
    @pytest.mark.component
    def test_init_with_conflict_mode(self, mock_catalog_service_class):
        """Test CSV loader initialization with custom conflict mode."""
        mock_catalog_service = MagicMock()
        loader = CSVLoader(
            catalog_service=mock_catalog_service, conflict_mode="overwrite"
        )
        assert loader.conflict_mode == "overwrite"
        assert loader.catalog_service == mock_catalog_service

    @pytest.mark.asyncio
    @patch("src.services.csv_loader.DataCatalogService")
    async def test_load_file_not_exists(self, mock_catalog_service_class):
        """Test load_file with non-existent file."""
        mock_catalog_service_class.return_value = MagicMock()
        loader = CSVLoader()
        non_existent_file = Path("/non/existent/file.csv")

        with pytest.raises(FileNotFoundError, match="CSV file not found"):
            await loader.load_file(non_existent_file, "AAPL", "NASDAQ")

    @patch("src.services.csv_loader.DataCatalogService")
    @pytest.mark.component
    def test_validate_columns_valid(self, mock_catalog_service_class):
        """Test _validate_columns with valid DataFrame."""
        mock_catalog_service_class.return_value = MagicMock()
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

    @patch("src.services.csv_loader.DataCatalogService")
    @pytest.mark.component
    def test_validate_columns_missing_required(self, mock_catalog_service_class):
        """Test _validate_columns with missing required columns."""
        mock_catalog_service_class.return_value = MagicMock()
        loader = CSVLoader()
        df = pd.DataFrame(
            {
                "timestamp": ["2024-01-01"],
                "open": [100.0],
                "high": [101.0],
                # Missing 'low', 'close', 'volume'
            }
        )

        with pytest.raises(ValidationError, match="Missing required columns"):
            loader._validate_columns(df)

    @pytest.mark.asyncio
    @patch("src.services.csv_loader.DataCatalogService")
    async def test_convert_to_bars_valid(self, mock_catalog_service_class):
        """Test _convert_to_bars with valid data."""
        mock_catalog_service_class.return_value = MagicMock()
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

        bars, errors = await loader._convert_to_bars(
            df, "AAPL", "NASDAQ", "1-MINUTE-LAST"
        )

        assert len(bars) == 1
        assert len(errors) == 0
        bar = bars[0]
        assert isinstance(bar, Bar)
        assert bar.open.as_double() == 100.50
        assert bar.high.as_double() == 101.00
        assert bar.low.as_double() == 100.25
        assert bar.close.as_double() == 100.75
        assert bar.volume.as_double() == 10000.0

    @pytest.mark.asyncio
    @patch("src.services.csv_loader.DataCatalogService")
    async def test_convert_to_bars_invalid_data(self, mock_catalog_service_class):
        """Test _convert_to_bars with invalid data."""
        mock_catalog_service_class.return_value = MagicMock()
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

        bars, errors = await loader._convert_to_bars(
            df, "AAPL", "NASDAQ", "1-MINUTE-LAST"
        )

        # Should collect validation errors rather than raising
        assert len(bars) == 0
        assert len(errors) > 0
        assert (
            "Invalid timestamp format" in errors[0] or "Invalid OHLCV data" in errors[0]
        )

    @pytest.mark.asyncio
    @patch("src.services.csv_loader.DataCatalogService")
    async def test_convert_to_bars_timezone_handling(self, mock_catalog_service_class):
        """Test _convert_to_bars handles timezone correctly."""
        mock_catalog_service_class.return_value = MagicMock()
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

        bars, errors = await loader._convert_to_bars(
            df, "AAPL", "NASDAQ", "1-MINUTE-LAST"
        )

        assert len(bars) == 1
        assert len(errors) == 0
        # Bar timestamps are in nanoseconds (UTC)
        bar = bars[0]
        assert bar.ts_event > 0  # Should have valid timestamp

    @pytest.mark.asyncio
    @patch("src.services.csv_loader.DataCatalogService")
    async def test_convert_to_bars_ohlc_validation(self, mock_catalog_service_class):
        """Test _convert_to_bars validates OHLC relationships."""
        mock_catalog_service_class.return_value = MagicMock()
        loader = CSVLoader()

        # Invalid: high < low
        df = pd.DataFrame(
            {
                "timestamp": ["2024-01-01 09:30:00"],
                "open": [100.50],
                "high": [99.00],  # high less than low - invalid
                "low": [100.25],
                "close": [100.75],
                "volume": [10000],
            }
        )

        bars, errors = await loader._convert_to_bars(
            df, "AAPL", "NASDAQ", "1-MINUTE-LAST"
        )

        # Should have validation error
        assert len(bars) == 0
        assert len(errors) > 0
        assert "high" in errors[0].lower() and "low" in errors[0].lower()

    @pytest.mark.asyncio
    @patch("src.services.csv_loader.pd.read_csv")
    @patch("src.services.csv_loader.DataCatalogService")
    async def test_load_file_success(self, mock_catalog_service_class, mock_read_csv):
        """Test successful load_file workflow."""
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

            # Mock catalog service
            mock_catalog_service = MagicMock()
            mock_catalog_service.get_availability.return_value = (
                None  # No existing data
            )
            mock_catalog_service.write_bars = MagicMock()
            mock_catalog_service.catalog_path = Path("/tmp/catalog")
            mock_catalog_service_class.return_value = mock_catalog_service

            # Mock glob to return no files initially
            with patch.object(Path, "glob", return_value=[]):
                loader = CSVLoader(catalog_service=mock_catalog_service)
                result = await loader.load_file(
                    csv_file, "AAPL", "NASDAQ", "1-MINUTE-LAST"
                )

            # Verify result structure
            assert result["file"] == str(csv_file)
            assert result["instrument_id"] == "AAPL.NASDAQ"
            assert result["bar_type_spec"] == "1-MINUTE-LAST"
            assert result["rows_processed"] == 1
            assert result["bars_written"] == 1
            assert result["conflicts_skipped"] == 0
            assert len(result["validation_errors"]) == 0

            # Verify pandas was called
            mock_read_csv.assert_called_once_with(csv_file)

            # Verify catalog write was called
            mock_catalog_service.write_bars.assert_called_once()

        finally:
            # Clean up temp file
            csv_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    @patch("src.services.csv_loader.pd.read_csv")
    @patch("src.services.csv_loader.DataCatalogService")
    async def test_load_file_with_validation_error(
        self, mock_catalog_service_class, mock_read_csv
    ):
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

            mock_catalog_service_class.return_value = MagicMock()
            loader = CSVLoader()

            with pytest.raises(ValidationError, match="Missing required columns"):
                await loader.load_file(csv_file, "AAPL", "NASDAQ", "1-MINUTE-LAST")

        finally:
            # Clean up temp file
            csv_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    @patch("src.services.csv_loader.pd.read_csv")
    @patch("src.services.csv_loader.DataCatalogService")
    async def test_load_file_skip_conflicts(
        self, mock_catalog_service_class, mock_read_csv
    ):
        """Test load_file with conflict_mode='skip'."""
        # Create a temporary file
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

            # Mock catalog service with existing data
            mock_catalog_service = MagicMock()
            mock_availability = MagicMock()
            mock_availability.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
            mock_availability.end_date = datetime(2024, 1, 2, tzinfo=timezone.utc)
            mock_catalog_service.get_availability.return_value = mock_availability
            mock_catalog_service.catalog_path = Path("/tmp/catalog")
            mock_catalog_service_class.return_value = mock_catalog_service

            # Mock glob to return no files
            with patch.object(Path, "glob", return_value=[]):
                loader = CSVLoader(
                    catalog_service=mock_catalog_service, conflict_mode="skip"
                )
                result = await loader.load_file(
                    csv_file, "AAPL", "NASDAQ", "1-MINUTE-LAST"
                )

            # Should skip all bars due to existing data
            assert result["bars_written"] == 0
            assert result["conflicts_skipped"] == 1  # 1 bar in the CSV
            mock_catalog_service.write_bars.assert_not_called()

        finally:
            csv_file.unlink(missing_ok=True)

    @patch("src.services.csv_loader.DataCatalogService")
    @pytest.mark.component
    def test_parse_timestamp_valid(self, mock_catalog_service_class):
        """Test _parse_timestamp with valid timestamp."""
        mock_catalog_service_class.return_value = MagicMock()
        loader = CSVLoader()

        timestamp = loader._parse_timestamp("2024-01-01 09:30:00", 1)

        assert isinstance(timestamp, pd.Timestamp)
        assert timestamp.tz is not None  # Should be timezone-aware

    @patch("src.services.csv_loader.DataCatalogService")
    @pytest.mark.component
    def test_parse_timestamp_invalid(self, mock_catalog_service_class):
        """Test _parse_timestamp with invalid timestamp."""
        mock_catalog_service_class.return_value = MagicMock()
        loader = CSVLoader()

        with pytest.raises(ValidationError, match="Invalid timestamp format"):
            loader._parse_timestamp("not-a-date", 1)

    @patch("src.services.csv_loader.DataCatalogService")
    @pytest.mark.component
    def test_validate_ohlcv_valid(self, mock_catalog_service_class):
        """Test _validate_ohlcv with valid OHLCV data."""
        mock_catalog_service_class.return_value = MagicMock()
        loader = CSVLoader()

        row = pd.Series(
            {
                "open": 100.50,
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            }
        )

        # Should not raise
        open_p, high_p, low_p, close_p, volume = loader._validate_ohlcv(row, 1)

        assert open_p.as_double() == 100.50
        assert high_p.as_double() == 101.00
        assert low_p.as_double() == 100.25
        assert close_p.as_double() == 100.75
        assert volume.as_double() == 10000.0

    @patch("src.services.csv_loader.DataCatalogService")
    @pytest.mark.component
    def test_validate_ohlcv_negative_price(self, mock_catalog_service_class):
        """Test _validate_ohlcv rejects negative prices."""
        mock_catalog_service_class.return_value = MagicMock()
        loader = CSVLoader()

        row = pd.Series(
            {
                "open": -100.50,  # Negative price
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            }
        )

        with pytest.raises(ValidationError, match="open must be > 0"):
            loader._validate_ohlcv(row, 1)

    @patch("src.services.csv_loader.DataCatalogService")
    @pytest.mark.component
    def test_validate_ohlcv_high_less_than_low(self, mock_catalog_service_class):
        """Test _validate_ohlcv rejects high < low."""
        mock_catalog_service_class.return_value = MagicMock()
        loader = CSVLoader()

        row = pd.Series(
            {
                "open": 100.50,
                "high": 100.00,  # High less than low
                "low": 100.25,
                "close": 100.75,
                "volume": 10000,
            }
        )

        with pytest.raises(ValidationError, match="high.*must be.*low"):
            loader._validate_ohlcv(row, 1)

    @patch("src.services.csv_loader.DataCatalogService")
    @pytest.mark.component
    def test_validate_ohlcv_negative_volume(self, mock_catalog_service_class):
        """Test _validate_ohlcv rejects negative volume."""
        mock_catalog_service_class.return_value = MagicMock()
        loader = CSVLoader()

        row = pd.Series(
            {
                "open": 100.50,
                "high": 101.00,
                "low": 100.25,
                "close": 100.75,
                "volume": -1000,  # Negative volume
            }
        )

        with pytest.raises(ValidationError, match="volume must be >= 0"):
            loader._validate_ohlcv(row, 1)
