"""Tests for data CLI commands."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli.commands.data import data


class TestDataCommands:
    """Test cases for data CLI commands."""

    @pytest.mark.component
    def test_data_group_exists(self):
        """Test that data command group exists."""
        runner = CliRunner()
        result = runner.invoke(data, ["--help"])
        assert result.exit_code == 0
        assert "Data management commands" in result.output

    @patch("src.services.csv_loader.CSVLoader")
    @pytest.mark.component
    def test_import_csv_success(self, mock_csv_loader):
        """Test successful CSV import."""
        # Setup mocks
        mock_loader_instance = MagicMock()

        # Make load_file async
        async def mock_load_file(*args, **kwargs):
            return {
                "file": "/tmp/test.csv",
                "instrument_id": "AAPL.NASDAQ",
                "bar_type_spec": "1-MINUTE-LAST",
                "rows_processed": 100,
                "bars_written": 95,
                "conflicts_skipped": 5,
                "validation_errors": [],
                "date_range": "2024-01-01 09:30 to 2024-01-01 16:00",
                "file_size_kb": 3.5,
            }

        mock_loader_instance.load_file = mock_load_file
        mock_csv_loader.return_value = mock_loader_instance

        # Create temporary CSV file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("timestamp,open,high,low,close,volume\n")
            f.write("2024-01-01,100,101,99,100.5,1000\n")
            csv_file = Path(f.name)

        try:
            runner = CliRunner()
            result = runner.invoke(
                data,
                [
                    "import",
                    "--csv",
                    str(csv_file),
                    "--symbol",
                    "AAPL",
                    "--venue",
                    "NASDAQ",
                ],
            )

            assert result.exit_code == 0
            assert "Successfully imported 95 bars" in result.output

        finally:
            # Clean up temp file
            csv_file.unlink(missing_ok=True)

    @pytest.mark.component
    def test_import_csv_file_not_found(self):
        """Test CSV import with non-existent file."""
        runner = CliRunner()
        result = runner.invoke(
            data,
            [
                "import",
                "--csv",
                "/non/existent/file.csv",
                "--symbol",
                "AAPL",
                "--venue",
                "NASDAQ",
            ],
        )

        assert result.exit_code == 2  # Click validation error
        assert "does not exist" in result.output

    @patch("src.services.csv_loader.CSVLoader")
    @pytest.mark.component
    def test_import_csv_file_not_found_runtime(self, mock_csv_loader):
        """Test CSV import with file not found during runtime."""
        mock_loader_instance = MagicMock()

        async def mock_load_file(*args, **kwargs):
            raise FileNotFoundError("File not found")

        mock_loader_instance.load_file = mock_load_file
        mock_csv_loader.return_value = mock_loader_instance

        # Create temporary file that exists for Click validation
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            csv_file = Path(f.name)

        try:
            runner = CliRunner()
            result = runner.invoke(
                data,
                [
                    "import",
                    "--csv",
                    str(csv_file),
                    "--symbol",
                    "AAPL",
                    "--venue",
                    "NASDAQ",
                ],
            )

            assert result.exit_code != 0
            assert "File not found" in result.output

        finally:
            # Clean up temp file
            csv_file.unlink(missing_ok=True)

    @patch("src.services.csv_loader.CSVLoader")
    @pytest.mark.component
    def test_import_csv_invalid_format(self, mock_csv_loader):
        """Test CSV import with invalid CSV format."""
        from src.services.csv_loader import ValidationError

        mock_loader_instance = MagicMock()

        async def mock_load_file(*args, **kwargs):
            raise ValidationError(0, "Missing required columns")

        mock_loader_instance.load_file = mock_load_file
        mock_csv_loader.return_value = mock_loader_instance

        # Create temporary CSV file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("invalid,csv,headers\n")
            csv_file = Path(f.name)

        try:
            runner = CliRunner()
            result = runner.invoke(
                data,
                [
                    "import",
                    "--csv",
                    str(csv_file),
                    "--symbol",
                    "AAPL",
                    "--venue",
                    "NASDAQ",
                ],
            )

            assert result.exit_code != 0
            assert "Import failed" in result.output

        finally:
            # Clean up temp file
            csv_file.unlink(missing_ok=True)

    @patch("src.services.csv_loader.CSVLoader")
    @pytest.mark.component
    def test_import_csv_general_exception(self, mock_csv_loader):
        """Test CSV import with general exception."""
        mock_loader_instance = MagicMock()

        async def mock_load_file(*args, **kwargs):
            raise Exception("Unexpected error")

        mock_loader_instance.load_file = mock_load_file
        mock_csv_loader.return_value = mock_loader_instance

        # Create temporary CSV file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            csv_file = Path(f.name)

        try:
            runner = CliRunner()
            result = runner.invoke(
                data,
                [
                    "import",
                    "--csv",
                    str(csv_file),
                    "--symbol",
                    "AAPL",
                    "--venue",
                    "NASDAQ",
                ],
            )

            assert result.exit_code != 0
            assert "Import failed" in result.output

        finally:
            # Clean up temp file
            csv_file.unlink(missing_ok=True)

    @patch("src.services.csv_loader.CSVLoader")
    @pytest.mark.component
    def test_import_csv_no_conflicts(self, mock_csv_loader):
        """Test CSV import with no conflicts."""
        mock_loader_instance = MagicMock()

        async def mock_load_file(*args, **kwargs):
            return {
                "file": "/tmp/test.csv",
                "instrument_id": "AAPL.NASDAQ",
                "bar_type_spec": "1-MINUTE-LAST",
                "rows_processed": 100,
                "bars_written": 100,
                "conflicts_skipped": 0,
                "validation_errors": [],
                "date_range": "2024-01-01 09:30 to 2024-01-01 16:00",
                "file_size_kb": 3.5,
            }

        mock_loader_instance.load_file = mock_load_file
        mock_csv_loader.return_value = mock_loader_instance

        # Create temporary CSV file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            csv_file = Path(f.name)

        try:
            runner = CliRunner()
            result = runner.invoke(
                data,
                [
                    "import",
                    "--csv",
                    str(csv_file),
                    "--symbol",
                    "AAPL",
                    "--venue",
                    "NASDAQ",
                ],
            )

            assert result.exit_code == 0
            assert "Successfully imported 100 bars" in result.output

        finally:
            # Clean up temp file
            csv_file.unlink(missing_ok=True)

    @pytest.mark.component
    def test_import_csv_required_parameters(self):
        """Test that import requires csv and symbol parameters."""
        runner = CliRunner()

        # Test missing csv parameter
        result = runner.invoke(data, ["import", "--symbol", "AAPL", "--venue", "NASDAQ"])
        assert result.exit_code == 2
        assert "Missing option" in result.output

        # Test missing symbol parameter - use a temporary file for Click validation
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            csv_file = Path(f.name)

        try:
            result = runner.invoke(data, ["import", "--csv", str(csv_file), "--venue", "NASDAQ"])
            assert result.exit_code == 2
            assert "Missing option" in result.output
        finally:
            csv_file.unlink(missing_ok=True)

    @pytest.mark.component
    def test_list_data_command_exists(self):
        """Test that list command exists."""
        runner = CliRunner()
        result = runner.invoke(data, ["list", "--help"])
        assert result.exit_code == 0
        assert "all available market data" in result.output.lower()

    @patch("src.services.data_catalog.DataCatalogService")
    @pytest.mark.component
    def test_list_data_without_symbol(self, mock_catalog_service_class):
        """Test list data command without symbol filter."""
        # Mock empty catalog
        mock_catalog_service = MagicMock()
        mock_catalog_service.scan_catalog.return_value = {}
        mock_catalog_service_class.return_value = mock_catalog_service

        runner = CliRunner()
        result = runner.invoke(data, ["list"])

        assert result.exit_code == 0
        assert "No data found in catalog" in result.output

    @patch("src.services.data_catalog.DataCatalogService")
    @pytest.mark.component
    def test_list_data_with_symbol(self, mock_catalog_service_class):
        """Test list data command with symbol filter."""
        # Mock catalog with data
        mock_catalog_service = MagicMock()
        mock_avail = MagicMock()
        mock_avail.bar_type_spec = "1-MINUTE-LAST"
        mock_avail.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_avail.end_date = datetime(2024, 1, 31, tzinfo=timezone.utc)
        mock_avail.file_count = 5
        mock_avail.total_rows = 1000

        mock_catalog_service.scan_catalog.return_value = {"AAPL.NASDAQ": [mock_avail]}
        mock_catalog_service_class.return_value = mock_catalog_service

        runner = CliRunner()
        result = runner.invoke(data, ["list", "--symbol", "AAPL"])

        assert result.exit_code == 0
        # Should show data for AAPL
        assert "AAPL" in result.output

    @patch("src.services.data_catalog.DataCatalogService")
    @pytest.mark.component
    def test_list_data_symbol_case_handling(self, mock_catalog_service_class):
        """Test that list data converts symbol to uppercase."""
        # Mock catalog with data
        mock_catalog_service = MagicMock()
        mock_avail = MagicMock()
        mock_avail.bar_type_spec = "1-MINUTE-LAST"
        mock_avail.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_avail.end_date = datetime(2024, 1, 31, tzinfo=timezone.utc)
        mock_avail.file_count = 5
        mock_avail.total_rows = 1000

        mock_catalog_service.scan_catalog.return_value = {"AAPL.NASDAQ": [mock_avail]}
        mock_catalog_service_class.return_value = mock_catalog_service

        runner = CliRunner()
        result = runner.invoke(data, ["list", "--symbol", "aapl"])

        assert result.exit_code == 0
        # Should filter by uppercase AAPL
        assert "AAPL" in result.output

    @patch("src.services.csv_loader.CSVLoader")
    @pytest.mark.component
    def test_import_csv_symbol_case_handling(self, mock_csv_loader):
        """Test that import converts symbol to uppercase."""
        mock_loader_instance = MagicMock()

        async def mock_load_file(file_path, symbol, venue, bar_type_spec):
            # Verify symbol was uppercased
            assert symbol == "AAPL"
            assert venue == "NASDAQ"
            return {
                "file": str(file_path),
                "instrument_id": f"{symbol}.{venue}",
                "bar_type_spec": bar_type_spec,
                "rows_processed": 1,
                "bars_written": 1,
                "conflicts_skipped": 0,
                "validation_errors": [],
                "date_range": "2024-01-01 09:30 to 2024-01-01 16:00",
                "file_size_kb": 0.5,
            }

        mock_loader_instance.load_file = mock_load_file
        mock_csv_loader.return_value = mock_loader_instance

        # Create temporary CSV file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            csv_file = Path(f.name)

        try:
            runner = CliRunner()
            result = runner.invoke(
                data,
                [
                    "import",
                    "--csv",
                    str(csv_file),
                    "--symbol",
                    "aapl",  # lowercase
                    "--venue",
                    "nasdaq",  # lowercase
                ],
            )

            assert result.exit_code == 0

        finally:
            # Clean up temp file
            csv_file.unlink(missing_ok=True)

    @patch("src.services.data_catalog.DataCatalogService")
    @pytest.mark.component
    def test_check_data_command_exists(self, mock_catalog_service_class):
        """Test that check command exists."""
        runner = CliRunner()
        result = runner.invoke(data, ["check", "--help"])
        assert result.exit_code == 0
        assert "Check data availability" in result.output

    @patch("src.services.data_catalog.DataCatalogService")
    @pytest.mark.component
    def test_check_data_no_data_found(self, mock_catalog_service_class):
        """Test check command when no data found."""
        mock_catalog_service = MagicMock()
        mock_catalog_service.get_availability.return_value = None
        mock_catalog_service_class.return_value = mock_catalog_service

        runner = CliRunner()
        result = runner.invoke(data, ["check", "--symbol", "AAPL", "--venue", "NASDAQ"])

        assert result.exit_code == 0
        assert "No data found" in result.output

    @patch("src.services.data_catalog.DataCatalogService")
    @pytest.mark.component
    def test_check_data_with_data(self, mock_catalog_service_class):
        """Test check command when data exists."""
        mock_catalog_service = MagicMock()
        mock_avail = MagicMock()
        mock_avail.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_avail.end_date = datetime(2024, 1, 31, tzinfo=timezone.utc)
        mock_avail.file_count = 5
        mock_avail.total_rows = 1000
        mock_avail.last_updated = datetime(2024, 2, 1, tzinfo=timezone.utc)

        mock_catalog_service.get_availability.return_value = mock_avail
        mock_catalog_service_class.return_value = mock_catalog_service

        runner = CliRunner()
        result = runner.invoke(data, ["check", "--symbol", "AAPL", "--venue", "NASDAQ"])

        assert result.exit_code == 0
        assert "Data Available" in result.output
