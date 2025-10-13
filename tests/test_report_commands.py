"""Tests for report CLI commands."""

import pytest
from click.testing import CliRunner
from unittest.mock import patch, MagicMock
from decimal import Decimal
from datetime import datetime

from src.cli.commands.report import (
    report,
    summary,
    generate,
    list as list_cmd,
    export_all,
)
from src.models.backtest_result import EnhancedBacktestResult, BacktestMetadata


@pytest.fixture
def mock_result():
    """Create a mock EnhancedBacktestResult for testing."""
    metadata = BacktestMetadata(
        backtest_id="test-123-456",
        timestamp=datetime(2024, 1, 15, 10, 30),
        strategy_name="SMA Crossover",
        strategy_type="sma",
        symbol="AAPL",
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 1, 31),
        parameters={"fast_period": 10, "slow_period": 20},
    )

    return EnhancedBacktestResult(
        metadata=metadata,
        total_return=Decimal("1500.00"),
        total_trades=25,
        winning_trades=15,
        losing_trades=10,
        largest_win=Decimal("350.00"),
        largest_loss=Decimal("-120.00"),
        final_balance=Decimal("11500.00"),
        sharpe_ratio=1.42,
        sortino_ratio=1.68,
        max_drawdown=-0.087,
        calmar_ratio=1.76,
        volatility=0.15,
        profit_factor=1.85,
        realized_pnl=Decimal("1500.00"),
        unrealized_pnl=Decimal("0.00"),
        total_pnl=Decimal("1500.00"),
    )


class TestReportCommandGroup:
    """Test the report command group."""

    def test_report_group_exists(self):
        """Test that report command group exists."""
        runner = CliRunner()
        result = runner.invoke(report, ["--help"])
        assert result.exit_code == 0
        assert "Report generation and viewing commands" in result.output

    def test_report_subcommands_exist(self):
        """Test that all report subcommands are registered."""
        runner = CliRunner()
        result = runner.invoke(report, ["--help"])
        assert result.exit_code == 0
        assert "summary" in result.output
        assert "generate" in result.output
        assert "list" in result.output
        assert "export-all" in result.output


class TestSummaryCommand:
    """Test the report summary command."""

    @patch("src.cli.commands.report.ResultsStore")
    def test_summary_displays_correctly(self, mock_store_class, mock_result):
        """Test that summary displays result correctly."""
        # Setup mock
        mock_store = MagicMock()
        mock_store.get.return_value = mock_result
        mock_store_class.return_value = mock_store

        runner = CliRunner()
        result = runner.invoke(summary, ["test-123-456"])

        assert result.exit_code == 0
        assert "Backtest Performance Summary" in result.output
        assert "SMA Crossover" in result.output
        assert "AAPL" in result.output

    @patch("src.cli.commands.report.ResultsStore")
    def test_summary_result_not_found(self, mock_store_class):
        """Test summary when result ID not found."""
        from src.services.results_store import ResultNotFoundError

        # Setup mock to raise exception
        mock_store = MagicMock()
        mock_store.get.side_effect = ResultNotFoundError("Result not found")
        mock_store_class.return_value = mock_store

        runner = CliRunner()
        result = runner.invoke(summary, ["invalid-id"])

        assert result.exit_code == 0  # Click doesn't exit with error
        assert "Result not found" in result.output


class TestGenerateCommand:
    """Test the report generate command."""

    @patch("src.cli.commands.report.ResultsStore")
    @patch("src.cli.commands.report.TextReportGenerator")
    def test_generate_text_format(
        self, mock_generator_class, mock_store_class, mock_result
    ):
        """Test generating text format report."""
        # Setup mocks
        mock_store = MagicMock()
        mock_store.get.return_value = mock_result
        mock_store_class.return_value = mock_store

        mock_generator = MagicMock()
        mock_generator.generate_performance_report.return_value = "Test Report"
        mock_generator_class.return_value = mock_generator

        runner = CliRunner()
        result = runner.invoke(
            generate, ["--result-id", "test-123-456", "--format", "text"]
        )

        assert result.exit_code == 0
        assert "Generating TEXT report" in result.output
        mock_generator.generate_performance_report.assert_called_once()

    @patch("src.cli.commands.report.ResultsStore")
    @patch("src.cli.commands.report.CSVExporter")
    def test_generate_csv_format(
        self, mock_exporter_class, mock_store_class, mock_result, tmp_path
    ):
        """Test generating CSV format report."""
        # Setup mocks
        mock_store = MagicMock()
        mock_store.get.return_value = mock_result
        mock_store_class.return_value = mock_store

        mock_exporter = MagicMock()
        mock_exporter.export_metrics.return_value = True
        mock_exporter_class.return_value = mock_exporter

        output_file = str(tmp_path / "test_report.csv")

        runner = CliRunner()
        result = runner.invoke(
            generate,
            ["--result-id", "test-123-456", "--format", "csv", "--output", output_file],
        )

        assert result.exit_code == 0
        assert "Generating CSV report" in result.output
        assert "CSV report exported" in result.output

    @patch("src.cli.commands.report.ResultsStore")
    def test_generate_json_format(self, mock_store_class, mock_result, tmp_path):
        """Test generating JSON format report."""
        # Setup mocks
        mock_store = MagicMock()
        mock_store.get.return_value = mock_result
        mock_store_class.return_value = mock_store

        output_file = str(tmp_path / "test_report.json")

        runner = CliRunner()
        result = runner.invoke(
            generate,
            [
                "--result-id",
                "test-123-456",
                "--format",
                "json",
                "--output",
                output_file,
            ],
        )

        assert result.exit_code == 0
        assert "Generating JSON report" in result.output
        assert "JSON report exported" in result.output

    @patch("src.cli.commands.report.ResultsStore")
    def test_generate_result_not_found(self, mock_store_class):
        """Test generate when result ID not found."""
        from src.services.results_store import ResultNotFoundError

        # Setup mock to raise exception
        mock_store = MagicMock()
        mock_store.get.side_effect = ResultNotFoundError("Result not found")
        mock_store_class.return_value = mock_store

        runner = CliRunner()
        result = runner.invoke(
            generate, ["--result-id", "invalid-id", "--format", "text"]
        )

        assert result.exit_code == 0
        assert "Result not found" in result.output


class TestListCommand:
    """Test the report list command."""

    @patch("src.cli.commands.report.ResultsStore")
    def test_list_displays_results(self, mock_store_class):
        """Test that list displays results correctly."""
        # Setup mock with sample results
        mock_store = MagicMock()
        mock_store.list.return_value = [
            {
                "result_id": "test-123",
                "timestamp": "2024-01-15T10:30:00",
                "strategy": "SMA Crossover",
                "symbol": "AAPL",
                "total_return": "1500.00",
                "total_trades": 25,
                "win_rate": 60.0,
                "sharpe_ratio": 1.42,
            },
            {
                "result_id": "test-456",
                "timestamp": "2024-01-14T15:20:00",
                "strategy": "Mean Reversion",
                "symbol": "EUR/USD",
                "total_return": "850.50",
                "total_trades": 18,
                "win_rate": 55.6,
                "sharpe_ratio": 1.15,
            },
        ]
        mock_store.get_storage_info.return_value = {
            "result_count": 2,
            "total_size_mb": 0.5,
            "storage_dir": "/tmp/results",
        }
        mock_store_class.return_value = mock_store

        runner = CliRunner()
        result = runner.invoke(list_cmd, ["--limit", "10"])

        assert result.exit_code == 0
        assert "Backtest Results" in result.output
        assert "SMA" in result.output  # May be truncated to "SMA Crosso..."
        assert "Mean" in result.output  # May be truncated to "Mean Revers..."
        assert "AAPL" in result.output

    @patch("src.cli.commands.report.ResultsStore")
    def test_list_no_results(self, mock_store_class):
        """Test list when no results exist."""
        # Setup mock with empty results
        mock_store = MagicMock()
        mock_store.list.return_value = []
        mock_store_class.return_value = mock_store

        runner = CliRunner()
        result = runner.invoke(list_cmd)

        assert result.exit_code == 0
        assert "No backtest results found" in result.output

    @patch("src.cli.commands.report.ResultsStore")
    def test_list_with_strategy_filter(self, mock_store_class):
        """Test list with strategy filter."""
        # Setup mock
        mock_store = MagicMock()
        mock_store.find_by_strategy.return_value = []
        mock_store_class.return_value = mock_store

        runner = CliRunner()
        result = runner.invoke(list_cmd, ["--strategy", "sma"])

        assert result.exit_code == 0
        mock_store.find_by_strategy.assert_called_once_with("sma")

    @patch("src.cli.commands.report.ResultsStore")
    def test_list_with_symbol_filter(self, mock_store_class):
        """Test list with symbol filter."""
        # Setup mock
        mock_store = MagicMock()
        mock_store.find_by_symbol.return_value = []
        mock_store_class.return_value = mock_store

        runner = CliRunner()
        result = runner.invoke(list_cmd, ["--symbol", "AAPL"])

        assert result.exit_code == 0
        mock_store.find_by_symbol.assert_called_once_with("AAPL")


class TestExportAllCommand:
    """Test the report export-all command."""

    @patch("src.cli.commands.report.ResultsStore")
    @patch("src.cli.commands.report.TextReportGenerator")
    @patch("src.cli.commands.report.CSVExporter")
    def test_export_all_creates_all_formats(
        self, mock_csv_class, mock_text_class, mock_store_class, mock_result, tmp_path
    ):
        """Test that export-all creates all format files."""
        # Setup mocks
        mock_store = MagicMock()
        mock_store.get.return_value = mock_result
        mock_store_class.return_value = mock_store

        mock_text = MagicMock()
        mock_text.generate_performance_report.return_value = "Test Report"
        mock_text_class.return_value = mock_text

        mock_csv = MagicMock()
        mock_csv.export_metrics.return_value = True
        mock_csv_class.return_value = mock_csv

        output_dir = str(tmp_path)

        runner = CliRunner()
        result = runner.invoke(export_all, ["test-123-456", "--output-dir", output_dir])

        assert result.exit_code == 0
        assert "Exporting all formats" in result.output
        assert "Export complete" in result.output
        assert "Text:" in result.output
        assert "CSV:" in result.output
        assert "JSON:" in result.output

    @patch("src.cli.commands.report.ResultsStore")
    def test_export_all_result_not_found(self, mock_store_class):
        """Test export-all when result ID not found."""
        from src.services.results_store import ResultNotFoundError

        # Setup mock to raise exception
        mock_store = MagicMock()
        mock_store.get.side_effect = ResultNotFoundError("Result not found")
        mock_store_class.return_value = mock_store

        runner = CliRunner()
        result = runner.invoke(export_all, ["invalid-id"])

        assert result.exit_code == 0
        assert "Result not found" in result.output


class TestReportCommandIntegration:
    """Integration tests for report commands."""

    @patch("src.cli.commands.report.ResultsStore")
    def test_complete_workflow(self, mock_store_class, mock_result):
        """Test complete workflow from list to export."""
        # Setup mock store
        mock_store = MagicMock()
        mock_store.list.return_value = [
            {
                "result_id": mock_result.result_id,
                "timestamp": "2024-01-15T10:30:00",
                "strategy": "SMA Crossover",
                "symbol": "AAPL",
                "total_return": "1500.00",
                "total_trades": 25,
                "win_rate": 60.0,
                "sharpe_ratio": 1.42,
            }
        ]
        mock_store.get.return_value = mock_result
        mock_store.get_storage_info.return_value = {
            "result_count": 1,
            "total_size_mb": 0.1,
            "storage_dir": "/tmp/results",
        }
        mock_store_class.return_value = mock_store

        runner = CliRunner()

        # 1. List results
        result = runner.invoke(list_cmd)
        assert result.exit_code == 0
        assert "Backtest Results" in result.output

        # 2. View summary
        result = runner.invoke(summary, [mock_result.result_id])
        assert result.exit_code == 0
        assert "Performance Summary" in result.output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
