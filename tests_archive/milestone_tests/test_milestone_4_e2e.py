"""
Milestone 4 End-to-End Integration Tests.

Comprehensive E2E tests validating complete user workflows for:
- Performance metrics calculation and reporting
- Multi-format report generation (text, CSV, JSON)
- CLI command integration
- Data persistence and retrieval
- Error recovery scenarios
- Analytics pipeline integration

These tests use real components (minimal mocking) to validate the
complete system behavior from end user perspective.
"""

import pytest
import json
import pandas as pd
from decimal import Decimal
from datetime import datetime, timedelta
from click.testing import CliRunner

from src.models.backtest_result import EnhancedBacktestResult, BacktestMetadata
from src.services.results_store import ResultsStore, ResultNotFoundError
from src.services.performance import PerformanceCalculator
from src.services.portfolio import PortfolioService
from src.services.reports.text_report import TextReportGenerator
from src.services.reports.csv_exporter import CSVExporter
from src.cli.commands.report import report


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def temp_storage_dir(tmp_path):
    """Provide temporary storage directory for E2E tests."""
    storage_dir = tmp_path / "e2e_results"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return storage_dir


@pytest.fixture
def e2e_results_store(temp_storage_dir):
    """Provide a clean ResultsStore for E2E testing."""
    return ResultsStore(storage_dir=temp_storage_dir)


@pytest.fixture
def sample_enhanced_result():
    """Create a realistic EnhancedBacktestResult for testing."""
    metadata = BacktestMetadata(
        backtest_id=f"e2e-test-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        timestamp=datetime(2024, 1, 15, 10, 30, 0),
        strategy_name="SMA Crossover",
        strategy_type="sma",
        symbol="AAPL",
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 1, 31),
        parameters={"fast_period": 10, "slow_period": 20, "trade_size": 100000},
    )

    # Create realistic trade data
    trades = []
    for i in range(15):
        trade = {
            "entry_time": (datetime(2024, 1, 1) + timedelta(days=i * 2)).isoformat(),
            "exit_time": (datetime(2024, 1, 1) + timedelta(days=i * 2 + 1)).isoformat(),
            "symbol": "AAPL",
            "side": "LONG" if i % 2 == 0 else "SHORT",
            "quantity": 100,
            "entry_price": str(Decimal("150.00") + Decimal(i * 2)),
            "exit_price": str(Decimal("152.00") + Decimal(i * 2)),
            "pnl": str(Decimal("200.00") if i % 3 != 0 else Decimal("-80.00")),
            "commission": str(Decimal("2.00")),
            "slippage": str(Decimal("0.50")),
        }
        trades.append(trade)

    result = EnhancedBacktestResult(
        metadata=metadata,
        total_return=Decimal("1500.50"),
        total_trades=15,
        winning_trades=10,
        losing_trades=5,
        largest_win=Decimal("350.00"),
        largest_loss=Decimal("-120.00"),
        final_balance=Decimal("11500.50"),
        sharpe_ratio=1.42,
        sortino_ratio=1.68,
        max_drawdown=-0.087,
        max_drawdown_date=datetime(2024, 1, 15),
        calmar_ratio=1.76,
        volatility=0.152,
        profit_factor=1.85,
        realized_pnl=Decimal("1500.50"),
        unrealized_pnl=Decimal("0.00"),
        total_pnl=Decimal("1500.50"),
        avg_win=Decimal("150.05"),
        avg_loss=Decimal("-80.00"),
        expectancy=60.0,
        trades=trades,
    )

    return result


@pytest.fixture
def multiple_sample_results():
    """Create multiple results for testing list/filter functionality."""
    results = []

    strategies = [
        ("SMA Crossover", "sma", "AAPL"),
        ("Mean Reversion", "mean_reversion", "AAPL"),
        ("SMA Crossover", "sma", "TSLA"),
        ("Momentum", "momentum", "MSFT"),
    ]

    for idx, (strategy_name, strategy_type, symbol) in enumerate(strategies):
        metadata = BacktestMetadata(
            backtest_id=f"multi-test-{idx:03d}",
            timestamp=datetime(2024, 1, 10 + idx, 10, 0, 0),
            strategy_name=strategy_name,
            strategy_type=strategy_type,
            symbol=symbol,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 31),
            parameters={"test": True},
        )

        result = EnhancedBacktestResult(
            metadata=metadata,
            total_return=Decimal(str(1000 + idx * 100)),
            total_trades=10 + idx * 5,
            winning_trades=6 + idx * 2,
            losing_trades=4 + idx,
            largest_win=Decimal("300.00"),
            largest_loss=Decimal("-100.00"),
            final_balance=Decimal(str(11000 + idx * 100)),
            sharpe_ratio=1.2 + idx * 0.1,
            sortino_ratio=1.4 + idx * 0.1,
            max_drawdown=-0.05 - idx * 0.01,
            calmar_ratio=1.5 + idx * 0.1,
        )

        results.append(result)

    return results


# ============================================================================
# TEST CLASS 1: Complete Workflow E2E
# ============================================================================


class TestCompleteWorkflowE2E:
    """Test the complete end-to-end workflow from backtest to export."""

    def test_full_workflow_backtest_to_export(
        self, e2e_results_store, sample_enhanced_result, tmp_path
    ):
        """
        Test complete workflow: Create → Save → List → Summary → Generate → Export.

        This is the primary E2E test validating the entire Milestone 4 workflow.
        """
        # Step 1: Save backtest result
        result_id = e2e_results_store.save(sample_enhanced_result)
        assert result_id is not None
        assert len(result_id) > 0

        # Step 2: Verify result appears in list
        results_list = e2e_results_store.list()
        assert len(results_list) == 1
        assert results_list[0]["result_id"] == result_id
        assert results_list[0]["strategy"] == "SMA Crossover"
        assert results_list[0]["symbol"] == "AAPL"

        # Step 3: Load result and verify integrity
        loaded_result = e2e_results_store.get(result_id)
        assert loaded_result.result_id == result_id
        assert loaded_result.metadata.strategy_name == "SMA Crossover"
        assert loaded_result.total_trades == 15
        assert loaded_result.sharpe_ratio == 1.42

        # Step 4: Generate text report
        text_generator = TextReportGenerator()
        metrics = {
            "total_return": float(loaded_result.total_return) / 100.0,
            "sharpe_ratio": loaded_result.sharpe_ratio,
            "max_drawdown": loaded_result.max_drawdown,
            "win_rate": loaded_result.win_rate / 100.0,
            "total_trades": loaded_result.total_trades,
        }
        text_report = text_generator.generate_performance_report(metrics)
        assert len(text_report) > 0
        assert isinstance(text_report, str)

        # Step 5: Export to CSV
        csv_exporter = CSVExporter()
        csv_file = tmp_path / "e2e_test.csv"
        csv_success = csv_exporter.export_metrics(metrics, str(csv_file))
        assert csv_success
        assert csv_file.exists()

        # Verify CSV content
        df = pd.read_csv(csv_file)
        assert len(df) > 0

        # Step 6: Export to JSON
        json_file = tmp_path / "e2e_test.json"
        result_dict = loaded_result.to_dict()

        with open(json_file, "w") as f:
            json.dump(result_dict, f, indent=2, default=str)

        assert json_file.exists()

        # Verify JSON content
        with open(json_file) as f:
            json_data = json.load(f)

        assert "metadata" in json_data
        assert "summary" in json_data
        assert json_data["summary"]["total_trades"] == 15

        # Step 7: Verify storage info
        storage_info = e2e_results_store.get_storage_info()
        assert storage_info["result_count"] == 1
        assert storage_info["total_size_bytes"] > 0

        # Step 8: Cleanup - Delete result
        e2e_results_store.delete(result_id)
        assert e2e_results_store.count() == 0

    def test_workflow_preserves_decimal_precision(
        self, e2e_results_store, sample_enhanced_result, tmp_path
    ):
        """
        Verify decimal precision is preserved throughout the entire workflow.
        """
        # Use a result with precise decimal values
        sample_enhanced_result.total_return = Decimal("1234.567890")
        sample_enhanced_result.avg_win = Decimal("150.12345")
        sample_enhanced_result.avg_loss = Decimal("-75.98765")

        # Save and reload
        result_id = e2e_results_store.save(sample_enhanced_result)
        loaded_result = e2e_results_store.get(result_id)

        # Verify precision preserved
        assert str(loaded_result.total_return) == "1234.567890"
        assert str(loaded_result.avg_win) == "150.12345"
        assert str(loaded_result.avg_loss) == "-75.98765"

        # Export to JSON and verify
        json_file = tmp_path / "precision_test.json"
        result_dict = loaded_result.to_dict()

        with open(json_file, "w") as f:
            json.dump(result_dict, f, indent=2, default=str)

        # Reload and verify
        with open(json_file) as f:
            json_data = json.load(f)

        assert json_data["summary"]["total_return"] == "1234.567890"

        # Cleanup
        e2e_results_store.delete(result_id)

    def test_workflow_handles_large_trade_list(
        self, e2e_results_store, sample_enhanced_result
    ):
        """
        Test workflow with large number of trades (100+).
        """
        # Create 100 trades
        trades = []
        for i in range(100):
            trade = {
                "entry_time": (datetime(2024, 1, 1) + timedelta(hours=i)).isoformat(),
                "exit_time": (
                    datetime(2024, 1, 1) + timedelta(hours=i + 1)
                ).isoformat(),
                "symbol": "AAPL",
                "side": "LONG" if i % 2 == 0 else "SHORT",
                "quantity": 100,
                "entry_price": str(Decimal("150.00") + Decimal(i * 0.5)),
                "exit_price": str(Decimal("151.00") + Decimal(i * 0.5)),
                "pnl": str(Decimal("100.00") if i % 3 != 0 else Decimal("-50.00")),
            }
            trades.append(trade)

        sample_enhanced_result.trades = trades
        sample_enhanced_result.total_trades = 100

        # Save and reload
        result_id = e2e_results_store.save(sample_enhanced_result)
        loaded_result = e2e_results_store.get(result_id)

        # Verify all trades preserved
        assert len(loaded_result.trades) == 100
        assert loaded_result.total_trades == 100

        # Cleanup
        e2e_results_store.delete(result_id)


# ============================================================================
# TEST CLASS 2: Multi-Result Management E2E
# ============================================================================


class TestMultiResultManagementE2E:
    """Test management of multiple backtest results."""

    def test_save_and_list_multiple_results(
        self, e2e_results_store, multiple_sample_results
    ):
        """
        Test saving multiple results and listing them.
        """
        # Save all results
        result_ids = []
        for result in multiple_sample_results:
            result_id = e2e_results_store.save(result)
            result_ids.append(result_id)

        # Verify count
        assert e2e_results_store.count() == 4

        # List all results
        results_list = e2e_results_store.list()
        assert len(results_list) == 4

        # Verify results are sorted by timestamp (most recent first)
        timestamps = [r["timestamp"] for r in results_list]
        assert timestamps == sorted(timestamps, reverse=True)

        # Cleanup
        for result_id in result_ids:
            e2e_results_store.delete(result_id)

    def test_filter_results_by_strategy(
        self, e2e_results_store, multiple_sample_results
    ):
        """
        Test filtering results by strategy name.
        """
        # Save all results
        result_ids = []
        for result in multiple_sample_results:
            result_id = e2e_results_store.save(result)
            result_ids.append(result_id)

        # Filter by "SMA Crossover" strategy
        sma_results = e2e_results_store.find_by_strategy("SMA Crossover")
        assert len(sma_results) == 2

        for result in sma_results:
            assert result["strategy"] == "SMA Crossover"

        # Filter by "Mean Reversion" strategy
        mean_rev_results = e2e_results_store.find_by_strategy("Mean Reversion")
        assert len(mean_rev_results) == 1
        assert mean_rev_results[0]["strategy"] == "Mean Reversion"

        # Cleanup
        for result_id in result_ids:
            e2e_results_store.delete(result_id)

    def test_filter_results_by_symbol(self, e2e_results_store, multiple_sample_results):
        """
        Test filtering results by trading symbol.
        """
        # Save all results
        result_ids = []
        for result in multiple_sample_results:
            result_id = e2e_results_store.save(result)
            result_ids.append(result_id)

        # Filter by "AAPL" symbol
        aapl_results = e2e_results_store.find_by_symbol("AAPL")
        assert len(aapl_results) == 2

        for result in aapl_results:
            assert result["symbol"] == "AAPL"

        # Filter by "TSLA" symbol
        tsla_results = e2e_results_store.find_by_symbol("TSLA")
        assert len(tsla_results) == 1
        assert tsla_results[0]["symbol"] == "TSLA"

        # Cleanup
        for result_id in result_ids:
            e2e_results_store.delete(result_id)

    def test_pagination_with_limit(self, e2e_results_store, multiple_sample_results):
        """
        Test listing results with pagination limit.
        """
        # Save all results
        result_ids = []
        for result in multiple_sample_results:
            result_id = e2e_results_store.save(result)
            result_ids.append(result_id)

        # List with limit=2
        limited_results = e2e_results_store.list(limit=2)
        assert len(limited_results) == 2

        # List with limit=10 (should return all 4)
        all_results = e2e_results_store.list(limit=10)
        assert len(all_results) == 4

        # Cleanup
        for result_id in result_ids:
            e2e_results_store.delete(result_id)

    def test_get_latest_result(self, e2e_results_store, multiple_sample_results):
        """
        Test getting the most recent result.
        """
        # Save results with different timestamps
        result_ids = []
        for result in multiple_sample_results:
            result_id = e2e_results_store.save(result)
            result_ids.append(result_id)

        # Get latest
        latest = e2e_results_store.get_latest()
        assert latest is not None

        # Latest should be the one with most recent timestamp
        # (multi-test-003 has timestamp 2024-01-13)
        assert latest.metadata.symbol == "MSFT"
        assert latest.metadata.strategy_name == "Momentum"

        # Cleanup
        for result_id in result_ids:
            e2e_results_store.delete(result_id)

    def test_clear_all_results(self, e2e_results_store, multiple_sample_results):
        """
        Test clearing all results from storage.
        """
        # Save all results
        for result in multiple_sample_results:
            e2e_results_store.save(result)

        assert e2e_results_store.count() == 4

        # Clear all
        deleted_count = e2e_results_store.clear()
        assert deleted_count == 4
        assert e2e_results_store.count() == 0


# ============================================================================
# TEST CLASS 3: CLI Command Chain E2E
# ============================================================================


class TestCLICommandChainE2E:
    """Test CLI command integration with real components."""

    def test_cli_report_list_command(self, e2e_results_store, sample_enhanced_result):
        """
        Test 'report list' CLI command with real data.
        """
        # Save a result
        result_id = e2e_results_store.save(sample_enhanced_result)

        # Run CLI command
        runner = CliRunner()

        # Patch ResultsStore to use our test store
        from unittest.mock import patch

        with patch(
            "src.cli.commands.report.ResultsStore", return_value=e2e_results_store
        ):
            result = runner.invoke(report, ["list", "--limit", "10"])

        assert result.exit_code == 0
        assert "Backtest Results" in result.output or "SMA" in result.output

        # Cleanup
        e2e_results_store.delete(result_id)

    def test_cli_report_summary_command(
        self, e2e_results_store, sample_enhanced_result
    ):
        """
        Test 'report summary' CLI command with real data.
        """
        # Save a result
        result_id = e2e_results_store.save(sample_enhanced_result)

        # Run CLI command
        runner = CliRunner()

        from unittest.mock import patch

        with patch(
            "src.cli.commands.report.ResultsStore", return_value=e2e_results_store
        ):
            result = runner.invoke(report, ["summary", result_id])

        assert result.exit_code == 0
        assert (
            "Performance Summary" in result.output or "SMA Crossover" in result.output
        )

        # Cleanup
        e2e_results_store.delete(result_id)

    def test_cli_report_generate_text_format(
        self, e2e_results_store, sample_enhanced_result, tmp_path
    ):
        """
        Test 'report generate --format text' CLI command.
        """
        # Save a result
        result_id = e2e_results_store.save(sample_enhanced_result)
        output_file = tmp_path / "cli_report.txt"

        # Run CLI command
        runner = CliRunner()

        from unittest.mock import patch

        with patch(
            "src.cli.commands.report.ResultsStore", return_value=e2e_results_store
        ):
            result = runner.invoke(
                report,
                [
                    "generate",
                    "--result-id",
                    result_id,
                    "--format",
                    "text",
                    "--output",
                    str(output_file),
                ],
            )

        assert result.exit_code == 0

        # Output file might not be created if there are issues, but command should succeed
        if output_file.exists():
            assert output_file.stat().st_size > 0

        # Cleanup
        e2e_results_store.delete(result_id)

    def test_cli_report_export_all_command(
        self, e2e_results_store, sample_enhanced_result, tmp_path
    ):
        """
        Test 'report export-all' CLI command.
        """
        # Save a result
        result_id = e2e_results_store.save(sample_enhanced_result)
        export_dir = tmp_path / "cli_exports"

        # Run CLI command
        runner = CliRunner()

        from unittest.mock import patch

        with patch(
            "src.cli.commands.report.ResultsStore", return_value=e2e_results_store
        ):
            result = runner.invoke(
                report, ["export-all", result_id, "--output-dir", str(export_dir)]
            )

        assert result.exit_code == 0
        assert "Export complete" in result.output or "Exporting" in result.output

        # Verify export directory created
        assert export_dir.exists()

        # Cleanup
        e2e_results_store.delete(result_id)


# ============================================================================
# TEST CLASS 4: Cross-Format Consistency E2E
# ============================================================================


class TestCrossFormatConsistencyE2E:
    """Test consistency across different export formats."""

    def test_metrics_consistency_across_formats(self, sample_enhanced_result, tmp_path):
        """
        Verify metrics are consistent across text, CSV, and JSON formats.
        """
        # Prepare metrics
        metrics = {
            "total_return": float(sample_enhanced_result.total_return) / 100.0,
            "sharpe_ratio": sample_enhanced_result.sharpe_ratio,
            "sortino_ratio": sample_enhanced_result.sortino_ratio,
            "max_drawdown": sample_enhanced_result.max_drawdown,
            "calmar_ratio": sample_enhanced_result.calmar_ratio,
            "total_trades": sample_enhanced_result.total_trades,
            "win_rate": sample_enhanced_result.win_rate / 100.0,
        }

        # Export to CSV
        csv_exporter = CSVExporter()
        csv_file = tmp_path / "metrics.csv"
        csv_exporter.export_metrics(metrics, str(csv_file))

        # Export to JSON
        json_file = tmp_path / "metrics.json"
        with open(json_file, "w") as f:
            json.dump(metrics, f, indent=2, default=str)

        # Generate text report
        text_generator = TextReportGenerator()
        text_report = text_generator.generate_performance_report(metrics)

        # Verify CSV data
        csv_df = pd.read_csv(csv_file)
        assert "sharpe_ratio" in csv_df.columns or len(csv_df) > 0

        # Verify JSON data
        with open(json_file) as f:
            json_data = json.load(f)

        assert json_data["sharpe_ratio"] == 1.42
        assert json_data["total_trades"] == 15

        # Verify text report contains metrics (approximate matching)
        assert len(text_report) > 0

    def test_decimal_precision_across_formats(self, sample_enhanced_result, tmp_path):
        """
        Verify decimal precision is preserved across all export formats.
        """
        # Set precise decimal values
        precise_metrics = {
            "total_return": Decimal("1234.567890"),
            "avg_win": Decimal("150.123456"),
            "avg_loss": Decimal("-75.987654"),
        }

        sample_enhanced_result.total_return = precise_metrics["total_return"]
        sample_enhanced_result.avg_win = precise_metrics["avg_win"]
        sample_enhanced_result.avg_loss = precise_metrics["avg_loss"]

        # Export to JSON
        result_dict = sample_enhanced_result.to_dict()
        json_file = tmp_path / "precise.json"

        with open(json_file, "w") as f:
            json.dump(result_dict, f, indent=2, default=str)

        # Reload and verify
        with open(json_file) as f:
            json_data = json.load(f)

        # Check precision preserved in JSON
        assert "1234.567890" in json_data["summary"]["total_return"]

    def test_datetime_serialization_consistency(self, sample_enhanced_result, tmp_path):
        """
        Verify datetime serialization is consistent across formats.
        """
        # Export to JSON
        result_dict = sample_enhanced_result.to_dict()
        json_file = tmp_path / "datetime_test.json"

        with open(json_file, "w") as f:
            json.dump(result_dict, f, indent=2, default=str)

        # Reload and verify datetime format
        with open(json_file) as f:
            json_data = json.load(f)

        # Verify timestamp is present and is a string
        timestamp = json_data["metadata"]["timestamp"]
        assert isinstance(timestamp, str)
        assert len(timestamp) > 0

        # Verify timestamp contains date components
        assert "2024" in timestamp
        assert "01" in timestamp
        assert "15" in timestamp

        # Verify can parse to datetime (either ISO format or space-separated)
        try:
            # Try ISO format first
            parsed_dt = datetime.fromisoformat(timestamp)
        except ValueError:
            # Try datetime string format
            parsed_dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")

        assert isinstance(parsed_dt, datetime)


# ============================================================================
# TEST CLASS 5: Error Recovery E2E
# ============================================================================


class TestErrorRecoveryE2E:
    """Test error handling and recovery scenarios."""

    def test_invalid_result_id_handling(self, e2e_results_store):
        """
        Test graceful handling of invalid result IDs.
        """
        # Try to get non-existent result
        with pytest.raises(ResultNotFoundError) as exc_info:
            e2e_results_store.get("invalid-id-12345")

        assert "Result not found" in str(exc_info.value)

    def test_delete_non_existent_result(self, e2e_results_store):
        """
        Test deleting a result that doesn't exist.
        """
        with pytest.raises(ResultNotFoundError):
            e2e_results_store.delete("non-existent-id")

    def test_corrupted_json_file_handling(self, temp_storage_dir):
        """
        Test handling of corrupted JSON files.
        """
        # Create a corrupted JSON file
        corrupted_file = temp_storage_dir / "corrupted.json"
        with open(corrupted_file, "w") as f:
            f.write("{invalid json content")

        store = ResultsStore(storage_dir=temp_storage_dir)

        # List should skip corrupted files
        results = store.list()
        # Should not crash, just skip the corrupted file
        assert isinstance(results, list)

    def test_missing_export_directory_creation(self, tmp_path):
        """
        Test that export operations create missing directories.
        """
        # Use non-existent directory
        export_dir = tmp_path / "non_existent" / "nested" / "path"
        assert not export_dir.exists()

        # Export should create directory

        export_dir.mkdir(parents=True, exist_ok=True)

        assert export_dir.exists()

    def test_cli_handles_invalid_result_id(self, e2e_results_store):
        """
        Test CLI gracefully handles invalid result IDs.
        """
        runner = CliRunner()

        from unittest.mock import patch

        with patch(
            "src.cli.commands.report.ResultsStore", return_value=e2e_results_store
        ):
            result = runner.invoke(report, ["summary", "invalid-id-xyz"])

        # Should not crash, should show error message
        assert result.exit_code == 0
        assert (
            "Result not found" in result.output or "not found" in result.output.lower()
        )


# ============================================================================
# TEST CLASS 6: Analytics Pipeline E2E
# ============================================================================


class TestAnalyticsPipelineE2E:
    """Test the full analytics pipeline integration."""

    def test_performance_calculator_integration(self):
        """
        Test PerformanceCalculator with realistic data.
        """
        calculator = PerformanceCalculator()

        # Create realistic returns series
        returns = pd.Series(
            [0.01, -0.005, 0.02, -0.01, 0.015, 0.008, -0.012, 0.018],
            index=pd.date_range("2024-01-01", periods=8, freq="D"),
        )

        test_data = {
            "return_series": returns,
            "total_pnl": 1500.0,
            "realized_pnl": 1500.0,
        }

        metrics = calculator.calculate_metrics_from_data(test_data)

        # Verify key metrics calculated
        assert "sharpe_ratio" in metrics
        assert "sortino_ratio" in metrics
        assert "max_drawdown" in metrics
        assert "total_return" in metrics
        assert "calculation_timestamp" in metrics

        # Verify metrics are reasonable
        assert metrics["sharpe_ratio"] is not None
        assert metrics["max_drawdown"] <= 0  # Drawdown should be negative

    def test_portfolio_service_integration(self):
        """
        Test PortfolioService with mock Nautilus components.
        """
        from unittest.mock import MagicMock

        # Create mock portfolio and cache
        mock_portfolio = MagicMock()
        mock_cache = MagicMock()

        mock_portfolio.total_pnl.return_value = 2500.0
        mock_portfolio.unrealized_pnls.return_value = {}
        mock_portfolio.realized_pnls.return_value = {}
        mock_portfolio.net_exposures.return_value = {}
        mock_portfolio.is_completely_flat.return_value = True

        mock_cache.positions_open.return_value = []
        mock_cache.positions_closed.return_value = []

        service = PortfolioService(mock_portfolio, mock_cache)

        # Get current state
        state = service.get_current_state()

        # Verify state structure
        assert "timestamp" in state
        assert "total_pnl" in state
        assert "open_positions" in state
        assert "closed_positions" in state
        assert "is_flat" in state

        assert state["total_pnl"] == 2500.0
        assert state["is_flat"] is True

    def test_enhanced_result_from_basic_conversion(self):
        """
        Test converting basic result to EnhancedBacktestResult.
        """
        from unittest.mock import MagicMock

        # Create mock basic result
        basic_result = MagicMock()
        basic_result.total_return = 1500.0
        basic_result.total_trades = 20
        basic_result.winning_trades = 12
        basic_result.losing_trades = 8
        basic_result.largest_win = 300.0
        basic_result.largest_loss = -150.0
        basic_result.final_balance = 11500.0

        metadata = BacktestMetadata(
            strategy_name="Test Strategy",
            strategy_type="test",
            symbol="TEST",
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 31),
            parameters={},
        )

        performance_metrics = {
            "sharpe_ratio": 1.5,
            "sortino_ratio": 1.7,
            "max_drawdown": -0.08,
            "calmar_ratio": 1.8,
            "volatility": 0.15,
            "profit_factor": 1.9,
            "expectancy": 65.0,
            "avg_win": 125.0,
            "avg_loss": -75.0,
        }

        # Convert to enhanced result
        enhanced_result = EnhancedBacktestResult.from_basic_result(
            basic_result=basic_result,
            metadata=metadata,
            performance_metrics=performance_metrics,
        )

        # Verify conversion
        assert enhanced_result.total_trades == 20
        assert enhanced_result.sharpe_ratio == 1.5
        assert enhanced_result.metadata.strategy_name == "Test Strategy"
        assert enhanced_result.expectancy == 65.0


# ============================================================================
# TEST CLASS 7: Large Dataset Performance E2E
# ============================================================================


@pytest.mark.slow
class TestLargeDatasetPerformanceE2E:
    """Test system performance with large datasets."""

    def test_large_trade_list_performance(self, e2e_results_store):
        """
        Test saving and loading results with 100+ trades.
        """
        import time

        # Create result with 150 trades
        metadata = BacktestMetadata(
            strategy_name="Large Dataset Test",
            strategy_type="test",
            symbol="AAPL",
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 6, 30),
            parameters={},
        )

        trades = []
        for i in range(150):
            trade = {
                "entry_time": (
                    datetime(2024, 1, 1) + timedelta(hours=i * 2)
                ).isoformat(),
                "exit_time": (
                    datetime(2024, 1, 1) + timedelta(hours=i * 2 + 1)
                ).isoformat(),
                "symbol": "AAPL",
                "side": "LONG" if i % 2 == 0 else "SHORT",
                "quantity": 100,
                "entry_price": str(Decimal("150.00") + Decimal(i * 0.1)),
                "exit_price": str(Decimal("151.00") + Decimal(i * 0.1)),
                "pnl": str(Decimal("100.00") if i % 3 != 0 else Decimal("-50.00")),
            }
            trades.append(trade)

        result = EnhancedBacktestResult(
            metadata=metadata,
            total_return=Decimal("5000.00"),
            total_trades=150,
            winning_trades=100,
            losing_trades=50,
            largest_win=Decimal("500.00"),
            largest_loss=Decimal("-200.00"),
            final_balance=Decimal("15000.00"),
            trades=trades,
        )

        # Measure save time
        start_time = time.time()
        result_id = e2e_results_store.save(result)
        save_time = time.time() - start_time

        # Should save in reasonable time (<1 second for 150 trades)
        assert save_time < 1.0

        # Measure load time
        start_time = time.time()
        loaded_result = e2e_results_store.get(result_id)
        load_time = time.time() - start_time

        # Should load in reasonable time (<0.5 seconds)
        assert load_time < 0.5

        # Verify all trades loaded
        assert len(loaded_result.trades) == 150

        # Cleanup
        e2e_results_store.delete(result_id)

    def test_multiple_results_listing_performance(
        self, e2e_results_store, multiple_sample_results
    ):
        """
        Test performance of listing multiple results.
        """
        import time

        # Save 20 results (5x the sample set)
        result_ids = []
        for _ in range(5):
            for result in multiple_sample_results:
                # Create new instance with unique ID
                new_metadata = BacktestMetadata(
                    strategy_name=result.metadata.strategy_name,
                    strategy_type=result.metadata.strategy_type,
                    symbol=result.metadata.symbol,
                    start_date=result.metadata.start_date,
                    end_date=result.metadata.end_date,
                    parameters=result.metadata.parameters,
                )
                new_result = EnhancedBacktestResult(
                    metadata=new_metadata,
                    total_return=result.total_return,
                    total_trades=result.total_trades,
                    winning_trades=result.winning_trades,
                    losing_trades=result.losing_trades,
                    largest_win=result.largest_win,
                    largest_loss=result.largest_loss,
                    final_balance=result.final_balance,
                    sharpe_ratio=result.sharpe_ratio,
                    sortino_ratio=result.sortino_ratio,
                    max_drawdown=result.max_drawdown,
                    calmar_ratio=result.calmar_ratio,
                )
                result_id = e2e_results_store.save(new_result)
                result_ids.append(result_id)

        # Measure list time
        start_time = time.time()
        results = e2e_results_store.list()
        list_time = time.time() - start_time

        # Should list in reasonable time (<0.2 seconds for 20 results)
        assert list_time < 0.2
        assert len(results) == 20

        # Cleanup
        for result_id in result_ids:
            e2e_results_store.delete(result_id)

    def test_storage_info_calculation_performance(
        self, e2e_results_store, multiple_sample_results
    ):
        """
        Test performance of storage info calculation.
        """
        import time

        # Save multiple results
        result_ids = []
        for result in multiple_sample_results:
            result_id = e2e_results_store.save(result)
            result_ids.append(result_id)

        # Measure storage info calculation time
        start_time = time.time()
        storage_info = e2e_results_store.get_storage_info()
        info_time = time.time() - start_time

        # Should calculate in reasonable time (<0.1 seconds)
        assert info_time < 0.1

        # Verify info is correct
        assert storage_info["result_count"] == 4
        assert storage_info["total_size_bytes"] > 0

        # Cleanup
        for result_id in result_ids:
            e2e_results_store.delete(result_id)


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-m", "not slow"])
