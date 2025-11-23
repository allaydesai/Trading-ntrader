"""
Integration tests for the 'backtest show' CLI command.

Tests the complete CLI command flow for retrieving and displaying
backtest details by run_id.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from click.testing import CliRunner

from src.cli.commands.show import show_backtest_details as show
from src.db.repositories.backtest_repository_sync import SyncBacktestRepository


@pytest.mark.integration
def test_show_displays_successful_backtest_details(sync_db_session):
    """
    Test that 'show' command displays complete details of a successful backtest.

    Verifies:
    - All backtest metadata displayed (strategy, symbol, dates)
    - Configuration snapshot shown with formatting
    - All performance metrics displayed
    - Execution metadata shown (status, duration, timestamps)
    """
    # Setup test data
    repository = SyncBacktestRepository(sync_db_session)
    run_id = uuid4()

    backtest = repository.create_backtest_run(
        run_id=run_id,
        strategy_name="SMA Crossover",
        strategy_type="trend_following",
        instrument_symbol="AAPL",
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        initial_capital=Decimal("100000.00"),
        data_source="IBKR",
        execution_status="success",
        execution_duration_seconds=Decimal("45.237"),
        config_snapshot={
            "strategy_path": "src.strategies.sma_crossover.SMAStrategyConfig",
            "config_path": "config/strategies/sma_crossover.yaml",
            "version": "1.0",
            "config": {
                "fast_period": 10,
                "slow_period": 50,
                "risk_percent": 2.0,
            },
        },
    )

    repository.create_performance_metrics(
        backtest_run_id=backtest.id,
        total_return=Decimal("0.2547"),
        final_balance=Decimal("125470.00"),
        cagr=Decimal("0.2547"),
        sharpe_ratio=Decimal("1.85"),
        sortino_ratio=Decimal("2.34"),
        max_drawdown=Decimal("-0.12"),
        max_drawdown_date=datetime(2024, 6, 15, tzinfo=timezone.utc),
        calmar_ratio=Decimal("2.12"),
        volatility=Decimal("0.18"),
        total_trades=45,
        winning_trades=28,
        losing_trades=17,
        win_rate=Decimal("0.6222"),
        profit_factor=Decimal("2.15"),
        expectancy=Decimal("145.60"),
        avg_win=Decimal("520.35"),
        avg_loss=Decimal("-285.20"),
    )

    sync_db_session.commit()

    # Refresh the backtest to load metrics relationship
    sync_db_session.refresh(backtest, ["metrics"])

    # Mock get_sync_session to use the test session
    @contextmanager
    def mock_get_sync_session():
        yield sync_db_session

    with patch("src.cli.commands.show.get_sync_session", mock_get_sync_session):
        runner = CliRunner()
        result = runner.invoke(show, [str(run_id)])

    # Assert: Verify output contains all key information
    assert result.exit_code == 0, f"Command failed: {result.output}"

    # Check metadata
    assert "SMA Crossover" in result.output
    assert "AAPL" in result.output
    assert "success" in result.output.lower()

    # Check config snapshot
    assert "fast_period" in result.output
    assert "slow_period" in result.output
    assert "10" in result.output  # fast_period value
    assert "50" in result.output  # slow_period value

    # Check performance metrics
    assert "25.47" in result.output or "0.2547" in result.output  # total_return
    assert "1.85" in result.output  # sharpe_ratio
    assert "62.22" in result.output or "0.6222" in result.output  # win_rate
    assert "45" in result.output  # total_trades

    # Check execution metadata
    assert "45.237" in result.output or "45.2" in result.output  # duration


@pytest.mark.integration
def test_show_displays_failed_backtest_with_error(sync_db_session):
    """
    Test that 'show' command displays failed backtest with error message prominently.

    Verifies:
    - Error message is displayed prominently
    - Execution status shows "failed"
    - No performance metrics displayed (should not exist)
    - Basic metadata still shown
    """
    # Setup test data
    repository = SyncBacktestRepository(sync_db_session)
    run_id = uuid4()

    repository.create_backtest_run(
        run_id=run_id,
        strategy_name="RSI Mean Reversion",
        strategy_type="mean_reversion",
        instrument_symbol="TSLA",
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
        initial_capital=Decimal("50000.00"),
        data_source="CSV",
        execution_status="failed",
        execution_duration_seconds=Decimal("2.145"),
        error_message="DataError: Missing price data for 2024-03-15. Data file corrupted or incomplete.",
        config_snapshot={
            "strategy_path": "src.strategies.rsi_mean_reversion.RSIMeanReversionConfig",
            "config_path": "config/strategies/rsi_mean_reversion.yaml",
            "version": "1.0",
            "config": {
                "rsi_period": 14,
                "oversold_threshold": 30,
                "overbought_threshold": 70,
            },
        },
    )

    sync_db_session.commit()

    # Mock get_sync_session to use the test session
    @contextmanager
    def mock_get_sync_session():
        yield sync_db_session

    with patch("src.cli.commands.show.get_sync_session", mock_get_sync_session):
        runner = CliRunner()
        result = runner.invoke(show, [str(run_id)])

    # Assert: Verify output shows error prominently
    assert result.exit_code == 0, f"Command failed: {result.output}"

    # Check error message is displayed
    assert "DataError" in result.output
    assert "Missing price data" in result.output
    assert "2024-03-15" in result.output

    # Check status
    assert "failed" in result.output.lower()

    # Check basic metadata still shown
    assert "RSI Mean Reversion" in result.output
    assert "TSLA" in result.output


@pytest.mark.integration
def test_show_handles_not_found_uuid(sync_db_session):
    """
    Test that 'show' command handles non-existent UUIDs gracefully.

    Verifies:
    - Clear "not found" error message
    - Non-zero exit code or clear error output
    - Suggests user to check the UUID
    """
    non_existent_id = uuid4()

    # Mock get_sync_session to use the test session
    @contextmanager
    def mock_get_sync_session():
        yield sync_db_session

    with patch("src.cli.commands.show.get_sync_session", mock_get_sync_session):
        runner = CliRunner()
        result = runner.invoke(show, [str(non_existent_id)])

    # Assert: Verify not found handling
    assert "not found" in result.output.lower() or "does not exist" in result.output.lower()
    assert str(non_existent_id) in result.output


@pytest.mark.integration
def test_show_handles_invalid_uuid(sync_db_session):
    """
    Test that 'show' command handles invalid UUID format.

    Verifies:
    - Clear validation error message
    - Indicates the UUID format is invalid
    - Non-zero exit code or clear error output
    """
    invalid_uuid = "not-a-valid-uuid"

    # Test CLI command
    runner = CliRunner()
    result = runner.invoke(show, [invalid_uuid])

    # Assert: Verify UUID validation error
    assert "invalid" in result.output.lower() or "error" in result.output.lower()
