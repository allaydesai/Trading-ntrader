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
# Profiles path discovery
# ---------------------------------------------------------------------------


class TestFindProfilesCsv:
    """Test discovery of company_profiles.csv across source_path and parent."""

    @pytest.mark.unit
    def test_found_at_source_path(self, tmp_path):
        """Returns path when csv is directly inside source_path."""
        from src.cli.commands.import_data import _find_profiles_csv

        source = tmp_path / "data"
        source.mkdir()
        expected = source / "company_profiles.csv"
        expected.write_text("Ticker\nSPY\n")

        assert _find_profiles_csv(source) == expected

    @pytest.mark.unit
    def test_found_at_parent_directory(self, tmp_path):
        """Returns parent path when csv lives one level up (FirstRate layout)."""
        from src.cli.commands.import_data import _find_profiles_csv

        parent = tmp_path / "Stocks"
        parent.mkdir()
        source = parent / "Stocks_1day"
        source.mkdir()
        expected = parent / "company_profiles.csv"
        expected.write_text("Ticker\nA\n")

        assert _find_profiles_csv(source) == expected

    @pytest.mark.unit
    def test_prefers_source_path_over_parent(self, tmp_path):
        """If both exist, source_path wins."""
        from src.cli.commands.import_data import _find_profiles_csv

        parent = tmp_path / "Data"
        parent.mkdir()
        source = parent / "Stocks_1day"
        source.mkdir()

        parent_csv = parent / "company_profiles.csv"
        parent_csv.write_text("parent")
        source_csv = source / "company_profiles.csv"
        source_csv.write_text("source")

        assert _find_profiles_csv(source) == source_csv

    @pytest.mark.unit
    def test_returns_none_when_missing(self, tmp_path):
        """Returns None when neither location has the csv."""
        from src.cli.commands.import_data import _find_profiles_csv

        source = tmp_path / "empty"
        source.mkdir()
        assert _find_profiles_csv(source) is None


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


# ---------------------------------------------------------------------------
# Dry-run flag routing tests (Story 1-6, Task 1)
# ---------------------------------------------------------------------------


class TestDryRunFlag:
    """Test --dry-run flag routes to _run_dry_run and skips _run_import."""

    @pytest.mark.unit
    @patch("src.cli.commands.import_data._run_dry_run")
    @patch("src.cli.commands.import_data._run_import")
    def test_dry_run_routes_to_dry_run(self, mock_import, mock_dry, runner, tmp_path):
        """--dry-run calls _run_dry_run, not _run_import."""
        from src.cli.commands.import_data import import_firstrate

        mock_dry.return_value = 0
        result = runner.invoke(
            import_firstrate,
            ["--format", "firstrate", "--catalog", "test", "--dry-run", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert mock_dry.called
        assert not mock_import.called

    @pytest.mark.unit
    @patch("src.cli.commands.import_data._run_dry_run")
    @patch("src.cli.commands.import_data._run_import")
    def test_no_dry_run_routes_to_import(self, mock_import, mock_dry, runner, tmp_path):
        """Default (no flag) calls _run_import, not _run_dry_run."""
        from src.cli.commands.import_data import import_firstrate

        mock_import.return_value = 0
        runner.invoke(
            import_firstrate,
            ["--format", "firstrate", "--catalog", "test", str(tmp_path)],
        )
        assert mock_import.called
        assert not mock_dry.called

    @pytest.mark.unit
    @patch("src.cli.commands.import_data._run_dry_run")
    def test_dry_run_never_constructs_services(self, mock_dry, runner, tmp_path, monkeypatch):
        """Dry-run must not instantiate any import-path services."""
        from src.cli.commands.import_data import import_firstrate

        mock_dry.return_value = 0

        # Patch every service class and DB accessor that the import path uses.
        sentinel = []

        def _fail(*_a, **_kw):
            sentinel.append("called")
            raise AssertionError("service must not be constructed in dry-run")

        monkeypatch.setattr("src.services.firstrate.import_service.ImportService.__init__", _fail)
        monkeypatch.setattr("src.services.firstrate.catalog_manager.CatalogManager.__init__", _fail)
        monkeypatch.setattr(
            "src.services.firstrate.instrument_mapper.InstrumentMapper.__init__",
            _fail,
        )
        monkeypatch.setattr(
            "src.services.firstrate.metadata_service.MetadataService.__init__",
            _fail,
        )

        def _db_must_not_be_touched():
            raise AssertionError("DB must not be touched in dry-run")

        monkeypatch.setattr(
            "src.db.session_sync.get_sync_session_maker",
            _db_must_not_be_touched,
        )

        result = runner.invoke(
            import_firstrate,
            ["--format", "firstrate", "--catalog", "test", "--dry-run", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert sentinel == []


class TestRunDryRun:
    """Unit tests for _run_dry_run."""

    @pytest.mark.unit
    def test_happy_path_returns_0(self, tmp_path):
        """Empty directory is a valid dry-run; returns 0."""
        from src.cli.commands.import_data import _run_dry_run

        code = _run_dry_run("firstrate", "test", tmp_path, None)
        assert code == 0

    @pytest.mark.unit
    def test_mismatches_still_return_0(self, tmp_path):
        """Schema mismatches are informational — exit code stays 0 (AC-4)."""
        from src.cli.commands.import_data import _run_dry_run

        subdir = tmp_path / "A"
        subdir.mkdir()
        (subdir / "AAPL_full_1day_adjsplitdiv.txt").write_text("1,2,3,4,5")

        code = _run_dry_run("firstrate", "test", tmp_path, None)
        assert code == 0

    @pytest.mark.unit
    def test_missing_directory_returns_2(self, tmp_path):
        """Non-existent source_path returns exit code 2."""
        from src.cli.commands.import_data import _run_dry_run

        code = _run_dry_run("firstrate", "test", tmp_path / "nope", None)
        assert code == 2

    @pytest.mark.unit
    def test_asset_class_defaults_to_stock(self, tmp_path):
        """Dry-run defaults to STOCK (Phase 1 pivot), not ETF."""
        from src.cli.commands.import_data import _run_dry_run
        from src.models.catalog import AssetClass

        captured: dict = {}

        def fake_build(source_path, asset_class, **_kw):
            from src.models.catalog import DryRunReport

            captured["asset_class"] = asset_class
            return DryRunReport(
                asset_class=asset_class,
                source_path=str(source_path),
                timeframes={},
                total_file_count=0,
                total_source_bytes=0,
                estimated_parquet_bytes=0,
            )

        with patch("src.cli.commands.import_data.build_dry_run_report", side_effect=fake_build):
            _run_dry_run("firstrate", "test", tmp_path, None)

        assert captured["asset_class"] == AssetClass.STOCK

    @pytest.mark.unit
    def test_asset_class_override(self, tmp_path):
        """--asset-class override is respected on the dry-run path."""
        from src.cli.commands.import_data import _run_dry_run
        from src.models.catalog import AssetClass

        captured: dict = {}

        def fake_build(source_path, asset_class, **_kw):
            from src.models.catalog import DryRunReport

            captured["asset_class"] = asset_class
            return DryRunReport(
                asset_class=asset_class,
                source_path=str(source_path),
                timeframes={},
                total_file_count=0,
                total_source_bytes=0,
                estimated_parquet_bytes=0,
            )

        with patch("src.cli.commands.import_data.build_dry_run_report", side_effect=fake_build):
            _run_dry_run("firstrate", "test", tmp_path, "etf")

        assert captured["asset_class"] == AssetClass.ETF
