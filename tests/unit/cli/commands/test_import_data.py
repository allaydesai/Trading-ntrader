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

    @pytest.mark.unit
    def test_30min_token(self):
        """Story 2.4 AC3: the 30-minute convention is a valid timeframe token."""
        from src.cli.commands.import_data import parse_timeframes

        assert parse_timeframes("30min") == ["30-MINUTE-LAST"]


# ---------------------------------------------------------------------------
# Timeframe resolution (Story 2.4 AC2/AC3)
# ---------------------------------------------------------------------------


class TestResolveTimeframes:
    """ETF imports default to all five native timeframes; explicit choice wins."""

    @pytest.mark.unit
    def test_etf_default_is_all_five_native(self):
        from src.cli.commands.import_data import resolve_timeframes
        from src.models.catalog import AssetClass

        assert resolve_timeframes(None, AssetClass.ETF) == [
            "1-DAY-LAST",
            "1-HOUR-LAST",
            "30-MINUTE-LAST",
            "5-MINUTE-LAST",
            "1-MINUTE-LAST",
        ]

    @pytest.mark.unit
    def test_non_etf_default_is_daily(self):
        from src.cli.commands.import_data import resolve_timeframes
        from src.models.catalog import AssetClass

        assert resolve_timeframes(None, AssetClass.STOCK) == ["1-DAY-LAST"]

    @pytest.mark.unit
    def test_explicit_timeframe_wins_for_etf(self):
        from src.cli.commands.import_data import resolve_timeframes
        from src.models.catalog import AssetClass

        assert resolve_timeframes("30min", AssetClass.ETF) == ["30-MINUTE-LAST"]
        assert resolve_timeframes("daily,hourly", AssetClass.ETF) == [
            "1-DAY-LAST",
            "1-HOUR-LAST",
        ]


# ---------------------------------------------------------------------------
# Metadata resolver wiring (Story 2.4 AC4)
# ---------------------------------------------------------------------------


class TestBuildMetadataResolver:
    """ETF imports get a cache-first resolver; non-ETF and unconfigured FMP skip."""

    @staticmethod
    def _fmp(api_key: str):
        from src.config import FMPSettings

        return FMPSettings(fmp_api_key=api_key)

    @pytest.mark.unit
    def test_non_etf_returns_none(self):
        from unittest.mock import MagicMock

        from src.cli.commands.import_data import _build_metadata_resolver
        from src.models.catalog import AssetClass

        result = _build_metadata_resolver(MagicMock(), AssetClass.STOCK, self._fmp("present"))
        assert result is None

    @pytest.mark.unit
    def test_etf_without_api_key_returns_none(self):
        from unittest.mock import MagicMock

        from src.cli.commands.import_data import _build_metadata_resolver
        from src.models.catalog import AssetClass

        result = _build_metadata_resolver(MagicMock(), AssetClass.ETF, self._fmp(""))
        assert result is None

    @pytest.mark.unit
    def test_etf_with_api_key_builds_resolver_without_network(self):
        from unittest.mock import MagicMock

        from src.cli.commands.import_data import _build_metadata_resolver
        from src.models.catalog import AssetClass
        from src.services.metadata.instrument_metadata_service import (
            InstrumentMetadataService,
        )

        result = _build_metadata_resolver(MagicMock(), AssetClass.ETF, self._fmp("test-key"))
        assert isinstance(result, InstrumentMetadataService)


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
        """Summary includes the Story 1-7 four-bucket breakdown."""
        from src.cli.commands.import_reporting import build_summary_text

        results = [
            ImportResult(
                ticker="SPY",
                status="success",
                row_count=6523,
                duration=1.0,
                outcome="new",
            ),
            ImportResult(
                ticker="QQQ",
                status="success",
                row_count=5892,
                duration=0.8,
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
        assert "Re-imported:     0" in text
        assert "Skipped:         0" in text
        assert "Failed:          1" in text
        assert "12,415" in text
        assert "Total processed: 2" in text

    @pytest.mark.unit
    def test_failure_table_lists_ticker_and_reason(self):
        """Failures listed with ticker + reason."""
        from src.cli.commands.import_reporting import build_summary_text

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
        from src.cli.commands.import_reporting import build_summary_text

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


# ---------------------------------------------------------------------------
# Story 2.3 — ETF dry-run wiring (cache reader + date ranges) and rendering
# ---------------------------------------------------------------------------


def _empty_report(source_path, asset_class):
    from src.models.catalog import DryRunReport

    return DryRunReport(
        asset_class=asset_class,
        source_path=str(source_path),
        timeframes={},
        total_file_count=0,
        total_source_bytes=0,
        estimated_parquet_bytes=0,
    )


class _FakeSession:
    """Lazy, connection-free stand-in for a sync SQLAlchemy session."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class TestEtfDryRunWiring:
    """The ETF path wires the cache reader + date ranges; stocks does not."""

    @pytest.mark.unit
    def test_etf_passes_cache_reader_and_date_ranges(self, tmp_path, monkeypatch):
        """ETF dry-run injects a cache reader and requests date ranges (AC1/AC3)."""
        from src.cli.commands.import_data import _run_dry_run

        captured: dict = {}

        def fake_build(source_path, asset_class, **kw):
            captured.update(kw)
            return _empty_report(source_path, asset_class)

        session = _FakeSession()
        monkeypatch.setattr(
            "src.db.session_sync.get_sync_session_maker",
            lambda: (lambda: session),
        )
        with patch("src.cli.commands.import_data.build_dry_run_report", side_effect=fake_build):
            code = _run_dry_run("firstrate", "test", tmp_path, "etf")

        assert code == 0
        assert captured["include_date_ranges"] is True
        assert captured["cache_reader"] is not None
        # The read-only session is always closed when done.
        assert session.closed is True

    @pytest.mark.unit
    def test_stock_omits_cache_reader_and_date_ranges(self, tmp_path):
        """Stocks dry-run passes no reader and no date ranges (invariant intact)."""
        from src.cli.commands.import_data import _run_dry_run

        captured: dict = {}

        def fake_build(source_path, asset_class, **kw):
            captured.update(kw)
            return _empty_report(source_path, asset_class)

        with patch("src.cli.commands.import_data.build_dry_run_report", side_effect=fake_build):
            code = _run_dry_run("firstrate", "test", tmp_path, None)

        assert code == 0
        assert captured["cache_reader"] is None
        assert captured["include_date_ranges"] is False

    @pytest.mark.unit
    def test_etf_db_unconfigured_degrades_gracefully(self, tmp_path, monkeypatch):
        """No DB → warn, omit the estimate, still scan and return 0 (AC3 degrade)."""
        from src.cli.commands.import_data import _run_dry_run

        captured: dict = {}

        def fake_build(source_path, asset_class, **kw):
            captured.update(kw)
            return _empty_report(source_path, asset_class)

        monkeypatch.setattr("src.db.session_sync.get_sync_session_maker", lambda: None)
        with patch("src.cli.commands.import_data.build_dry_run_report", side_effect=fake_build):
            code = _run_dry_run("firstrate", "test", tmp_path, "etf")

        assert code == 0
        # No reader (DB unconfigured) but date ranges still requested.
        assert captured["cache_reader"] is None
        assert captured["include_date_ranges"] is True

    @pytest.mark.unit
    def test_etf_db_unavailable_at_query_degrades(self, tmp_path, monkeypatch):
        """A DB error mid-estimate is caught; the scan still prints and returns 0."""
        from sqlalchemy.exc import OperationalError

        from src.cli.commands.import_data import _run_dry_run

        calls: dict = {"n": 0}

        def fake_build(source_path, asset_class, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                # First call (with reader) blows up as if the DB went away.
                raise OperationalError("SELECT 1", {}, Exception("connection refused"))
            # Retry (without reader) succeeds.
            assert kw["cache_reader"] is None
            return _empty_report(source_path, asset_class)

        session = _FakeSession()
        monkeypatch.setattr(
            "src.db.session_sync.get_sync_session_maker",
            lambda: (lambda: session),
        )
        with patch("src.cli.commands.import_data.build_dry_run_report", side_effect=fake_build):
            code = _run_dry_run("firstrate", "test", tmp_path, "etf")

        assert code == 0
        assert calls["n"] == 2  # rebuilt offline
        assert session.closed is True


class TestPrintDryRunReportStory23:
    """_print_dry_run_report renders the new sections and keeps the trailer."""

    @pytest.mark.unit
    def test_renders_date_ranges_estimate_and_trailer(self, capsys):
        from src.cli.commands.import_data import _print_dry_run_report
        from src.models.catalog import (
            AssetClass,
            DryRunReport,
            MetadataEstimate,
            TickerDateRange,
            TimeframeSummary,
        )

        report = DryRunReport(
            asset_class=AssetClass.ETF,
            source_path="/data/etf",
            timeframes={
                "1-DAY-LAST": TimeframeSummary(ticker_count=2, file_count=2, source_bytes=1000)
            },
            total_file_count=2,
            total_source_bytes=1000,
            estimated_parquet_bytes=350,
            distinct_ticker_count=2,
            ticker_date_ranges={
                "AAA": TickerDateRange(earliest="2020-01-02", latest="2024-12-31"),
            },
            metadata_estimate=MetadataEstimate(
                total_tickers=2, cached_count=1, needs_resolution_count=1
            ),
        )

        _print_dry_run_report(report)
        out = capsys.readouterr().out

        assert "2020-01-02" in out
        assert "2024-12-31" in out
        assert "cached" in out.lower()
        assert "resolution" in out.lower()
        assert "No data written — dry run only." in out


# ---------------------------------------------------------------------------
# Story 1-7 — Skip-outcome progress/summary/exit-code
# ---------------------------------------------------------------------------


class TestStory17ProgressAndSummary:
    """Three-way bucket rendering + all-skipped exit-code discipline."""

    @pytest.mark.unit
    def test_progress_line_renders_skip_glyph(self, capsys):
        from src.cli.commands.import_data import _print_progress_line

        result = ImportResult(
            ticker="SPY",
            status="skipped",
            row_count=0,
            duration=0.001,
            outcome="skipped",
        )
        _print_progress_line(result, "1-DAY-LAST")
        out = capsys.readouterr().out
        assert "SPY" in out
        assert "1-DAY-LAST" in out
        assert "skipped" in out
        assert "\u27f3" in out  # ⟳ glyph
        assert "\u2713" not in out  # no success check
        assert "\u2717" not in out  # no failure cross

    @pytest.mark.unit
    def test_progress_line_renders_success_glyph_for_reimport(self, capsys):
        from src.cli.commands.import_data import _print_progress_line

        result = ImportResult(
            ticker="SPY",
            status="success",
            row_count=42,
            duration=0.5,
            outcome="reimported",
        )
        _print_progress_line(result, "1-DAY-LAST")
        out = capsys.readouterr().out
        assert "\u2713" in out
        assert "42" in out

    @pytest.mark.unit
    def test_build_summary_text_renders_all_four_buckets(self):
        from src.cli.commands.import_reporting import build_summary_text

        results = [
            ImportResult(
                ticker="A",
                status="success",
                row_count=100,
                duration=0.1,
                outcome="new",
            ),
            ImportResult(
                ticker="B",
                status="success",
                row_count=200,
                duration=0.1,
                outcome="reimported",
            ),
            ImportResult(
                ticker="C",
                status="skipped",
                row_count=0,
                duration=0.001,
                outcome="skipped",
            ),
            ImportResult(
                ticker="D",
                status="failed",
                row_count=0,
                error="boom",
                duration=0.05,
            ),
        ]
        text = build_summary_text(results)
        assert "Total tickers:   4" in text
        assert "New:             1" in text
        assert "Re-imported:     1" in text
        assert "Skipped:         1" in text
        assert "Failed:          1" in text
        assert "Total rows:      300" in text  # 100 + 200, excludes skipped/failed
        assert "Total processed: 2" in text  # new + reimported

    @pytest.mark.unit
    def test_print_summary_shows_all_buckets(self, capsys):
        from src.cli.commands.import_data import _print_summary

        results = [
            ImportResult(
                ticker="A",
                status="success",
                row_count=50,
                duration=0.1,
                outcome="new",
            ),
            ImportResult(
                ticker="B",
                status="skipped",
                row_count=0,
                duration=0.001,
                outcome="skipped",
            ),
            ImportResult(
                ticker="C",
                status="skipped",
                row_count=0,
                duration=0.001,
                outcome="skipped",
            ),
        ]
        _print_summary(results)
        out = capsys.readouterr().out
        # Rich-rendered table may wrap; just check key values are present.
        assert "Total tickers" in out
        assert "New" in out
        assert "Re-imported" in out
        assert "Skipped" in out
        assert "Total processed" in out

    @pytest.mark.unit
    def test_determine_exit_code_all_skipped_returns_0(self):
        from src.cli.commands.import_data import determine_exit_code

        results = [
            ImportResult(
                ticker="A",
                status="skipped",
                row_count=0,
                duration=0.001,
                outcome="skipped",
            ),
            ImportResult(
                ticker="B",
                status="skipped",
                row_count=0,
                duration=0.001,
                outcome="skipped",
            ),
        ]
        assert determine_exit_code(results) == 0

    @pytest.mark.unit
    def test_determine_exit_code_mixed_success_and_skipped_returns_0(self):
        from src.cli.commands.import_data import determine_exit_code

        results = [
            ImportResult(
                ticker="A",
                status="success",
                row_count=100,
                duration=0.5,
                outcome="new",
            ),
            ImportResult(
                ticker="B",
                status="skipped",
                row_count=0,
                duration=0.001,
                outcome="skipped",
            ),
        ]
        assert determine_exit_code(results) == 0

    @pytest.mark.unit
    def test_determine_exit_code_with_any_failure_returns_1(self):
        from src.cli.commands.import_data import determine_exit_code

        results = [
            ImportResult(
                ticker="A",
                status="skipped",
                row_count=0,
                duration=0.001,
                outcome="skipped",
            ),
            ImportResult(
                ticker="B",
                status="failed",
                row_count=0,
                error="boom",
                duration=0.1,
            ),
        ]
        assert determine_exit_code(results) == 1


# ---------------------------------------------------------------------------
# Story 3.3: venue-override merge wiring (_apply_venue_overrides)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplyVenueOverrides:
    """The import-orchestration helper that merges venue_overrides.csv."""

    def _settings(self, path: str):
        from unittest.mock import MagicMock

        settings = MagicMock()
        settings.firstrate.firstrate_venue_overrides_path = path
        return settings

    def test_empty_file_is_silent_noop(self, capsys):
        from src.cli.commands.import_data import _apply_venue_overrides

        with patch("src.services.metadata.venue_overrides.load_venue_overrides", return_value={}):
            _apply_venue_overrides(object(), self._settings("venue_overrides.csv"))
        assert "Venue overrides" not in capsys.readouterr().out

    def test_non_empty_prints_summary(self, capsys):
        from src.cli.commands.import_data import _apply_venue_overrides
        from src.services.metadata.venue_overrides import VenueOverrideMergeResult

        with (
            patch(
                "src.services.metadata.venue_overrides.load_venue_overrides",
                return_value={"SPY": "ARCA"},
            ),
            patch(
                "src.services.metadata.venue_overrides.merge_venue_overrides",
                return_value=VenueOverrideMergeResult(applied=1),
            ),
            patch(
                "src.db.repositories.instrument_metadata_repository_sync."
                "SyncInstrumentMetadataRepository"
            ),
        ):
            _apply_venue_overrides(object(), self._settings("venue_overrides.csv"))
        assert "1 applied" in capsys.readouterr().out

    def test_unmatched_tickers_listed(self, capsys):
        from src.cli.commands.import_data import _apply_venue_overrides
        from src.services.metadata.venue_overrides import VenueOverrideMergeResult

        with (
            patch(
                "src.services.metadata.venue_overrides.load_venue_overrides",
                return_value={"ZZZ": "ARCA"},
            ),
            patch(
                "src.services.metadata.venue_overrides.merge_venue_overrides",
                return_value=VenueOverrideMergeResult(unmatched=["ZZZ"]),
            ),
            patch(
                "src.db.repositories.instrument_metadata_repository_sync."
                "SyncInstrumentMetadataRepository"
            ),
        ):
            _apply_venue_overrides(object(), self._settings("venue_overrides.csv"))
        out = capsys.readouterr().out
        assert "1 unmatched" in out
        assert "ZZZ" in out

    def test_malformed_header_warns_and_skips_without_markup_crash(self, capsys):
        # The real VenueOverrideError message embeds the header repr, which always
        # contains '[' / ']'. Without escaping, Rich would raise MarkupError and the
        # exception would propagate out of _apply_venue_overrides, rolling back the
        # in-flight import. This exercises the escape() fix (must NOT raise).
        from src.cli.commands.import_data import _apply_venue_overrides
        from src.services.metadata.venue_overrides import VenueOverrideError

        msg = "venue_overrides header must be exactly 'ticker,venue' (got: ['symbol', 'exchange'])"
        with patch(
            "src.services.metadata.venue_overrides.load_venue_overrides",
            side_effect=VenueOverrideError(msg),
        ):
            _apply_venue_overrides(object(), self._settings("venue_overrides.csv"))
        assert "not applied" in capsys.readouterr().out
