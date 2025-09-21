"""Integration test for complete Milestone 2 workflow."""

import tempfile
from pathlib import Path
from datetime import datetime, timezone

import pytest

from src.config import get_settings
from src.db import session as db_session


@pytest.mark.integration
@pytest.mark.asyncio
async def test_complete_csv_to_backtest_workflow():
    """
    INTEGRATION: Complete workflow from CSV import to backtest execution.

    This test validates the entire Milestone 2 functionality:
    1. CSV import capability
    2. Database storage
    3. Data retrieval
    4. Backtest execution with real data
    """
    settings = get_settings()

    # Skip if database not configured
    if not settings.database_url:
        pytest.skip("Database not configured")

    # Skip if database not accessible
    if not await db_session.test_connection():
        pytest.skip("Database not accessible")

    # Clean up any existing AAPL data first
    from src.db.session import get_session
    from sqlalchemy import text

    async with get_session() as cleanup_session:
        await cleanup_session.execute(
            text("DELETE FROM market_data WHERE symbol = 'AAPL'")
        )
        await cleanup_session.commit()

    # Create sample CSV data
    csv_content = """timestamp,open,high,low,close,volume
2024-01-02 09:30:00,185.25,186.50,184.75,185.95,2847300
2024-01-02 09:31:00,185.95,186.25,185.50,186.10,1254800
2024-01-02 09:32:00,186.10,186.75,185.80,186.45,982100
2024-01-02 09:33:00,186.45,187.20,186.20,186.80,1134500
2024-01-02 09:34:00,186.80,187.15,186.55,186.95,876200
2024-01-02 09:35:00,186.95,187.40,186.70,187.20,943700
2024-01-02 09:36:00,187.20,187.65,187.00,187.45,1087300
2024-01-02 09:37:00,187.45,187.80,187.25,187.60,754900
2024-01-02 09:38:00,187.60,188.10,187.40,187.85,1298600
2024-01-02 09:39:00,187.85,188.25,187.65,188.00,986400"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        csv_file = Path(f.name)

    try:
        # Step 1: Import CSV data
        from src.services.csv_loader import CSVLoader
        from src.db.session import get_session

        async with get_session() as session:
            loader = CSVLoader(session)
            import_result = await loader.load_file(csv_file, "AAPL")

        # Verify import success
        assert import_result["records_inserted"] == 10
        assert import_result["symbol"] == "AAPL"
        assert import_result["duplicates_skipped"] == 0

        # Step 2: Verify data can be retrieved
        from src.services.data_service import DataService

        data_service = DataService()

        # Check data availability - use exact data range
        validation = await data_service.validate_data_availability(
            "AAPL",
            datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),  # Exact start
            datetime(2024, 1, 2, 9, 39, tzinfo=timezone.utc),  # Exact end
        )
        assert validation["valid"] is True

        # Retrieve market data
        market_data = await data_service.get_market_data(
            "AAPL",
            datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
            datetime(2024, 1, 2, 9, 39, tzinfo=timezone.utc),
        )
        assert len(market_data) == 10
        assert market_data[0]["symbol"] == "AAPL"
        assert market_data[0]["open"] == 185.25

        # Step 3: Run backtest with imported data
        from src.core.backtest_runner import MinimalBacktestRunner

        runner = MinimalBacktestRunner(data_source="database")

        backtest_result = await runner.run_backtest_with_database(
            symbol="AAPL",
            start=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, 9, 39, tzinfo=timezone.utc),
            fast_period=3,  # Short periods for small dataset
            slow_period=5,
        )

        # Verify backtest executed successfully
        assert backtest_result is not None
        assert hasattr(backtest_result, "total_return")
        assert hasattr(backtest_result, "total_trades")
        assert hasattr(backtest_result, "final_balance")

        # Step 4: Verify both modes work (mock vs database)
        # Test mock mode still works
        mock_runner = MinimalBacktestRunner(data_source="mock")
        mock_result = mock_runner.run_sma_backtest()

        assert mock_result is not None
        assert hasattr(mock_result, "total_return")

        # Results should potentially differ (different data sources)
        # This validates that we're actually using different data
        # Note: Results might be the same by coincidence, so we don't assert inequality

        # Step 5: Verify CLI commands exist and are callable
        from src.cli.main import cli

        # Verify data command group exists
        data_group = cli.get_command(None, "data")
        assert data_group is not None

        # Verify import-csv command exists
        import_cmd = data_group.get_command(None, "import-csv")
        assert import_cmd is not None

        # Verify backtest command group exists
        backtest_group = cli.get_command(None, "backtest")
        assert backtest_group is not None

        # Verify backtest run command exists
        run_cmd = backtest_group.get_command(None, "run")
        assert run_cmd is not None

        # Step 6: Clean up
        runner.dispose()
        mock_runner.dispose()

    finally:
        # Clean up temp file
        csv_file.unlink()

        # Clean up test data
        async with get_session() as cleanup_session:
            await cleanup_session.execute(
                text("DELETE FROM market_data WHERE symbol = 'AAPL'")
            )
            await cleanup_session.commit()


@pytest.mark.integration
def test_original_functionality_preserved():
    """
    INTEGRATION: Verify original Milestone 1 functionality still works.

    This ensures backward compatibility is maintained.
    """
    from src.core.backtest_runner import MinimalBacktestRunner
    from src.cli.main import cli

    # Test original mock backtest still works
    runner = MinimalBacktestRunner()  # Default is mock mode
    result = runner.run_sma_backtest()

    assert result is not None
    assert hasattr(result, "total_return")
    assert hasattr(result, "total_trades")
    assert hasattr(result, "win_rate")
    assert hasattr(result, "final_balance")

    # Test run-simple command still exists
    run_simple_cmd = cli.get_command(None, "run-simple")
    assert run_simple_cmd is not None

    runner.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_data_service_functionality():
    """
    INTEGRATION: Test data service capabilities in isolation.
    """
    # Ensure clean state by disposing any existing connections first
    await db_session.dispose_all_connections()

    # Skip if database not accessible with retry logic for resilience
    max_retries = 3
    retry_delay = 0.5

    for attempt in range(max_retries):
        is_connected = await db_session.test_connection()
        if is_connected:
            break

        if attempt < max_retries - 1:
            # Wait before retrying and dispose connections
            import asyncio
            await asyncio.sleep(retry_delay)
            await db_session.dispose_all_connections()
    else:
        # All retries failed
        pytest.skip("Database not accessible after retries")

    from src.services.data_service import DataService

    data_service = DataService()

    # Test getting available symbols (may be empty, that's ok)
    symbols = await data_service.get_available_symbols()
    assert isinstance(symbols, list)

    # Test data range query for non-existent symbol
    range_info = await data_service.get_data_range("NONEXISTENT")
    assert range_info is None

    # Test validation for non-existent data
    validation = await data_service.validate_data_availability(
        "NONEXISTENT", datetime(2024, 1, 1), datetime(2024, 1, 2)
    )
    assert validation["valid"] is False
    assert "reason" in validation

    # Clean up connections after successful test
    await db_session.dispose_all_connections()


@pytest.mark.integration
def test_configuration_backwards_compatibility():
    """
    INTEGRATION: Test that configuration changes don't break existing functionality.
    """
    from src.config import get_settings

    settings = get_settings()

    # Verify all original settings still exist
    assert hasattr(settings, "app_name")
    assert hasattr(settings, "app_version")
    assert hasattr(settings, "fast_ema_period")
    assert hasattr(settings, "slow_ema_period")
    assert hasattr(settings, "trade_size")
    assert hasattr(settings, "default_balance")

    # Verify new database settings exist
    assert hasattr(settings, "database_url")
    assert hasattr(settings, "is_database_available")

    # Test database availability check
    is_available = settings.is_database_available
    assert isinstance(is_available, bool)
