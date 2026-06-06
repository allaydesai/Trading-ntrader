"""Unit tests for the FirstRate dry-run scanner and validator.

The dry-run module must stay pure Python — **no Nautilus imports**. These
tests also enforce that invariant (see ``test_module_does_not_import_nautilus``).
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from src.models.catalog import AssetClass
from src.services.firstrate.dry_run import (
    PARQUET_COMPRESSION_RATIO,
    build_dry_run_report,
    estimate_parquet_bytes,
    format_bytes,
    scan_firstrate_directory,
    validate_schema_sample,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_file(path: Path, body: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


_VALID_LINE = "2024-01-02 09:30:00,100.0,101.0,99.5,100.5,1000"


# ---------------------------------------------------------------------------
# scan_firstrate_directory
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestScanFirstRateDirectory:
    def test_empty_directory(self, tmp_path):
        """Empty directory returns an empty report (no crash)."""
        report = scan_firstrate_directory(tmp_path, AssetClass.STOCK)
        assert report.asset_class == AssetClass.STOCK
        assert report.timeframes == {}
        assert report.total_file_count == 0
        assert report.total_source_bytes == 0
        assert report.distinct_ticker_count == 0
        assert report.unreadable_count == 0

    def test_single_timeframe_alphabetical_subdirs(self, tmp_path):
        """source_path = timeframe dir with A/S/ subdirs → one timeframe group."""
        _make_file(tmp_path / "A" / "AAPL_full_1day_adjsplitdiv.txt", _VALID_LINE)
        _make_file(tmp_path / "S" / "SPY_full_1day_adjsplitdiv.txt", _VALID_LINE)

        report = scan_firstrate_directory(tmp_path, AssetClass.STOCK)

        assert list(report.timeframes.keys()) == ["1-DAY-LAST"]
        summary = report.timeframes["1-DAY-LAST"]
        assert summary.ticker_count == 2
        assert summary.file_count == 2
        assert report.total_file_count == 2
        assert report.distinct_ticker_count == 2

    def test_mixed_timeframes(self, tmp_path):
        """Files with different timeframe suffixes produce distinct groups."""
        _make_file(tmp_path / "A" / "AAPL_full_1day_adjsplitdiv.txt", _VALID_LINE)
        _make_file(tmp_path / "A" / "AAPL_full_1hour_adjsplitdiv.txt", _VALID_LINE)

        report = scan_firstrate_directory(tmp_path, AssetClass.STOCK)

        assert set(report.timeframes.keys()) == {"1-DAY-LAST", "1-HOUR-LAST"}
        assert report.timeframes["1-DAY-LAST"].file_count == 1
        assert report.timeframes["1-HOUR-LAST"].file_count == 1
        assert report.total_file_count == 2
        # Same ticker across timeframes collapses to 1 distinct ticker.
        assert report.distinct_ticker_count == 1

    def test_nested_layout(self, tmp_path):
        """source_path = Stocks/ with Stocks_1day/A/... and Stocks_1hour/A/... walks recursively."""
        root = tmp_path / "Stocks"
        _make_file(root / "Stocks_1day" / "A" / "AAPL_full_1day_adjsplitdiv.txt", _VALID_LINE)
        _make_file(root / "Stocks_1day" / "S" / "SPY_full_1day_adjsplitdiv.txt", _VALID_LINE)
        _make_file(root / "Stocks_1hour" / "A" / "AAPL_full_1hour_adjsplitdiv.txt", _VALID_LINE)

        report = scan_firstrate_directory(root, AssetClass.STOCK)

        assert report.timeframes["1-DAY-LAST"].ticker_count == 2
        assert report.timeframes["1-DAY-LAST"].file_count == 2
        assert report.timeframes["1-HOUR-LAST"].ticker_count == 1
        assert report.total_file_count == 3
        # Two distinct tickers total (AAPL in both timeframes, SPY in one).
        assert report.distinct_ticker_count == 2

    def test_non_txt_files_skipped(self, tmp_path):
        """Entries that are not .txt files are ignored."""
        _make_file(tmp_path / "A" / "AAPL_full_1day_adjsplitdiv.txt", _VALID_LINE)
        _make_file(tmp_path / "A" / "README.md", "not data")
        _make_file(tmp_path / "A" / "company_profiles.csv", "Ticker")

        report = scan_firstrate_directory(tmp_path, AssetClass.STOCK)
        assert report.total_file_count == 1

    def test_uppercase_extension_included(self, tmp_path):
        """Files with .TXT / .Txt extensions are discovered (case-insensitive)."""
        _make_file(tmp_path / "A" / "AAPL_full_1day_adjsplitdiv.TXT", _VALID_LINE)
        _make_file(tmp_path / "A" / "BAC_full_1day_adjsplitdiv.Txt", _VALID_LINE)

        report = scan_firstrate_directory(tmp_path, AssetClass.STOCK)
        assert report.total_file_count == 2
        assert report.timeframes["1-DAY-LAST"].file_count == 2

    def test_uppercase_timeframe_suffix(self, tmp_path):
        """Uppercase suffixes like _1Day_ are mapped to 1-DAY-LAST (not 'unknown')."""
        _make_file(tmp_path / "A" / "AAPL_full_1Day_adjsplitdiv.txt", _VALID_LINE)
        _make_file(tmp_path / "A" / "BAC_full_1HOUR_adjsplitdiv.txt", _VALID_LINE)

        report = scan_firstrate_directory(tmp_path, AssetClass.STOCK)
        assert "unknown" not in report.timeframes
        assert "1-DAY-LAST" in report.timeframes
        assert "1-HOUR-LAST" in report.timeframes

    def test_unknown_timeframe_bucketed(self, tmp_path):
        """Files with unknown timeframe suffixes go into an ``unknown`` group."""
        _make_file(tmp_path / "A" / "AAPL_full_weekly_adjsplitdiv.txt", _VALID_LINE)
        report = scan_firstrate_directory(tmp_path, AssetClass.STOCK)
        assert "unknown" in report.timeframes
        assert report.timeframes["unknown"].file_count == 1

    def test_missing_full_marker_is_schema_mismatch(self, tmp_path):
        """Files without '_full_' are excluded from counts and reported as mismatches."""
        _make_file(tmp_path / "A" / "AAPL.txt", _VALID_LINE)
        _make_file(tmp_path / "A" / "weird_1day_BAC.txt", _VALID_LINE)

        report = scan_firstrate_directory(tmp_path, AssetClass.STOCK)

        # These files must not inflate ticker/file counts.
        assert report.total_file_count == 0
        assert report.timeframes == {}
        assert report.distinct_ticker_count == 0
        # They show up in schema_mismatches with the filename-pattern reason.
        assert len(report.schema_mismatches) == 2
        reasons = {m.reason for m in report.schema_mismatches}
        assert reasons == {"unrecognized filename pattern"}

    def test_ticker_count_deduplicates(self, tmp_path):
        """Same ticker across multiple files counts once per timeframe."""
        # Same ticker, same timeframe, in different dirs — ticker_count should
        # still be 1 for that timeframe.
        _make_file(tmp_path / "A" / "AAPL_full_1day_adjsplitdiv.txt", _VALID_LINE)
        _make_file(tmp_path / "B" / "AAPL_full_1day_adjsplitdiv.txt", _VALID_LINE)

        report = scan_firstrate_directory(tmp_path, AssetClass.STOCK)
        summary = report.timeframes["1-DAY-LAST"]
        assert summary.ticker_count == 1
        assert summary.file_count == 2

    def test_source_bytes_sum(self, tmp_path):
        """source_bytes equals the sum of every file's stat().st_size."""
        f1 = _make_file(tmp_path / "A" / "AAPL_full_1day_adjsplitdiv.txt", "x" * 123)
        f2 = _make_file(tmp_path / "B" / "BAC_full_1day_adjsplitdiv.txt", "y" * 456)

        report = scan_firstrate_directory(tmp_path, AssetClass.STOCK)
        expected = f1.stat().st_size + f2.stat().st_size
        assert report.timeframes["1-DAY-LAST"].source_bytes == expected
        assert report.total_source_bytes == expected

    def test_scanner_never_opens_files(self, tmp_path):
        """Scanner must not read file bodies via any Path/io open mechanism."""
        _make_file(tmp_path / "A" / "AAPL_full_1day_adjsplitdiv.txt", _VALID_LINE)

        # Patch Path.open (what our validator uses) AND builtins.open (what
        # anyone else might reach for). Both must see zero calls.
        with (
            patch(
                "pathlib.Path.open",
                side_effect=AssertionError("scanner must not open files"),
            ),
            patch(
                "builtins.open",
                side_effect=AssertionError("scanner must not open files"),
            ),
        ):
            # The scanner is stat-only; if it tries to read a body either
            # patch fires and the test fails loudly.
            scan_firstrate_directory(tmp_path, AssetClass.STOCK)


# ---------------------------------------------------------------------------
# validate_schema_sample
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateSchemaSample:
    def test_valid_six_columns(self, tmp_path):
        """Six-column files produce no mismatches."""
        f = _make_file(tmp_path / "ok.txt", _VALID_LINE)
        assert validate_schema_sample([f]) == []

    def test_five_columns_mismatch(self, tmp_path):
        """Five-column file is reported as detected=5."""
        f = _make_file(tmp_path / "bad.txt", "2024-01-02,100,101,99,100")
        mismatches = validate_schema_sample([f])
        assert len(mismatches) == 1
        assert mismatches[0].file_path == f
        assert mismatches[0].expected_columns == 6
        assert mismatches[0].detected_columns == 5
        assert mismatches[0].reason is None

    def test_seven_columns_mismatch(self, tmp_path):
        """Seven-column file is reported as detected=7."""
        f = _make_file(tmp_path / "bad.txt", "2024,1,2,3,4,5,6")
        mismatches = validate_schema_sample([f])
        assert mismatches[0].detected_columns == 7

    def test_empty_file_mismatch(self, tmp_path):
        """Empty file is reported as detected=0 with reason='empty file'."""
        f = _make_file(tmp_path / "empty.txt", "")
        mismatches = validate_schema_sample([f])
        assert len(mismatches) == 1
        assert mismatches[0].detected_columns == 0
        assert mismatches[0].reason == "empty file"

    def test_decode_error_mismatch(self, tmp_path):
        """Non-UTF-8 bytes produce a mismatch with reason='decode error'."""
        f = tmp_path / "bin.txt"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"\xff\xfe\x00\x01valid-looking-garbage\xc0\xc1")

        mismatches = validate_schema_sample([f])
        assert len(mismatches) == 1
        assert mismatches[0].detected_columns == 0
        assert mismatches[0].reason == "decode error"

    def test_leading_blank_lines_tolerated(self, tmp_path):
        """Leading blank lines are skipped to find the first data row."""
        f = _make_file(tmp_path / "ok.txt", f"\n\n  \n{_VALID_LINE}\n")
        assert validate_schema_sample([f]) == []

    def test_crlf_endings_tolerated(self, tmp_path):
        """CRLF line endings are normalized when reading the first data row."""
        f = _make_file(tmp_path / "ok.txt", f"\r\n{_VALID_LINE}\r\n")
        assert validate_schema_sample([f]) == []

    def test_sample_size_limit(self, tmp_path):
        """Only the first ``sample_size`` files are inspected."""
        files = [_make_file(tmp_path / f"f{i}.txt", _VALID_LINE) for i in range(10)]
        # Make files 5-9 bad; with sample_size=5 they should be ignored.
        for f in files[5:]:
            f.write_text("1,2,3,4,5", encoding="utf-8")

        mismatches = validate_schema_sample(files, sample_size=5)
        assert mismatches == []

    def test_module_does_not_import_nautilus(self):
        """Importing dry_run in a fresh subprocess must not pull in nautilus_trader.

        A subprocess check is the only robust way to detect transitive imports:
        AST-walking the source would miss anything pulled in via a re-export,
        and sys.modules in the current process is polluted by other tests.
        """
        script = (
            "import sys\n"
            "import src.services.firstrate.dry_run  # noqa: F401\n"
            "assert 'nautilus_trader' not in sys.modules, (\n"
            "    'dry_run pulled nautilus_trader into sys.modules: '\n"
            "    + ', '.join(m for m in sys.modules if m.startswith('nautilus_trader'))\n"
            ")\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(Path(__file__).resolve().parents[4]),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"subprocess failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        )


# ---------------------------------------------------------------------------
# estimate_parquet_bytes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEstimateParquetBytes:
    def test_ratio_is_035(self):
        """PARQUET_COMPRESSION_RATIO is 0.35 (documented tunable)."""
        assert PARQUET_COMPRESSION_RATIO == 0.35

    def test_zero_bytes(self):
        assert estimate_parquet_bytes(0) == 0

    def test_deterministic_ratio(self):
        assert estimate_parquet_bytes(1000) == int(1000 * PARQUET_COMPRESSION_RATIO)
        assert estimate_parquet_bytes(1_000_000) == int(1_000_000 * PARQUET_COMPRESSION_RATIO)


# ---------------------------------------------------------------------------
# format_bytes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatBytes:
    def test_bytes_scale(self):
        assert format_bytes(0) == "0 B"
        assert format_bytes(512) == "512 B"

    def test_kib_scale(self):
        assert format_bytes(1024) == "1.00 KiB"
        assert format_bytes(2048) == "2.00 KiB"

    def test_mib_scale(self):
        assert format_bytes(1024 * 1024) == "1.00 MiB"

    def test_gib_scale(self):
        assert format_bytes(1024**3) == "1.00 GiB"

    def test_tib_scale(self):
        assert format_bytes(1024**4) == "1.00 TiB"
        assert format_bytes(5 * 1024**4) == "5.00 TiB"


# ---------------------------------------------------------------------------
# build_dry_run_report — integration of scan + validate + estimate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildDryRunReport:
    def test_happy_path(self, tmp_path):
        """build_dry_run_report wires scanner + validator + estimator."""
        _make_file(tmp_path / "A" / "AAPL_full_1day_adjsplitdiv.txt", _VALID_LINE)
        _make_file(tmp_path / "S" / "SPY_full_1day_adjsplitdiv.txt", _VALID_LINE)

        report = build_dry_run_report(tmp_path, AssetClass.STOCK)

        assert report.total_file_count == 2
        assert report.distinct_ticker_count == 2
        assert report.estimated_parquet_bytes == int(
            report.total_source_bytes * PARQUET_COMPRESSION_RATIO
        )
        assert report.schema_mismatches == []

    def test_catalog_plumbed_through(self, tmp_path):
        """The ``catalog`` kwarg is surfaced on the report for header display."""
        _make_file(tmp_path / "A" / "AAPL_full_1day_adjsplitdiv.txt", _VALID_LINE)
        report = build_dry_run_report(tmp_path, AssetClass.STOCK, catalog="stocks-dev")
        assert report.catalog == "stocks-dev"

    def test_schema_mismatches_flow_through(self, tmp_path):
        """Bad schema surfaces in report.schema_mismatches."""
        _make_file(tmp_path / "A" / "AAPL_full_1day_adjsplitdiv.txt", "1,2,3,4,5")
        report = build_dry_run_report(tmp_path, AssetClass.STOCK)
        assert len(report.schema_mismatches) == 1
        assert report.schema_mismatches[0].detected_columns == 5

    def test_name_pattern_and_schema_mismatches_both_present(self, tmp_path):
        """Filename-pattern mismatches and schema mismatches coexist."""
        _make_file(tmp_path / "A" / "AAPL.txt", _VALID_LINE)  # no _full_
        _make_file(tmp_path / "B" / "BAC_full_1day_adjsplitdiv.txt", "1,2,3")  # 3 cols

        report = build_dry_run_report(tmp_path, AssetClass.STOCK)
        reasons = {m.reason for m in report.schema_mismatches}
        assert "unrecognized filename pattern" in reasons
        assert None in reasons  # plain column-count mismatch has reason=None
