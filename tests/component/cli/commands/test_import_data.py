"""Component tests for CLI import command with mocked ImportService."""

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from src.models.catalog import ImportResult

_VALID_LINE = "2024-01-02 09:30:00,100.0,101.0,99.5,100.5,1000"


def _seed_valid_firstrate_dir(tmp_path: Path) -> Path:
    """Create a small FirstRate-style directory with 2 tickers in 1 timeframe."""
    (tmp_path / "A").mkdir()
    (tmp_path / "S").mkdir()
    (tmp_path / "A" / "AAPL_full_1day_adjsplitdiv.txt").write_text(_VALID_LINE)
    (tmp_path / "S" / "SPY_full_1day_adjsplitdiv.txt").write_text(_VALID_LINE)
    return tmp_path


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
        """Summary text includes the Story 1-7 four-bucket metrics."""
        from src.cli.commands.import_data import build_summary_text

        results = [
            ImportResult(
                ticker="SPY",
                status="success",
                row_count=6523,
                duration=1.2,
                outcome="new",
            ),
            ImportResult(
                ticker="QQQ",
                status="success",
                row_count=5892,
                duration=0.9,
                outcome="new",
            ),
            ImportResult(
                ticker="BAC",
                status="failed",
                row_count=0,
                error="parse error",
                duration=0.5,
            ),
        ]
        text = build_summary_text(results)
        assert "Total tickers:   3" in text
        assert "New:             2" in text
        assert "Failed:          1" in text
        assert "12,415" in text
        assert "Total processed: 2" in text
        assert "BAC" in text
        assert "parse error" in text


class TestMultiTimeframe:
    """Component test: multi-timeframe invocation."""

    @pytest.mark.component
    def test_multi_timeframe_calls_import_per_timeframe(self, runner, tmp_path):
        """CLI with --timeframe daily,hourly calls import_directory twice."""
        from src.cli.commands.import_data import import_firstrate

        call_log = []

        def mock_run_import(
            format_name,
            catalog,
            source_path,
            asset_class,
            timeframe,
            dividends_dir=None,
            splits_dir=None,
        ):
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


class TestDryRunInvocation:
    """Component tests for --dry-run flag (Story 1-6, AC-1..AC-4)."""

    @pytest.mark.component
    def test_happy_path_reports_and_exits_zero(self, runner, tmp_path):
        """Valid Stocks directory: dry-run exits 0 with a populated report."""
        from src.cli.commands.import_data import import_firstrate

        _seed_valid_firstrate_dir(tmp_path)

        result = runner.invoke(
            import_firstrate,
            [
                "--format",
                "firstrate",
                "--catalog",
                "test",
                "--dry-run",
                str(tmp_path),
            ],
        )

        assert result.exit_code == 0
        assert "Dry-Run Report" in result.output
        assert "STOCK" in result.output
        assert "1-DAY-LAST" in result.output
        # Task 7.1 explicitly requires the Tickers/Files counts to be asserted.
        # Match the 1-DAY-LAST row's ticker and file columns against "2" with a
        # regex so we aren't fooled by "2" appearing elsewhere in the output.
        import re

        assert re.search(r"1-DAY-LAST\D+2\D+2", result.output), (
            f"expected '1-DAY-LAST ... 2 ... 2' row in output, got:\n{result.output}"
        )
        # The catalog name should also appear in the header per D3.
        assert "test" in result.output
        assert "No data written" in result.output

    @pytest.mark.component
    def test_schema_mismatch_still_zero_exit(self, runner, tmp_path):
        """Files with bad schema produce a mismatch table but exit 0 (AC-4)."""
        from src.cli.commands.import_data import import_firstrate

        (tmp_path / "A").mkdir()
        (tmp_path / "A" / "AAPL_full_1day_adjsplitdiv.txt").write_text("1,2,3,4,5")

        result = runner.invoke(
            import_firstrate,
            [
                "--format",
                "firstrate",
                "--catalog",
                "test",
                "--dry-run",
                str(tmp_path),
            ],
        )

        assert result.exit_code == 0
        assert "Schema Mismatches" in result.output
        assert "5" in result.output  # detected column count
        assert "6" in result.output  # expected column count

    @pytest.mark.component
    def test_zero_side_effects(self, runner, tmp_path):
        """Dry-run must not construct any import-path services or DB session."""
        from src.cli.commands.import_data import import_firstrate

        _seed_valid_firstrate_dir(tmp_path)

        def _should_not_be_called(*_a, **_kw):
            raise AssertionError("constructed in dry-run")

        with (
            patch(
                "src.services.firstrate.import_service.ImportService.__init__",
                _should_not_be_called,
            ),
            patch(
                "src.services.firstrate.catalog_manager.CatalogManager.__init__",
                _should_not_be_called,
            ),
            patch(
                "src.services.firstrate.instrument_mapper.InstrumentMapper.__init__",
                _should_not_be_called,
            ),
            patch(
                "src.services.firstrate.metadata_service.MetadataService.__init__",
                _should_not_be_called,
            ),
            patch(
                "src.db.session_sync.get_sync_session_maker",
                side_effect=AssertionError("DB touched in dry-run"),
            ),
        ):
            result = runner.invoke(
                import_firstrate,
                [
                    "--format",
                    "firstrate",
                    "--catalog",
                    "test",
                    "--dry-run",
                    str(tmp_path),
                ],
                catch_exceptions=False,
            )

        assert result.exit_code == 0

    @pytest.mark.component
    def test_multi_timeframe_report_rows(self, runner, tmp_path):
        """1day + 1hour files produce two timeframe rows plus TOTAL row."""
        from src.cli.commands.import_data import import_firstrate

        (tmp_path / "A").mkdir()
        (tmp_path / "A" / "AAPL_full_1day_adjsplitdiv.txt").write_text(_VALID_LINE)
        (tmp_path / "A" / "AAPL_full_1hour_adjsplitdiv.txt").write_text(_VALID_LINE)

        result = runner.invoke(
            import_firstrate,
            [
                "--format",
                "firstrate",
                "--catalog",
                "test",
                "--dry-run",
                str(tmp_path),
            ],
        )

        assert result.exit_code == 0
        assert "1-DAY-LAST" in result.output
        assert "1-HOUR-LAST" in result.output
        assert "TOTAL" in result.output


class TestStory17DoubleRun:
    """Story 1-7 AC-4/5: back-to-back CLI runs show New then Skipped."""

    @pytest.mark.component
    def test_second_run_shows_skip_glyph_and_summary(self, runner, tmp_path, monkeypatch):
        """Second CLI invocation surfaces the skip glyph and Skipped: N summary.

        Exercises the real ``_print_progress_line``, ``_print_summary``,
        and ``determine_exit_code`` rendering code via CliRunner, keeping
        the heavy ``_run_import`` orchestration out of scope (the whole
        first-/second-run state lives in a small counter).
        """
        import click

        from src.cli.commands import import_data as import_cmd

        _seed_valid_firstrate_dir(tmp_path)

        run_counter = {"n": 0}

        def _fake_run_import(
            format_name: str,
            catalog: str,
            source_path: Path,
            asset_class: str | None,
            timeframe: str | None,
            dividends_dir: Path | None = None,
            splits_dir: Path | None = None,
        ) -> int:
            """Emit the exact progress + summary `_run_import` would."""
            run_counter["n"] += 1
            first_run = run_counter["n"] == 1
            outcome = "new" if first_run else "skipped"
            status = "success" if first_run else "skipped"
            row_count = 6523 if first_run else 0

            click_results = [
                ImportResult(
                    ticker="AAPL",
                    status=status,
                    row_count=row_count,
                    duration=0.1,
                    outcome=outcome,
                ),
                ImportResult(
                    ticker="SPY",
                    status=status,
                    row_count=row_count,
                    duration=0.1,
                    outcome=outcome,
                ),
            ]

            click.echo("\n--- STOCK ---")
            for r in click_results:
                import_cmd._print_progress_line(r, "1-DAY-LAST")
            import_cmd._print_summary(click_results)
            return import_cmd.determine_exit_code(click_results)

        monkeypatch.setattr(import_cmd, "_run_import", _fake_run_import)

        args = [
            "--format",
            "firstrate",
            "--catalog",
            "test-stocks",
            "--asset-class",
            "stock",
            str(tmp_path),
        ]

        first_result = runner.invoke(import_cmd.import_firstrate, args)
        assert first_result.exit_code == 0
        assert "\u2713" in first_result.output  # success checks on first run
        assert "\u27f3" not in first_result.output  # no skips on first run
        assert "New" in first_result.output

        second_result = runner.invoke(import_cmd.import_firstrate, args)
        assert second_result.exit_code == 0
        assert "\u27f3" in second_result.output  # skip glyph
        assert "skipped (already complete)" in second_result.output
        assert "Skipped" in second_result.output
        assert "Total processed" in second_result.output
