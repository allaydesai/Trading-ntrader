"""Crypto M2 (task 2.5): the dry-run and the source probe accept FirstRate crypto files.

Crypto files use the equity row layout but end at the timeframe token
(``BTC_full_1hour.txt``), like FX, and their bars are ``LAST``. The probe reads them as UTC
only when asked (the classifier asks); equity, futures and FX behaviour is unchanged.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.cli.commands.import_data import resolve_timeframes
from src.models.catalog import AssetClass
from src.services.firstrate.dry_run import build_dry_run_report
from src.services.firstrate.source_probe import compute_source_last_date

pytestmark = pytest.mark.unit

ALL_FIVE = ["1-DAY-LAST", "1-HOUR-LAST", "30-MINUTE-LAST", "5-MINUTE-LAST", "1-MINUTE-LAST"]


def _write(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestDryRun:
    def test_report_infers_all_five_timeframes_as_last(self, tmp_path):
        for token in ("1day", "1hour", "30min", "5min", "1min"):
            stamp = "2024-03-10" if token == "1day" else "2024-03-10 02:00:00"
            _write(tmp_path / f"BTC_full_{token}.txt", [f"{stamp},1,1,1,1,0.5"])
        report = build_dry_run_report(tmp_path, AssetClass.CRYPTO, include_date_ranges=True)
        assert report.schema_mismatches == []
        assert set(report.timeframes) == set(ALL_FIVE)

    def test_report_shows_the_utc_date_range(self, tmp_path):
        _write(
            tmp_path / "SOL_full_1day.txt",
            ["2021-03-01,15.1,16,14.9,15.5,1000.5", "2026-05-01,150,151,149,150,9"],
        )
        report = build_dry_run_report(tmp_path, AssetClass.CRYPTO, include_date_ranges=True)
        rng = report.ticker_date_ranges["SOL"]
        assert (rng.earliest, rng.latest) == ("2021-03-01", "2026-05-01")


class TestTimeframes:
    def test_crypto_defaults_to_all_five(self):
        assert resolve_timeframes(None, AssetClass.CRYPTO) == ALL_FIVE

    def test_stock_default_unchanged(self):
        assert resolve_timeframes(None, AssetClass.STOCK) == ["1-DAY-LAST"]


class TestProbe:
    def test_utc_probe_matches_the_parser_stamp(self, tmp_path):
        path = _write(tmp_path / "BTC_full_1hour.txt", ["2026-05-02 23:00:00,1,1,1,1,1"])
        assert compute_source_last_date(path, source_tz=timezone.utc) == datetime(
            2026, 5, 2, 23, tzinfo=timezone.utc
        )
