"""Integration test for CSV import functionality."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest



@pytest.mark.integration
@pytest.mark.asyncio
@patch("src.services.data_catalog.IBKRHistoricalClient")
async def test_csv_import_stores_to_database(mock_ibkr_client_class):
    """INTEGRATION: CSV → Parquet catalog flow."""
    # Mock IBKR client to prevent connection attempts
    mock_ibkr_client_class.return_value = MagicMock()

    # Create temporary CSV file
    csv_content = """timestamp,open,high,low,close,volume
2024-01-01 09:30:00,100.50,101.00,100.25,100.75,10000
2024-01-01 09:31:00,100.75,101.25,100.50,101.00,8500
2024-01-01 09:32:00,101.00,101.50,100.75,101.25,9200"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        csv_file = Path(f.name)

    # Create temporary catalog directory for clean test state
    with tempfile.TemporaryDirectory() as temp_catalog_dir:
        try:
            # Import CSV to Parquet catalog
            from src.services.csv_loader import CSVLoader
            from src.services.data_catalog import DataCatalogService

            # Create catalog service with temporary directory
            catalog_service = DataCatalogService(catalog_path=temp_catalog_dir)
            loader = CSVLoader(catalog_service=catalog_service)
            result = await loader.load_file(csv_file, "TEST", "NASDAQ", "1-MINUTE-LAST")

            # Verify data was stored
            assert result["bars_written"] == 3
            assert result["instrument_id"] == "TEST.NASDAQ"
            assert result["conflicts_skipped"] == 0

        finally:
            # Clean up CSV file
            csv_file.unlink()


@pytest.mark.integration
def test_csv_import_command_exists():
    """INTEGRATION: Verify CSV import command is available."""
    from src.cli.main import cli

    # Check that 'data' command group exists
    result = cli.get_command(None, "data")
    assert result is not None, "Data command group should exist"

    # Check that 'import' command exists (renamed from 'import-csv')
    data_cmd = result
    import_cmd = data_cmd.get_command(None, "import")
    assert import_cmd is not None, "import command should exist"
