"""FX M2 (task 2.5): the source probe and the dry-run accept both FirstRate FX row shapes.

FX rows carry a compact date: ``YYYYMMDD,HH:MM:SS,O,H,L,C,V`` (intraday, 7 fields) and
``YYYYMMDD,O,H,L,C,V`` (daily, 6 fields). The probe must return the same UTC time the FX
parser stamps the last bar with (a daily row dated ``D`` opens ``D-1 17:00`` ET), so the
idempotent re-run classifier can skip an up-to-date series. Equity and futures stamps are
unaffected.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.models.catalog import AssetClass
from src.services.firstrate.dry_run import _infer_timeframe, build_dry_run_report
from src.services.firstrate.source_probe import compute_source_last_date

pytestmark = pytest.mark.unit


def _write(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestSourceProbe:
    def test_intraday_rows_return_the_last_bar_open_in_utc(self, tmp_path):
        path = _write(
            tmp_path / "EURUSD_full_1hour.txt",
            [
                "20260430,22:00:00,1.17,1.18,1.16,1.17,5",
                "20260430,23:00:00,1.17,1.18,1.16,1.17,5",
                "20260430,09:00:00,1.17,1.18,1.16,1.17,5",
            ],
        )
        assert compute_source_last_date(path) == datetime(2026, 5, 1, 3, tzinfo=timezone.utc)

    def test_daily_row_returns_previous_day_session_open(self, tmp_path):
        path = _write(
            tmp_path / "EURUSD_full_1day.txt",
            ["20260430,1.17,1.18,1.16,1.17,5", "20260501,1.17,1.18,1.16,1.17,5"],
        )
        # 2026-04-30 17:00 EDT
        assert compute_source_last_date(path) == datetime(2026, 4, 30, 21, tzinfo=timezone.utc)

    def test_daily_row_on_the_first_of_the_month(self, tmp_path):
        path = _write(tmp_path / "USDJPY_full_1day.txt", ["20250101,150.1,151,149,150.5,9"])
        # 2024-12-31 17:00 EST
        assert compute_source_last_date(path) == datetime(2024, 12, 31, 22, tzinfo=timezone.utc)

    def test_malformed_compact_rows_are_skipped(self, tmp_path):
        path = _write(
            tmp_path / "EURUSD_full_1day.txt",
            ["20250102,1.1,1.2,1.0,1.1,5", "20251399,1.1,1.2,1.0,1.1,5"],
        )
        assert compute_source_last_date(path) == datetime(2025, 1, 1, 22, tzinfo=timezone.utc)

    def test_equity_and_futures_stamps_unchanged(self, tmp_path):
        equity = _write(tmp_path / "SPY_full_1day_x.txt", ["2025-01-03,1,2,1,2,5"])
        futures = _write(tmp_path / "ES_full_1day_x.txt", ["2025-01-03,1,2,1,2,5,9"])
        midnight_et = datetime(2025, 1, 3, 5, tzinfo=timezone.utc)
        assert compute_source_last_date(equity) == midnight_et
        assert compute_source_last_date(futures) == midnight_et


class TestDryRun:
    @pytest.mark.parametrize(
        ("name", "spec"),
        [
            ("EURUSD_full_1day.txt", "1-DAY-LAST"),
            ("EURUSD_full_1hour.txt", "1-HOUR-LAST"),
            ("EURUSD_full_30min.txt", "30-MINUTE-LAST"),
            ("EURUSD_full_5min.txt", "5-MINUTE-LAST"),
            ("EURUSD_full_1min.txt", "1-MINUTE-LAST"),
        ],
    )
    def test_fx_filenames_infer_their_timeframe(self, name, spec):
        assert _infer_timeframe(name, exact_stem=True) == spec

    def test_default_inference_is_unchanged(self):
        assert _infer_timeframe("EURUSD_full_1hour.txt") == "unknown"
        assert _infer_timeframe("SPY_full_1hour_adjsplitdiv.txt") == "1-HOUR-LAST"

    def test_report_accepts_both_shapes_and_shows_iso_ranges(self, tmp_path):
        _write(tmp_path / "EURUSD_full_1day.txt", ["20100104,1.43,1.44,1.42,1.44,9"])
        _write(
            tmp_path / "EURUSD_full_1hour.txt",
            ["20100103,17:00:00,1.43,1.44,1.42,1.44,9", "20260430,23:00:00,1.17,1.18,1.16,1.17,5"],
        )
        report = build_dry_run_report(tmp_path, AssetClass.FX, include_date_ranges=True)
        assert report.schema_mismatches == []
        assert set(report.timeframes) == {"1-DAY-LAST", "1-HOUR-LAST"}
        rng = report.ticker_date_ranges["EURUSD"]
        assert (rng.earliest, rng.latest) == ("2010-01-03", "2026-04-30")
