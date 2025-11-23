"""Tests for text report generation with Rich formatting."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.services.reports.text_report import TextReportGenerator


class TestTextReportGenerator:
    """Test suite for TextReportGenerator with Rich formatting."""

    @pytest.fixture
    def generator(self):
        """Create TextReportGenerator instance."""
        return TextReportGenerator()

    @pytest.fixture
    def sample_metrics(self):
        """Provide sample performance metrics for testing."""
        return {
            "sharpe_ratio": 1.42,
            "sortino_ratio": 1.68,
            "total_return": 0.153,
            "cagr": 0.125,
            "annualized_return": 0.125,
            "volatility": 0.18,
            "max_drawdown": -0.087,
            "max_drawdown_date": datetime(2024, 6, 15, tzinfo=timezone.utc),
            "recovery_date": datetime(2024, 8, 10, tzinfo=timezone.utc),
            "recovery_days": 56,
            "calmar_ratio": 1.44,
            "profit_factor": 1.8,
            "win_rate": 0.583,
            "total_trades": 45,
            "winning_trades": 26,
            "losing_trades": 19,
            "avg_win": 1250.50,
            "avg_loss": -850.25,
            "largest_win": 3500.00,
            "largest_loss": -2100.00,
            "total_pnl": 15300.00,
            "calculation_timestamp": datetime.now(timezone.utc),
        }

    @pytest.fixture
    def sample_trades(self):
        """Provide sample trade data for testing."""
        return [
            {
                "entry_time": datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc),
                "exit_time": datetime(2024, 1, 15, 11, 45, tzinfo=timezone.utc),
                "symbol": "AAPL",
                "side": "LONG",
                "quantity": 100,
                "entry_price": Decimal("150.25"),
                "exit_price": Decimal("152.80"),
                "pnl": Decimal("255.00"),
                "commission": Decimal("2.00"),
                "strategy_name": "sma_crossover",
            },
            {
                "entry_time": datetime(2024, 1, 16, 10, 15, tzinfo=timezone.utc),
                "exit_time": datetime(2024, 1, 16, 14, 30, tzinfo=timezone.utc),
                "symbol": "MSFT",
                "side": "SHORT",
                "quantity": 50,
                "entry_price": Decimal("310.50"),
                "exit_price": Decimal("308.75"),
                "pnl": Decimal("87.50"),
                "commission": Decimal("1.50"),
                "strategy_name": "mean_reversion",
            },
        ]

    @pytest.mark.component
    def test_generator_initialization(self, generator):
        """Test that TextReportGenerator initializes correctly."""
        assert generator is not None
        assert hasattr(generator, "console")
        assert generator.console is not None

    @pytest.mark.component
    def test_generate_performance_report_structure(self, generator, sample_metrics):
        """Test that performance report generates with correct structure."""
        report = generator.generate_performance_report(sample_metrics)

        assert isinstance(report, str)
        assert len(report) > 0

        # Check for expected sections
        assert "Performance Summary" in report
        assert "Returns Analysis" in report
        assert "Risk Metrics" in report
        assert "Trading Statistics" in report

    @pytest.mark.component
    def test_performance_summary_panel_content(self, generator, sample_metrics):
        """Test that performance summary panel contains key metrics."""
        report = generator.generate_performance_report(sample_metrics)

        # Check for formatted metrics
        assert "15.30%" in report  # Total return formatted as percentage
        assert "1.42" in report  # Sharpe ratio
        assert "-8.70%" in report  # Max drawdown formatted as percentage
        assert "58.30%" in report  # Win rate formatted as percentage

    @pytest.mark.component
    def test_returns_analysis_table(self, generator, sample_metrics):
        """Test that returns analysis table contains correct data."""
        report = generator.generate_performance_report(sample_metrics)

        # Check for returns metrics
        assert "Total Return" in report
        assert "CAGR" in report
        assert "Annual Return" in report
        assert "Volatility" in report
        assert "12.50%" in report  # CAGR formatted

    @pytest.mark.component
    def test_risk_metrics_table(self, generator, sample_metrics):
        """Test that risk metrics table displays correctly."""
        report = generator.generate_performance_report(sample_metrics)

        # Check for risk metrics
        assert "Sharpe Ratio" in report
        assert "Sortino Ratio" in report
        assert "Max Drawdown" in report
        assert "Calmar Ratio" in report
        assert "1.68" in report  # Sortino ratio
        assert "1.44" in report  # Calmar ratio

    @pytest.mark.component
    def test_trading_statistics_table(self, generator, sample_metrics):
        """Test that trading statistics table shows trade data."""
        report = generator.generate_performance_report(sample_metrics)

        # Check for trading stats
        assert "Total Trades" in report
        assert "Winning Trades" in report
        assert "Win Rate" in report
        assert "Profit Factor" in report
        assert "45" in report  # Total trades
        assert "26" in report  # Winning trades
        assert "1.80" in report  # Profit factor

    @pytest.mark.component
    def test_trade_history_report(self, generator, sample_trades):
        """Test trade history report generation."""
        report = generator.generate_trade_history_report(sample_trades)

        assert isinstance(report, str)
        assert "Trade History" in report

        # Check for trade data
        assert "AAPL" in report
        assert "MSFT" in report
        assert "LONG" in report
        assert "SHORT" in report
        assert "150.25" in report  # Entry price
        assert "255.00" in report  # PnL

    @pytest.mark.component
    def test_equity_curve_text_representation(self, generator):
        """Test equity curve text representation."""
        # Create sample equity curve data
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        equity_values = [100000 + i * 500 for i in range(10)]
        equity_curve = pd.Series(equity_values, index=dates)

        report = generator.generate_equity_curve_report(equity_curve)

        assert isinstance(report, str)
        assert "Equity Curve" in report
        assert "100,000" in report  # Starting value
        assert "104,500" in report  # Final value

    @pytest.mark.component
    def test_comprehensive_report_generation(self, generator, sample_metrics, sample_trades):
        """Test comprehensive report with all sections."""
        equity_curve = pd.Series(
            [100000, 102000, 101500, 103000, 104500],
            index=pd.date_range("2024-01-01", periods=5, freq="D"),
        )

        report = generator.generate_comprehensive_report(
            metrics=sample_metrics, trades=sample_trades, equity_curve=equity_curve
        )

        assert isinstance(report, str)
        assert len(report) > 1000  # Should be substantial

        # Check all sections are present
        assert "Performance Summary" in report
        assert "Returns Analysis" in report
        assert "Risk Metrics" in report
        assert "Trading Statistics" in report
        assert "Trade History" in report
        assert "Equity Curve" in report

    @pytest.mark.component
    def test_empty_data_handling(self, generator):
        """Test report generation with empty data."""
        empty_metrics = {}
        empty_trades = []
        empty_equity = pd.Series(dtype=float)

        # Should not raise exceptions
        performance_report = generator.generate_performance_report(empty_metrics)
        trades_report = generator.generate_trade_history_report(empty_trades)
        equity_report = generator.generate_equity_curve_report(empty_equity)

        assert isinstance(performance_report, str)
        assert isinstance(trades_report, str)
        assert isinstance(equity_report, str)

        # Should contain appropriate messages for empty data
        assert "No performance data available" in performance_report or "N/A" in performance_report

    @pytest.mark.component
    def test_formatting_with_none_values(self, generator):
        """Test formatting when metrics contain None values."""
        metrics_with_nones = {
            "sharpe_ratio": None,
            "total_return": 0.15,
            "max_drawdown": None,
            "win_rate": 0.58,
            "recovery_date": None,
        }

        report = generator.generate_performance_report(metrics_with_nones)

        assert isinstance(report, str)
        assert "N/A" in report or "None" in report  # Should handle None values gracefully

    @pytest.mark.component
    def test_decimal_precision_formatting(self, generator, sample_trades):
        """Test that Decimal values are formatted correctly."""
        report = generator.generate_trade_history_report(sample_trades)

        # Should preserve decimal precision in display
        assert "150.25" in report  # Entry price
        assert "152.80" in report  # Exit price
        assert "255.00" in report  # PnL
        # Commission is not displayed in table, but Decimal formatting is preserved in price fields

    @pytest.mark.component
    def test_datetime_formatting(self, generator, sample_trades):
        """Test that datetime values are formatted consistently."""
        report = generator.generate_trade_history_report(sample_trades)

        # Should contain formatted dates
        assert "2024-01-15" in report
        assert "2024-01-16" in report

    @pytest.mark.component
    def test_performance_attribution_report(self, generator):
        """Test performance attribution by strategy."""
        strategy_performance = {
            "sma_crossover": {
                "total_pnl": 8500.00,
                "trades": 25,
                "win_rate": 0.60,
                "sharpe_ratio": 1.35,
            },
            "mean_reversion": {
                "total_pnl": 6800.00,
                "trades": 20,
                "win_rate": 0.55,
                "sharpe_ratio": 1.48,
            },
        }

        report = generator.generate_strategy_attribution_report(strategy_performance)

        assert isinstance(report, str)
        assert "Strategy Performance" in report
        assert "sma_crossover" in report
        assert "mean_reversion" in report
        assert "8,500.00" in report
        assert "6,800.00" in report

    @patch("src.services.reports.text_report.Console")
    @pytest.mark.component
    def test_console_capture_mechanism(self, mock_console_class, generator):
        """Test that console capture works correctly."""
        mock_console = MagicMock()
        mock_capture = MagicMock()
        mock_capture.get.return_value = "Test report content"
        mock_console.capture.return_value.__enter__.return_value = mock_capture
        mock_console_class.return_value = mock_console

        # Reinitialize with mocked console
        generator = TextReportGenerator()

        generator.generate_performance_report({})

        # Should call console capture
        mock_console.capture.assert_called()

    @pytest.mark.component
    def test_rich_formatting_elements(self, generator, sample_metrics):
        """Test that Rich formatting elements are properly included."""
        report = generator.generate_performance_report(sample_metrics)

        # Check for Rich panel and table markers (these appear in captured output)
        # The exact format depends on Rich version, but should contain structured content
        assert len(report.split("\n")) > 10  # Should be multi-line formatted

        # Should contain box drawing characters or structured layout
        lines = report.split("\n")
        structured_lines = [line for line in lines if line.strip()]
        assert len(structured_lines) > 5  # Should have multiple formatted sections

    @pytest.mark.component
    def test_export_to_file(self, generator, sample_metrics, tmp_path):
        """Test exporting report to file."""
        output_file = tmp_path / "test_report.txt"

        success = generator.export_performance_report(
            metrics=sample_metrics, output_path=str(output_file)
        )

        assert success is True
        assert output_file.exists()

        content = output_file.read_text()
        assert "Performance Summary" in content
        assert "15.30%" in content
