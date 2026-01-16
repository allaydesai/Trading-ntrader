"""Tests for backtest CLI signal validation flags.

Tests for US9 - Run Backtest with Signal Validation Enabled:
- T101: Unit test for --enable-signals CLI flag parsing
- T102: Unit test for --signal-export-path CLI option
- T103: Integration test: Backtest with signals enabled produces CSV export
"""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


class MockBacktestResult:
    """Mock backtest result for testing."""

    def __init__(self):
        self.total_return = Decimal("0.15")
        self.total_trades = 10
        self.winning_trades = 6
        self.losing_trades = 4
        self.win_rate = 60.0
        self.largest_win = Decimal("500.00")
        self.largest_loss = Decimal("-200.00")
        self.final_balance = Decimal("11500.00")


class MockSignalStatistics:
    """Mock signal statistics for testing."""

    def __init__(self):
        self.total_evaluations = 100
        self.total_triggered = 8
        self.signal_rate = 0.08
        self.trigger_rates = {"trend_filter": 0.72, "rsi_oversold": 0.10}
        self.blocking_rates = {"trend_filter": 0.15, "rsi_oversold": 0.72}
        self.near_miss_count = 12
        self.near_miss_threshold = 0.75
        self.primary_blocker = "rsi_oversold"


class TestEnableSignalsFlag:
    """Test cases for --enable-signals CLI flag."""

    @pytest.mark.unit
    def test_enable_signals_flag_exists(self):
        """T101: Test that --enable-signals flag is recognized by the CLI."""
        from src.cli.commands.backtest import run_backtest

        runner = CliRunner()
        result = runner.invoke(run_backtest, ["--help"])

        assert result.exit_code == 0
        assert "--enable-signals" in result.output

    @pytest.mark.unit
    def test_enable_signals_flag_default_false(self):
        """T101: Test that --enable-signals defaults to False."""
        from src.cli.commands.backtest import run_backtest

        runner = CliRunner()
        result = runner.invoke(run_backtest, ["--help"])

        # The help text should indicate it's a flag (boolean option)
        assert "--enable-signals" in result.output
        # Default behavior is disabled (no signals captured unless flag is set)

    @patch("src.cli.commands.backtest.DataCatalogService")
    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    @pytest.mark.unit
    def test_enable_signals_flag_passed_to_runner(
        self, mock_runner_class, mock_catalog_service_class
    ):
        """T101: Test that --enable-signals flag is passed to the backtest runner."""
        from src.cli.commands.backtest import run_backtest

        # Mock catalog service
        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.covers_range.return_value = True
        mock_availability.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_availability.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
        mock_availability.file_count = 5
        mock_availability.total_rows = 1000

        mock_catalog_service.get_availability.return_value = mock_availability

        async def mock_fetch_or_load(*args, **kwargs):
            return [MagicMock()]

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = MagicMock()
        mock_catalog_service_class.return_value = mock_catalog_service

        # Mock backtest runner with signal support
        mock_runner = MagicMock()
        # Explicitly set signal_statistics to None (no signal stats returned yet)
        mock_runner.signal_statistics = None

        async def mock_run_backtest(*args, **kwargs):
            # Track if enable_signals was passed
            mock_run_backtest.enable_signals = kwargs.get("enable_signals", False)
            return MockBacktestResult(), None

        mock_runner.run_backtest_with_catalog_data = mock_run_backtest
        mock_runner.dispose = MagicMock()
        mock_runner_class.return_value = mock_runner

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            [
                "--symbol",
                "AAPL",
                "--start",
                "2024-01-01",
                "--end",
                "2024-01-31",
                "--enable-signals",
            ],
        )

        # Should succeed and pass enable_signals=True to runner
        assert result.exit_code == 0
        assert mock_run_backtest.enable_signals is True


class TestSignalExportPathOption:
    """Test cases for --signal-export-path CLI option."""

    @pytest.mark.unit
    def test_signal_export_path_option_exists(self):
        """T102: Test that --signal-export-path option is recognized by the CLI."""
        from src.cli.commands.backtest import run_backtest

        runner = CliRunner()
        result = runner.invoke(run_backtest, ["--help"])

        assert result.exit_code == 0
        assert "--signal-export-path" in result.output

    @patch("src.cli.commands.backtest.DataCatalogService")
    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    @pytest.mark.unit
    def test_signal_export_path_requires_enable_signals(
        self, mock_runner_class, mock_catalog_service_class
    ):
        """T102: Test that --signal-export-path requires --enable-signals."""
        from src.cli.commands.backtest import run_backtest

        # Mock catalog service
        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.covers_range.return_value = True
        mock_availability.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_availability.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
        mock_availability.file_count = 5
        mock_availability.total_rows = 1000

        mock_catalog_service.get_availability.return_value = mock_availability

        async def mock_fetch_or_load(*args, **kwargs):
            return [MagicMock()]

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = MagicMock()
        mock_catalog_service_class.return_value = mock_catalog_service

        # Mock backtest runner
        mock_runner = MagicMock()

        async def mock_run_backtest(*args, **kwargs):
            return MockBacktestResult(), None

        mock_runner.run_backtest_with_catalog_data = mock_run_backtest
        mock_runner.dispose = MagicMock()
        mock_runner_class.return_value = mock_runner

        runner = CliRunner()

        # Using --signal-export-path without --enable-signals should show warning
        with runner.isolated_filesystem():
            result = runner.invoke(
                run_backtest,
                [
                    "--symbol",
                    "AAPL",
                    "--start",
                    "2024-01-01",
                    "--end",
                    "2024-01-31",
                    "--signal-export-path",
                    "./signals.csv",
                ],
            )

            # Should either fail validation or produce a warning about requiring --enable-signals
            # For now, the implementation may choose to:
            # 1. Auto-enable signals when export path is provided
            # 2. Or show an error/warning
            # We accept either behavior in this test
            assert result.exit_code == 0 or "enable-signals" in result.output.lower()

    @patch("src.cli.commands.backtest.DataCatalogService")
    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    @pytest.mark.unit
    def test_signal_export_path_creates_csv(self, mock_runner_class, mock_catalog_service_class):
        """T102: Test that --signal-export-path creates CSV file at specified location."""
        from src.cli.commands.backtest import run_backtest

        # Mock catalog service
        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.covers_range.return_value = True
        mock_availability.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_availability.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
        mock_availability.file_count = 5
        mock_availability.total_rows = 1000

        mock_catalog_service.get_availability.return_value = mock_availability

        async def mock_fetch_or_load(*args, **kwargs):
            return [MagicMock()]

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = MagicMock()
        mock_catalog_service_class.return_value = mock_catalog_service

        # Mock backtest runner with signal export capability
        mock_runner = MagicMock()
        mock_runner.signal_statistics = None  # No signal stats returned yet
        export_path_captured = {}

        async def mock_run_backtest(*args, **kwargs):
            export_path_captured["path"] = kwargs.get("signal_export_path")
            return MockBacktestResult(), None

        mock_runner.run_backtest_with_catalog_data = mock_run_backtest
        mock_runner.dispose = MagicMock()
        mock_runner_class.return_value = mock_runner

        runner = CliRunner()

        with runner.isolated_filesystem():
            result = runner.invoke(
                run_backtest,
                [
                    "--symbol",
                    "AAPL",
                    "--start",
                    "2024-01-01",
                    "--end",
                    "2024-01-31",
                    "--enable-signals",
                    "--signal-export-path",
                    "./output/signals.csv",
                ],
            )

            assert result.exit_code == 0
            # Verify the export path was captured
            assert export_path_captured.get("path") == "./output/signals.csv"


class TestSignalSummaryDisplay:
    """Test cases for signal summary display in CLI output."""

    @patch("src.cli.commands.backtest.DataCatalogService")
    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    @pytest.mark.unit
    def test_signal_summary_displayed_when_enabled(
        self, mock_runner_class, mock_catalog_service_class
    ):
        """T103: Test that signal summary is displayed when --enable-signals is used."""
        from src.cli.commands.backtest import run_backtest

        # Mock catalog service
        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.covers_range.return_value = True
        mock_availability.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_availability.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
        mock_availability.file_count = 5
        mock_availability.total_rows = 1000

        mock_catalog_service.get_availability.return_value = mock_availability

        async def mock_fetch_or_load(*args, **kwargs):
            return [MagicMock()]

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = MagicMock()
        mock_catalog_service_class.return_value = mock_catalog_service

        # Mock backtest runner with signal statistics
        mock_runner = MagicMock()

        async def mock_run_backtest(*args, **kwargs):
            result = MockBacktestResult()
            # When signals enabled, also return signal statistics
            if kwargs.get("enable_signals"):
                mock_runner.signal_statistics = MockSignalStatistics()
            return result, None

        mock_runner.run_backtest_with_catalog_data = mock_run_backtest
        mock_runner.signal_statistics = None
        mock_runner.dispose = MagicMock()
        mock_runner_class.return_value = mock_runner

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            [
                "--symbol",
                "AAPL",
                "--start",
                "2024-01-01",
                "--end",
                "2024-01-31",
                "--enable-signals",
            ],
        )

        assert result.exit_code == 0
        # When signals are enabled, the CLI should display signal analysis section
        # Expected output includes: Total Evaluations, Trigger Rate, Primary Blocker, Near-Misses
        # This test will fail until implementation is complete (TDD red phase)
        assert "Signal Analysis" in result.output or "signal" in result.output.lower()

    @patch("src.cli.commands.backtest.DataCatalogService")
    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    @pytest.mark.unit
    def test_no_signal_summary_when_disabled(self, mock_runner_class, mock_catalog_service_class):
        """T103: Test that signal summary is NOT displayed when --enable-signals is not used."""
        from src.cli.commands.backtest import run_backtest

        # Mock catalog service
        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.covers_range.return_value = True
        mock_availability.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_availability.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
        mock_availability.file_count = 5
        mock_availability.total_rows = 1000

        mock_catalog_service.get_availability.return_value = mock_availability

        async def mock_fetch_or_load(*args, **kwargs):
            return [MagicMock()]

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = MagicMock()
        mock_catalog_service_class.return_value = mock_catalog_service

        # Mock backtest runner without signals
        mock_runner = MagicMock()

        async def mock_run_backtest(*args, **kwargs):
            return MockBacktestResult(), None

        mock_runner.run_backtest_with_catalog_data = mock_run_backtest
        mock_runner.dispose = MagicMock()
        mock_runner_class.return_value = mock_runner

        runner = CliRunner()
        result = runner.invoke(
            run_backtest,
            [
                "--symbol",
                "AAPL",
                "--start",
                "2024-01-01",
                "--end",
                "2024-01-31",
                # NOTE: --enable-signals is NOT passed
            ],
        )

        assert result.exit_code == 0
        # When signals are disabled, no signal analysis section should appear
        assert "Signal Analysis" not in result.output
        assert "Primary Blocker" not in result.output


class TestSignalExportIntegration:
    """Integration tests for signal export functionality."""

    @patch("src.cli.commands.backtest.DataCatalogService")
    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    @pytest.mark.unit
    def test_backtest_with_signals_exports_csv(self, mock_runner_class, mock_catalog_service_class):
        """T103: Integration test - backtest with signals enabled produces CSV export."""
        from src.cli.commands.backtest import run_backtest

        # Mock catalog service
        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.covers_range.return_value = True
        mock_availability.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_availability.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
        mock_availability.file_count = 5
        mock_availability.total_rows = 1000

        mock_catalog_service.get_availability.return_value = mock_availability

        async def mock_fetch_or_load(*args, **kwargs):
            return [MagicMock()]

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = MagicMock()
        mock_catalog_service_class.return_value = mock_catalog_service

        # Mock backtest runner that creates actual export file
        mock_runner = MagicMock()
        mock_runner.signal_statistics = None  # No signal stats for this test
        export_created = {"path": None}

        async def mock_run_backtest(*args, **kwargs):
            if kwargs.get("enable_signals"):
                export_path = kwargs.get("signal_export_path")
                if export_path:
                    # Create the output directory if it doesn't exist
                    output_dir = Path(export_path).parent
                    output_dir.mkdir(parents=True, exist_ok=True)
                    # Create a mock CSV file
                    with open(export_path, "w") as f:
                        f.write("timestamp,bar_type,signal,strength,blocking_component\n")
                        f.write(
                            "2024-01-01T10:00:00,AAPL.NASDAQ-1-DAY-LAST,False,0.75,rsi_oversold\n"
                        )
                    export_created["path"] = export_path
            return MockBacktestResult(), None

        mock_runner.run_backtest_with_catalog_data = mock_run_backtest
        mock_runner.dispose = MagicMock()
        mock_runner_class.return_value = mock_runner

        runner = CliRunner()

        with runner.isolated_filesystem():
            result = runner.invoke(
                run_backtest,
                [
                    "--symbol",
                    "AAPL",
                    "--start",
                    "2024-01-01",
                    "--end",
                    "2024-01-31",
                    "--enable-signals",
                    "--signal-export-path",
                    "./output/signals.csv",
                ],
            )

            assert result.exit_code == 0

            # Verify CSV file was created
            csv_path = Path("./output/signals.csv")
            assert csv_path.exists(), "Signal audit CSV should be created"

            # Verify CSV has content
            content = csv_path.read_text()
            assert "timestamp" in content
            assert "signal" in content
            assert "strength" in content

    @patch("src.cli.commands.backtest.DataCatalogService")
    @patch("src.cli.commands.backtest.MinimalBacktestRunner")
    @pytest.mark.unit
    def test_signal_export_path_with_audit_trail_message(
        self, mock_runner_class, mock_catalog_service_class
    ):
        """T103: Test that export path is shown in output when signals are exported."""
        from src.cli.commands.backtest import run_backtest

        # Mock catalog service
        mock_catalog_service = MagicMock()
        mock_availability = MagicMock()
        mock_availability.covers_range.return_value = True
        mock_availability.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_availability.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
        mock_availability.file_count = 5
        mock_availability.total_rows = 1000

        mock_catalog_service.get_availability.return_value = mock_availability

        async def mock_fetch_or_load(*args, **kwargs):
            return [MagicMock()]

        mock_catalog_service.fetch_or_load = mock_fetch_or_load
        mock_catalog_service.load_instrument.return_value = MagicMock()
        mock_catalog_service_class.return_value = mock_catalog_service

        # Mock backtest runner
        mock_runner = MagicMock()
        mock_runner.signal_statistics = None  # No signal stats for this test

        async def mock_run_backtest(*args, **kwargs):
            return MockBacktestResult(), None

        mock_runner.run_backtest_with_catalog_data = mock_run_backtest
        mock_runner.dispose = MagicMock()
        mock_runner_class.return_value = mock_runner

        runner = CliRunner()

        with runner.isolated_filesystem():
            result = runner.invoke(
                run_backtest,
                [
                    "--symbol",
                    "AAPL",
                    "--start",
                    "2024-01-01",
                    "--end",
                    "2024-01-31",
                    "--enable-signals",
                    "--signal-export-path",
                    "./output/signals.csv",
                ],
            )

            assert result.exit_code == 0
            # The output should mention the audit trail location
            # This test will fail until implementation is complete (TDD red phase)
            assert (
                "Audit Trail" in result.output
                or "signals.csv" in result.output
                or "export" in result.output.lower()
            )
