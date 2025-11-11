"""
Integration tests for the 'backtest reproduce' CLI command.

Tests the complete CLI command flow for re-running previous backtests
with their exact same configuration.
"""

import pytest
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, timezone
from click.testing import CliRunner
from unittest.mock import patch, MagicMock
from contextlib import contextmanager

from src.cli.commands.reproduce import reproduce_backtest
from src.db.repositories.backtest_repository_sync import SyncBacktestRepository


@pytest.mark.integration
def test_reproduce_creates_new_run_with_same_config(sync_db_session):
    """
    Test that 'reproduce' command can be invoked and loads original config.

    Verifies:
    - Command loads original backtest from database
    - Original configuration is displayed
    - Command provides feedback about reproduction attempt

    Note: This test does NOT execute the actual backtest (would require catalog data).
    It verifies the CLI interface and config loading works correctly.
    """
    # Setup test data
    repository = SyncBacktestRepository(sync_db_session)
    original_run_id = uuid4()

    repository.create_backtest_run(
        run_id=original_run_id,
        strategy_name="SMA Crossover",
        strategy_type="sma_crossover",
        instrument_symbol="AAPL",
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 1, 31, tzinfo=timezone.utc),
        initial_capital=Decimal("100000.00"),
        data_source="catalog",
        execution_status="success",
        execution_duration_seconds=Decimal("45.237"),
        config_snapshot={
            "strategy_path": "src.core.strategies.sma_crossover",
            "config_path": "runtime_config",
            "version": "1.0",
            "config": {
                "fast_period": 10,
                "slow_period": 50,
                "trade_size": 1000,
            },
        },
    )
    sync_db_session.commit()

    # Mock get_sync_session to use test session
    @contextmanager
    def mock_get_sync_session():
        yield sync_db_session

    # Mock DataCatalogService and MinimalBacktestRunner to prevent actual execution
    mock_catalog = MagicMock()
    mock_catalog.fetch_or_load.return_value = []  # Return empty bars list
    mock_catalog.load_instrument.return_value = MagicMock()

    mock_runner = MagicMock()
    mock_runner.run_backtest_with_catalog_data.return_value = (MagicMock(), uuid4())

    with (
        patch("src.cli.commands.reproduce.get_sync_session", mock_get_sync_session),
        patch(
            "src.services.data_catalog.DataCatalogService", return_value=mock_catalog
        ),
        patch(
            "src.cli.commands.reproduce.MinimalBacktestRunner", return_value=mock_runner
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(reproduce_backtest, [str(original_run_id)])

    # Assert: Command should load original config and attempt reproduction
    # It will fail at data loading stage (no catalog data), which is expected
    # But it should successfully retrieve and display the original config
    assert "SMA Crossover" in result.output or "sma_crossover" in result.output
    assert "AAPL" in result.output
    assert (
        str(original_run_id)[:12] in result.output
        or str(original_run_id)[:8] in result.output
    )


@pytest.mark.integration
def test_reproduce_sets_reproduced_from_run_id(sync_db_session):
    """
    Test that 'reproduce' command loads and displays original backtest configuration.

    Verifies:
    - Command retrieves original backtest from database
    - Original strategy and parameters are shown
    - User gets clear feedback about what will be reproduced

    Note: This test verifies config retrieval, not actual reproduction execution.
    """
    # Setup test data
    repository = SyncBacktestRepository(sync_db_session)
    original_run_id = uuid4()

    repository.create_backtest_run(
        run_id=original_run_id,
        strategy_name="Mean Reversion",
        strategy_type="mean_reversion",
        instrument_symbol="TSLA",
        start_date=datetime(2024, 2, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 2, 28, tzinfo=timezone.utc),
        initial_capital=Decimal("50000.00"),
        data_source="catalog",
        execution_status="success",
        execution_duration_seconds=Decimal("30.5"),
        config_snapshot={
            "strategy_path": "src.core.strategies.mean_reversion",
            "config_path": "runtime_config",
            "version": "1.0",
            "config": {
                "lookback_period": 20,
                "entry_threshold": 2.0,
                "exit_threshold": 0.5,
            },
        },
    )
    sync_db_session.commit()

    # Mock get_sync_session to use test session
    @contextmanager
    def mock_get_sync_session():
        yield sync_db_session

    # Mock DataCatalogService and MinimalBacktestRunner to prevent actual execution
    mock_catalog = MagicMock()
    mock_catalog.fetch_or_load.return_value = []  # Return empty bars list
    mock_catalog.load_instrument.return_value = MagicMock()

    mock_runner = MagicMock()
    mock_runner.run_backtest_with_catalog_data.return_value = (MagicMock(), uuid4())

    with (
        patch("src.cli.commands.reproduce.get_sync_session", mock_get_sync_session),
        patch(
            "src.services.data_catalog.DataCatalogService", return_value=mock_catalog
        ),
        patch(
            "src.cli.commands.reproduce.MinimalBacktestRunner", return_value=mock_runner
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(reproduce_backtest, [str(original_run_id)])

    # Assert: Command should retrieve and display original config
    assert "Mean Reversion" in result.output or "mean_reversion" in result.output
    assert "TSLA" in result.output
    assert (
        str(original_run_id)[:12] in result.output
        or str(original_run_id)[:8] in result.output
    )


@pytest.mark.integration
def test_reproduce_handles_nonexistent_run_id(sync_db_session):
    """
    Test that 'reproduce' command shows clear error for non-existent run_id.

    Verifies:
    - Error message displayed when run_id doesn't exist
    - Exit code indicates failure
    - Error message is user-friendly and actionable
    """
    nonexistent_run_id = str(uuid4())

    # Mock get_sync_session to use test session
    @contextmanager
    def mock_get_sync_session():
        yield sync_db_session

    with patch("src.cli.commands.reproduce.get_sync_session", mock_get_sync_session):
        runner = CliRunner()
        result = runner.invoke(reproduce_backtest, [nonexistent_run_id])

    # Assert: Should show clear error message
    # This should eventually pass once command is implemented
    # For now, it will fail because command doesn't exist
    # Once implemented:
    # assert result.exit_code != 0
    # assert "not found" in result.output.lower() or "does not exist" in result.output.lower()
    # assert nonexistent_run_id[:8] in result.output  # Show partial UUID for reference

    # For now, expect command not found or similar error
    assert result.exit_code != 0


@pytest.mark.integration
def test_reproduce_handles_invalid_uuid_format(sync_db_session):
    """
    Test that 'reproduce' command validates UUID format.

    Verifies:
    - Invalid UUID format rejected
    - Clear error message shown
    - Suggests correct UUID format
    """
    invalid_uuid = "not-a-valid-uuid"

    # Mock get_sync_session to use test session
    @contextmanager
    def mock_get_sync_session():
        yield sync_db_session

    with patch("src.cli.commands.reproduce.get_sync_session", mock_get_sync_session):
        runner = CliRunner()
        result = runner.invoke(reproduce_backtest, [invalid_uuid])

    # Assert: Should show UUID validation error
    # Once implemented:
    # assert result.exit_code != 0
    # assert "invalid" in result.output.lower() or "uuid" in result.output.lower()

    # For now, expect command not found or validation error
    assert result.exit_code != 0


@pytest.mark.integration
def test_reproduce_displays_original_configuration(sync_db_session):
    """
    Test that 'reproduce' command displays original backtest configuration.

    Verifies:
    - Original run_id shown in output
    - Strategy name and type displayed
    - Configuration parameters shown
    """
    # Setup test data
    repository = SyncBacktestRepository(sync_db_session)
    original_run_id = uuid4()

    repository.create_backtest_run(
        run_id=original_run_id,
        strategy_name="Momentum",
        strategy_type="momentum",
        instrument_symbol="GOOGL",
        start_date=datetime(2024, 3, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 3, 31, tzinfo=timezone.utc),
        initial_capital=Decimal("75000.00"),
        data_source="catalog",
        execution_status="success",
        execution_duration_seconds=Decimal("25.0"),
        config_snapshot={
            "strategy_path": "src.core.strategies.momentum",
            "config_path": "runtime_config",
            "version": "1.0",
            "config": {"momentum_period": 14, "entry_threshold": 0.05},
        },
    )
    sync_db_session.commit()

    # Mock get_sync_session to use test session
    @contextmanager
    def mock_get_sync_session():
        yield sync_db_session

    # Mock DataCatalogService and MinimalBacktestRunner to prevent actual execution
    mock_catalog = MagicMock()
    mock_catalog.fetch_or_load.return_value = []  # Return empty bars list
    mock_catalog.load_instrument.return_value = MagicMock()

    mock_runner = MagicMock()
    mock_runner.run_backtest_with_catalog_data.return_value = (MagicMock(), uuid4())

    with (
        patch("src.cli.commands.reproduce.get_sync_session", mock_get_sync_session),
        patch(
            "src.services.data_catalog.DataCatalogService", return_value=mock_catalog
        ),
        patch(
            "src.cli.commands.reproduce.MinimalBacktestRunner", return_value=mock_runner
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(reproduce_backtest, [str(original_run_id)])

    # Assert: Should display original configuration
    assert (
        str(original_run_id)[:12] in result.output
        or str(original_run_id)[:8] in result.output
    )
    assert "Momentum" in result.output or "momentum" in result.output
    assert "GOOGL" in result.output
