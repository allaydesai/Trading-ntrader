"""Integration test for CSV import functionality."""

import tempfile
from pathlib import Path

import pytest

from src.config import get_settings


@pytest.mark.integration
@pytest.mark.asyncio
async def test_csv_import_stores_to_database():
    """INTEGRATION: CSV → Database flow - MUST FAIL INITIALLY."""
    settings = get_settings()

    # Skip if database not configured
    if not settings.database_url:
        pytest.skip("Database not configured")

    # Skip if database not accessible
    from src.db.session import test_connection
    if not await test_connection():
        pytest.skip("Database not accessible")

    # Clean up any existing TEST data first
    from src.db.session import get_session
    from sqlalchemy import text
    async with get_session() as cleanup_session:
        await cleanup_session.execute(text("DELETE FROM market_data WHERE symbol = 'TEST'"))
        await cleanup_session.commit()

    # Create temporary CSV file
    csv_content = """timestamp,open,high,low,close,volume
2024-01-01 09:30:00,100.50,101.00,100.25,100.75,10000
2024-01-01 09:31:00,100.75,101.25,100.50,101.00,8500
2024-01-01 09:32:00,101.00,101.50,100.75,101.25,9200"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write(csv_content)
        csv_file = Path(f.name)

    try:
        # This should fail - we haven't implemented CSV import yet
        from src.services.csv_loader import CSVLoader
        from src.db.session import get_session

        async with get_session() as session:
            loader = CSVLoader(session)
            result = await loader.load_file(csv_file, "TEST")

            # Verify data was stored
            assert result["records_inserted"] == 3
            assert result["symbol"] == "TEST"
            assert result["duplicates_skipped"] == 0

    finally:
        # Clean up CSV file
        csv_file.unlink()

        # Clean up test data
        async with get_session() as cleanup_session:
            await cleanup_session.execute(text("DELETE FROM market_data WHERE symbol = 'TEST'"))
            await cleanup_session.commit()


@pytest.mark.integration
def test_csv_import_command_exists():
    """INTEGRATION: Verify CSV import command is available - MUST FAIL INITIALLY."""
    from src.cli.main import cli

    # Check that 'data import-csv' command exists
    result = cli.get_command(None, 'data')
    assert result is not None, "Data command group should exist"

    # This will fail initially - we haven't added the command yet
    data_cmd = result
    import_cmd = data_cmd.get_command(None, 'import-csv')
    assert import_cmd is not None, "import-csv command should exist"