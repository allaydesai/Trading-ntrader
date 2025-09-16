"""Tests for data CLI commands."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch
from click.testing import CliRunner

from src.cli.commands.data import data, import_csv, list_data


class TestDataCommands:
    """Test cases for data CLI commands."""

    def test_data_group_exists(self):
        """Test that data command group exists."""
        runner = CliRunner()
        result = runner.invoke(data, ["--help"])
        assert result.exit_code == 0
        assert "Data management commands" in result.output

    @patch("src.cli.commands.data.test_connection")
    @patch("src.cli.commands.data.CSVLoader")
    def test_import_csv_success(self, mock_csv_loader, mock_test_connection):
        """Test successful CSV import."""
        # Setup mocks
        mock_test_connection.return_value = True

        mock_loader_instance = AsyncMock()
        mock_loader_instance.load_file.return_value = {
            "file": "/tmp/test.csv",
            "symbol": "AAPL",
            "records_processed": 100,
            "records_inserted": 95,
            "duplicates_skipped": 5,
        }
        mock_csv_loader.return_value = mock_loader_instance

        # Create temporary CSV file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("timestamp,open,high,low,close,volume\n")
            f.write("2024-01-01,100,101,99,100.5,1000\n")
            csv_file = Path(f.name)

        try:
            runner = CliRunner()
            result = runner.invoke(
                import_csv, ["--file", str(csv_file), "--symbol", "AAPL"]
            )

            assert result.exit_code == 0
            assert "Successfully imported 95 records" in result.output
            assert "Skipped 5 duplicate records" in result.output

            # Verify CSV loader was called correctly
            mock_csv_loader.assert_called_once()
            mock_loader_instance.load_file.assert_called_once_with(csv_file, "AAPL")

        finally:
            # Clean up temp file
            csv_file.unlink(missing_ok=True)

    @patch("src.cli.commands.data.test_connection")
    def test_import_csv_database_not_accessible(self, mock_test_connection):
        """Test CSV import when database is not accessible."""
        mock_test_connection.return_value = False

        # Create temporary CSV file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            csv_file = Path(f.name)

        try:
            runner = CliRunner()
            result = runner.invoke(
                import_csv, ["--file", str(csv_file), "--symbol", "AAPL"]
            )

            assert result.exit_code == 1
            assert "Database not accessible" in result.output

        finally:
            # Clean up temp file
            csv_file.unlink(missing_ok=True)

    def test_import_csv_file_not_found(self):
        """Test CSV import with non-existent file."""
        runner = CliRunner()
        result = runner.invoke(
            import_csv, ["--file", "/non/existent/file.csv", "--symbol", "AAPL"]
        )

        assert result.exit_code == 2  # Click validation error
        assert "does not exist" in result.output

    @patch("src.cli.commands.data.test_connection")
    @patch("src.cli.commands.data.CSVLoader")
    def test_import_csv_file_not_found_runtime(
        self, mock_csv_loader, mock_test_connection
    ):
        """Test CSV import with file not found during runtime."""
        mock_test_connection.return_value = True

        mock_loader_instance = AsyncMock()
        mock_loader_instance.load_file.side_effect = FileNotFoundError("File not found")
        mock_csv_loader.return_value = mock_loader_instance

        # Create temporary file that exists for Click validation
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            csv_file = Path(f.name)

        try:
            runner = CliRunner()
            result = runner.invoke(
                import_csv, ["--file", str(csv_file), "--symbol", "AAPL"]
            )

            assert result.exit_code == 1
            assert "File not found" in result.output

        finally:
            # Clean up temp file
            csv_file.unlink(missing_ok=True)

    @patch("src.cli.commands.data.test_connection")
    @patch("src.cli.commands.data.CSVLoader")
    def test_import_csv_invalid_format(self, mock_csv_loader, mock_test_connection):
        """Test CSV import with invalid CSV format."""
        mock_test_connection.return_value = True

        mock_loader_instance = AsyncMock()
        mock_loader_instance.load_file.side_effect = ValueError(
            "Missing required columns"
        )
        mock_csv_loader.return_value = mock_loader_instance

        # Create temporary CSV file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("invalid,csv,headers\n")
            csv_file = Path(f.name)

        try:
            runner = CliRunner()
            result = runner.invoke(
                import_csv, ["--file", str(csv_file), "--symbol", "AAPL"]
            )

            assert result.exit_code == 1
            assert "Invalid CSV format" in result.output

        finally:
            # Clean up temp file
            csv_file.unlink(missing_ok=True)

    @patch("src.cli.commands.data.test_connection")
    @patch("src.cli.commands.data.CSVLoader")
    def test_import_csv_general_exception(self, mock_csv_loader, mock_test_connection):
        """Test CSV import with general exception."""
        mock_test_connection.return_value = True

        mock_loader_instance = AsyncMock()
        mock_loader_instance.load_file.side_effect = Exception(
            "Database connection failed"
        )
        mock_csv_loader.return_value = mock_loader_instance

        # Create temporary CSV file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            csv_file = Path(f.name)

        try:
            runner = CliRunner()
            result = runner.invoke(
                import_csv, ["--file", str(csv_file), "--symbol", "AAPL"]
            )

            assert result.exit_code == 1
            assert "Import failed" in result.output

        finally:
            # Clean up temp file
            csv_file.unlink(missing_ok=True)

    @patch("src.cli.commands.data.test_connection")
    @patch("src.cli.commands.data.CSVLoader")
    def test_import_csv_no_duplicates(self, mock_csv_loader, mock_test_connection):
        """Test CSV import with no duplicates."""
        mock_test_connection.return_value = True

        mock_loader_instance = AsyncMock()
        mock_loader_instance.load_file.return_value = {
            "file": "/tmp/test.csv",
            "symbol": "AAPL",
            "records_processed": 100,
            "records_inserted": 100,
            "duplicates_skipped": 0,
        }
        mock_csv_loader.return_value = mock_loader_instance

        # Create temporary CSV file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            csv_file = Path(f.name)

        try:
            runner = CliRunner()
            result = runner.invoke(
                import_csv, ["--file", str(csv_file), "--symbol", "AAPL"]
            )

            assert result.exit_code == 0
            assert "Successfully imported 100 records" in result.output
            # The table will show duplicates skipped but no warning message should appear
            assert "Skipped 0 duplicate records" not in result.output

        finally:
            # Clean up temp file
            csv_file.unlink(missing_ok=True)

    def test_import_csv_required_parameters(self):
        """Test that import-csv requires file and symbol parameters."""
        runner = CliRunner()

        # Test missing file parameter
        result = runner.invoke(import_csv, ["--symbol", "AAPL"])
        assert result.exit_code == 2
        assert "Missing option" in result.output

        # Test missing symbol parameter - use a temporary file for Click validation
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            csv_file = Path(f.name)

        try:
            result = runner.invoke(import_csv, ["--file", str(csv_file)])
            assert result.exit_code == 2
            assert "Missing option" in result.output
        finally:
            csv_file.unlink(missing_ok=True)

    def test_list_data_command_exists(self):
        """Test that list command exists."""
        runner = CliRunner()
        result = runner.invoke(list_data, ["--help"])
        assert result.exit_code == 0
        assert "List available market data" in result.output

    def test_list_data_without_symbol(self):
        """Test list data command without symbol filter."""
        runner = CliRunner()
        result = runner.invoke(list_data)

        assert result.exit_code == 0
        assert "Data listing feature coming soon" in result.output

    def test_list_data_with_symbol(self):
        """Test list data command with symbol filter."""
        runner = CliRunner()
        result = runner.invoke(list_data, ["--symbol", "AAPL"])

        assert result.exit_code == 0
        assert "Data listing feature coming soon" in result.output
        assert "Filtering by symbol: AAPL" in result.output

    def test_list_data_symbol_case_handling(self):
        """Test that list data converts symbol to uppercase."""
        runner = CliRunner()
        result = runner.invoke(list_data, ["--symbol", "aapl"])

        assert result.exit_code == 0
        assert "Filtering by symbol: AAPL" in result.output

    def test_import_csv_symbol_case_handling(self):
        """Test that import-csv converts symbol to uppercase."""
        with patch("src.cli.commands.data.test_connection", return_value=True):
            with patch("src.cli.commands.data.CSVLoader") as mock_csv_loader:
                mock_loader_instance = AsyncMock()
                mock_loader_instance.load_file.return_value = {
                    "file": "/tmp/test.csv",
                    "symbol": "AAPL",
                    "records_processed": 1,
                    "records_inserted": 1,
                    "duplicates_skipped": 0,
                }
                mock_csv_loader.return_value = mock_loader_instance

                # Create temporary CSV file
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".csv", delete=False
                ) as f:
                    csv_file = Path(f.name)

                try:
                    runner = CliRunner()
                    result = runner.invoke(
                        import_csv,
                        [
                            "--file",
                            str(csv_file),
                            "--symbol",
                            "aapl",  # lowercase
                        ],
                    )

                    assert result.exit_code == 0
                    # Verify the loader was called with uppercase symbol
                    mock_loader_instance.load_file.assert_called_once_with(
                        csv_file, "AAPL"
                    )

                finally:
                    # Clean up temp file
                    csv_file.unlink(missing_ok=True)
