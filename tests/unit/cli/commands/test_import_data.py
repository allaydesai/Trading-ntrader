"""Unit tests for CLI import command."""

from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

from src.models.catalog import ImportResult


@pytest.fixture
def runner():
    return CliRunner()


# ---------------------------------------------------------------------------
# Timeframe parsing tests (Task 1.4)
# ---------------------------------------------------------------------------


class TestParseTimeframes:
    """Test comma-separated timeframe string parsing."""

    @pytest.mark.unit
    def test_single_daily(self):
        from src.cli.commands.import_data import parse_timeframes

        assert parse_timeframes("daily") == ["1-DAY-LAST"]

    @pytest.mark.unit
    def test_single_hourly(self):
        from src.cli.commands.import_data import parse_timeframes

        assert parse_timeframes("hourly") == ["1-HOUR-LAST"]

    @pytest.mark.unit
    def test_single_minute(self):
        from src.cli.commands.import_data import parse_timeframes

        assert parse_timeframes("minute") == ["1-MINUTE-LAST"]

    @pytest.mark.unit
    def test_multiple_timeframes(self):
        from src.cli.commands.import_data import parse_timeframes

        result = parse_timeframes("daily,hourly")
        assert result == ["1-DAY-LAST", "1-HOUR-LAST"]

    @pytest.mark.unit
    def test_aliases(self):
        from src.cli.commands.import_data import parse_timeframes

        assert parse_timeframes("1min") == ["1-MINUTE-LAST"]
        assert parse_timeframes("5min") == ["5-MINUTE-LAST"]

    @pytest.mark.unit
    def test_whitespace_handling(self):
        from src.cli.commands.import_data import parse_timeframes

        result = parse_timeframes(" daily , hourly ")
        assert result == ["1-DAY-LAST", "1-HOUR-LAST"]

    @pytest.mark.unit
    def test_unknown_timeframe_raises(self):
        from src.cli.commands.import_data import parse_timeframes

        with pytest.raises(click.BadParameter):
            parse_timeframes("weekly")


# ---------------------------------------------------------------------------
# CLI argument parsing tests (Task 1.1)
# ---------------------------------------------------------------------------


class TestCliArgumentParsing:
    """Test Click command option parsing."""

    @pytest.mark.unit
    @patch("src.cli.commands.import_data._run_import")
    def test_required_format_option(self, mock_run, runner, tmp_path):
        """--format is required."""
        from src.cli.commands.import_data import import_firstrate

        result = runner.invoke(
            import_firstrate,
            ["--catalog", "test", str(tmp_path)],
        )
        assert result.exit_code != 0
        assert "Missing option" in result.output or "format" in result.output.lower()

    @pytest.mark.unit
    @patch("src.cli.commands.import_data._run_import")
    def test_required_catalog_option(self, mock_run, runner, tmp_path):
        """--catalog is required."""
        from src.cli.commands.import_data import import_firstrate

        result = runner.invoke(
            import_firstrate,
            ["--format", "firstrate", str(tmp_path)],
        )
        assert result.exit_code != 0
        assert "Missing option" in result.output or "catalog" in result.output.lower()

    @pytest.mark.unit
    @patch("src.cli.commands.import_data._run_import")
    def test_source_path_positional(self, mock_run, runner, tmp_path):
        """Source path is a required positional argument."""
        from src.cli.commands.import_data import import_firstrate

        result = runner.invoke(
            import_firstrate,
            ["--format", "firstrate", "--catalog", "test"],
        )
        assert result.exit_code != 0
        assert "Missing argument" in result.output or "source" in result.output.lower()

    @pytest.mark.unit
    @patch("src.cli.commands.import_data._run_import")
    def test_valid_invocation_calls_run_import(self, mock_run, runner, tmp_path):
        """Valid args trigger _run_import."""
        from src.cli.commands.import_data import import_firstrate

        mock_run.return_value = 0
        runner.invoke(
            import_firstrate,
            ["--format", "firstrate", "--catalog", "test", str(tmp_path)],
        )
        assert mock_run.called

    @pytest.mark.unit
    @patch("src.cli.commands.import_data._run_import")
    def test_asset_class_option(self, mock_run, runner, tmp_path):
        """--asset-class accepts valid choices."""
        from src.cli.commands.import_data import import_firstrate

        mock_run.return_value = 0
        result = runner.invoke(
            import_firstrate,
            [
                "--format",
                "firstrate",
                "--catalog",
                "test",
                "--asset-class",
                "etf",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0
        call_kwargs = mock_run.call_args
        assert call_kwargs is not None

    @pytest.mark.unit
    @patch("src.cli.commands.import_data._run_import")
    def test_timeframe_option(self, mock_run, runner, tmp_path):
        """--timeframe accepts comma-separated values."""
        from src.cli.commands.import_data import import_firstrate

        mock_run.return_value = 0
        result = runner.invoke(
            import_firstrate,
            [
                "--format",
                "firstrate",
                "--catalog",
                "test",
                "--timeframe",
                "daily,hourly",
                str(tmp_path),
            ],
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Invalid format tests (Task 1.2)
# ---------------------------------------------------------------------------


class TestInvalidFormat:
    """Test invalid/missing format handling — exit code 2."""

    @pytest.mark.unit
    def test_unsupported_format_exits_2(self, runner, tmp_path):
        """Unsupported --format value produces exit code 2."""
        from src.cli.commands.import_data import import_firstrate

        result = runner.invoke(
            import_firstrate,
            ["--format", "unknown", "--catalog", "test", str(tmp_path)],
        )
        assert result.exit_code == 2

    @pytest.mark.unit
    def test_missing_format_exits_nonzero(self, runner, tmp_path):
        """Missing --format produces non-zero exit."""
        from src.cli.commands.import_data import import_firstrate

        result = runner.invoke(
            import_firstrate,
            ["--catalog", "test", str(tmp_path)],
        )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Summary report generation tests (Task 4.1, 4.2, 4.4)
# ---------------------------------------------------------------------------


class TestSummaryReport:
    """Test summary report content generation."""

    @pytest.mark.unit
    def test_summary_totals(self):
        """Summary includes total tickers, rows, successes, failures."""
        from src.cli.commands.import_data import build_summary_text

        results = [
            ImportResult(ticker="SPY", status="success", row_count=6523, duration=1.0),
            ImportResult(ticker="QQQ", status="success", row_count=5892, duration=0.8),
            ImportResult(
                ticker="BAC",
                status="failed",
                row_count=0,
                error="parse error",
                duration=0.5,
            ),
        ]
        text = build_summary_text(results)
        assert "Total tickers: 3" in text
        assert "Successful:    2" in text
        assert "Failed:        1" in text
        assert "12,415" in text

    @pytest.mark.unit
    def test_failure_table_lists_ticker_and_reason(self):
        """Failures listed with ticker + reason."""
        from src.cli.commands.import_data import build_summary_text

        results = [
            ImportResult(
                ticker="BAC",
                status="failed",
                row_count=0,
                error="invalid OHLC",
                duration=0.5,
            ),
        ]
        text = build_summary_text(results)
        assert "BAC" in text
        assert "invalid OHLC" in text

    @pytest.mark.unit
    def test_all_success_no_failure_section(self):
        """When all succeed, no failure details shown."""
        from src.cli.commands.import_data import build_summary_text

        results = [
            ImportResult(ticker="SPY", status="success", row_count=100, duration=0.5),
        ]
        text = build_summary_text(results)
        assert "Failures" not in text or "0" in text


# ---------------------------------------------------------------------------
# Exit code logic tests (Task 4.4)
# ---------------------------------------------------------------------------


class TestExitCode:
    """Test exit code determination."""

    @pytest.mark.unit
    def test_all_success_returns_0(self):
        from src.cli.commands.import_data import determine_exit_code

        results = [
            ImportResult(ticker="SPY", status="success", row_count=100, duration=0.5),
        ]
        assert determine_exit_code(results) == 0

    @pytest.mark.unit
    def test_some_failures_returns_1(self):
        from src.cli.commands.import_data import determine_exit_code

        results = [
            ImportResult(ticker="SPY", status="success", row_count=100, duration=0.5),
            ImportResult(
                ticker="BAC",
                status="failed",
                row_count=0,
                error="err",
                duration=0.5,
            ),
        ]
        assert determine_exit_code(results) == 1

    @pytest.mark.unit
    def test_all_failures_returns_1(self):
        from src.cli.commands.import_data import determine_exit_code

        results = [
            ImportResult(
                ticker="BAC",
                status="failed",
                row_count=0,
                error="err",
                duration=0.5,
            ),
        ]
        assert determine_exit_code(results) == 1

    @pytest.mark.unit
    def test_empty_results_returns_0(self):
        from src.cli.commands.import_data import determine_exit_code

        assert determine_exit_code([]) == 0
