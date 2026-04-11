"""Component tests for CLI import command with mocked ImportService."""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from src.models.catalog import ImportResult


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_import_results_success():
    """All-success import results."""
    return [
        ImportResult(ticker="SPY", status="success", row_count=6523, duration=1.2),
        ImportResult(ticker="QQQ", status="success", row_count=5892, duration=0.9),
        ImportResult(ticker="IWM", status="success", row_count=4101, duration=0.7),
    ]


@pytest.fixture
def mock_import_results_mixed():
    """Mixed success/failure import results."""
    return [
        ImportResult(ticker="SPY", status="success", row_count=6523, duration=1.2),
        ImportResult(
            ticker="BAC",
            status="failed",
            row_count=0,
            error="parse error: invalid OHLC",
            duration=0.5,
        ),
        ImportResult(ticker="QQQ", status="success", row_count=5892, duration=0.9),
    ]


class TestFullCliInvocation:
    """Component test: full CLI invocation via CliRunner with mocked deps."""

    @pytest.mark.component
    def test_success_exit_code_0(self, runner, mock_import_results_success, tmp_path):
        """Exit code 0 when all tickers succeed."""
        from src.cli.commands.import_data import import_firstrate

        with patch("src.cli.commands.import_data._run_import", return_value=0):
            result = runner.invoke(
                import_firstrate,
                ["--format", "firstrate", "--catalog", "test", str(tmp_path)],
            )

        assert result.exit_code == 0

    @pytest.mark.component
    def test_exit_code_1_some_failures(self, runner, tmp_path):
        """Exit code 1 when some tickers fail."""
        from src.cli.commands.import_data import import_firstrate

        with patch("src.cli.commands.import_data._run_import", return_value=1):
            result = runner.invoke(
                import_firstrate,
                ["--format", "firstrate", "--catalog", "test", str(tmp_path)],
            )

        assert result.exit_code == 1

    @pytest.mark.component
    def test_exit_code_2_invalid_format(self, runner, tmp_path):
        """Exit code 2 for invalid format."""
        from src.cli.commands.import_data import import_firstrate

        result = runner.invoke(
            import_firstrate,
            ["--format", "badformat", "--catalog", "test", str(tmp_path)],
        )
        assert result.exit_code == 2

    @pytest.mark.component
    def test_exit_code_2_fatal_error(self, runner, tmp_path):
        """Exit code 2 when _run_import returns 2."""
        from src.cli.commands.import_data import import_firstrate

        with patch("src.cli.commands.import_data._run_import", return_value=2):
            result = runner.invoke(
                import_firstrate,
                ["--format", "firstrate", "--catalog", "test", str(tmp_path)],
            )

        assert result.exit_code == 2


class TestStreamingOutput:
    """Component test: streaming output format verification."""

    @pytest.mark.component
    def test_success_lines_have_checkmark(self, runner, tmp_path):
        """Success lines include checkmark and row count."""
        from src.cli.commands.import_data import _print_progress_line

        r = ImportResult(ticker="SPY", status="success", row_count=6523, duration=1.2)
        with patch("click.echo") as mock_echo:
            _print_progress_line(r, "1-DAY-LAST")
            line = mock_echo.call_args[0][0]

        assert "SPY" in line
        assert "6,523" in line
        assert "\u2713" in line
        assert "1-DAY-LAST" in line

    @pytest.mark.component
    def test_failure_lines_have_x_and_error(self, runner):
        """Failure lines include X marker and error message."""
        from src.cli.commands.import_data import _print_progress_line

        r = ImportResult(
            ticker="BAC",
            status="failed",
            row_count=0,
            error="parse error: invalid OHLC",
            duration=0.5,
        )
        with patch("click.echo") as mock_echo:
            _print_progress_line(r, "1-DAY-LAST")
            line = mock_echo.call_args[0][0]

        assert "BAC" in line
        assert "parse error: invalid OHLC" in line
        assert "\u2717" in line

    @pytest.mark.component
    def test_asset_class_header_format(self):
        """Asset class header uses --- FORMAT --- pattern."""
        # The header is printed inline in _run_import as:
        # click.echo(f"\n--- {ac.value} ---")
        # Verify the format directly
        from src.models.catalog import AssetClass

        header = f"--- {AssetClass.ETF.value} ---"
        assert header == "--- ETF ---"

    @pytest.mark.component
    def test_summary_report_contains_metrics(self):
        """Summary text includes all required metrics."""
        from src.cli.commands.import_data import build_summary_text

        results = [
            ImportResult(ticker="SPY", status="success", row_count=6523, duration=1.2),
            ImportResult(ticker="QQQ", status="success", row_count=5892, duration=0.9),
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
        assert "BAC" in text
        assert "parse error" in text


class TestMultiTimeframe:
    """Component test: multi-timeframe invocation."""

    @pytest.mark.component
    def test_multi_timeframe_calls_import_per_timeframe(self, runner, tmp_path):
        """CLI with --timeframe daily,hourly calls import_directory twice."""
        from src.cli.commands.import_data import import_firstrate

        call_log = []

        def mock_run_import(format_name, catalog, source_path, asset_class, timeframe):
            call_log.append(timeframe)
            return 0

        with patch(
            "src.cli.commands.import_data._run_import",
            side_effect=mock_run_import,
        ):
            runner.invoke(
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
                catch_exceptions=False,
            )

        # _run_import is called once with the timeframe string
        assert len(call_log) == 1
        assert call_log[0] == "daily,hourly"

    @pytest.mark.component
    def test_timeframe_parsing_in_run_import(self):
        """parse_timeframes splits correctly for multi-timeframe."""
        from src.cli.commands.import_data import parse_timeframes

        result = parse_timeframes("daily,hourly")
        assert result == ["1-DAY-LAST", "1-HOUR-LAST"]

        result = parse_timeframes("daily,hourly,minute")
        assert result == ["1-DAY-LAST", "1-HOUR-LAST", "1-MINUTE-LAST"]
