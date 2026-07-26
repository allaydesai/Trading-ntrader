"""Unit tests for the FMP price-validation report: pure deviation math,
date alignment, and aggregation. No I/O, no mocks — real Nautilus ``Bar``
objects and plain dicts throughout.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.objects import Price, Quantity

from src.models.price_validation_report import (
    DEFAULT_MAX_ABS_PCT_TOL,
    DEFAULT_MEAN_ABS_PCT_TOL,
    DEFAULT_MIN_MATCHED_DAYS,
    align_catalog_bars_to_dates,
    build_report,
    evaluate_ticker_deviation,
    parse_fmp_rows_to_dates,
)

pytestmark = pytest.mark.unit

_EASTERN = ZoneInfo("America/New_York")


def _bar(instrument_id: str, trade_date: date, close: float) -> Bar:
    """Build a daily Bar whose ts_event is midnight-ET-in-UTC for trade_date.

    Mirrors FirstRateCsvParser._parse_timestamp's daily-row transform exactly
    (localize midnight to America/New_York, convert to UTC) so alignment
    tests exercise the real inverse operation, not a hand-picked constant.
    """
    dt_utc = datetime(
        trade_date.year, trade_date.month, trade_date.day, tzinfo=_EASTERN
    ).astimezone(timezone.utc)
    ts_ns = int(dt_utc.timestamp()) * 1_000_000_000
    bar_type = BarType.from_str(f"{instrument_id}-1-DAY-LAST-EXTERNAL")
    return Bar(
        bar_type=bar_type,
        open=Price(close, precision=2),
        high=Price(close + 1.0, precision=2),
        low=Price(close - 1.0, precision=2),
        close=Price(close, precision=2),
        volume=Quantity(1_000_000, precision=0),
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


class TestAlignCatalogBarsToDates:
    def test_maps_bar_to_correct_et_date(self):
        bars = [_bar("SPY.ARCA", date(2026, 1, 16), 480.0)]  # winter, EST
        result = align_catalog_bars_to_dates(bars)
        assert result == {date(2026, 1, 16): 480.0}

    def test_summer_bar_maps_correctly_despite_edt_offset(self):
        bars = [_bar("SPY.ARCA", date(2026, 7, 16), 500.0)]  # summer, EDT
        result = align_catalog_bars_to_dates(bars)
        assert result == {date(2026, 7, 16): 500.0}

    def test_dst_boundary_bars_map_correctly_on_both_sides(self):
        # 2026 spring-forward is 2026-03-08. One bar just before (EST), one
        # just after (EDT) — both must invert to their own calendar date.
        before = _bar("SPY.ARCA", date(2026, 3, 6), 490.0)
        after = _bar("SPY.ARCA", date(2026, 3, 9), 495.0)
        result = align_catalog_bars_to_dates([before, after])
        assert result == {date(2026, 3, 6): 490.0, date(2026, 3, 9): 495.0}

    def test_duplicate_date_keeps_first(self):
        bar_type = BarType.from_str("SPY.ARCA-1-DAY-LAST-EXTERNAL")
        dt_utc = datetime(2026, 1, 16, tzinfo=_EASTERN).astimezone(timezone.utc)
        ts_ns = int(dt_utc.timestamp()) * 1_000_000_000
        first = Bar(
            bar_type=bar_type,
            open=Price(480.0, precision=2),
            high=Price(481.0, precision=2),
            low=Price(479.0, precision=2),
            close=Price(480.0, precision=2),
            volume=Quantity(1_000_000, precision=0),
            ts_event=ts_ns,
            ts_init=ts_ns,
        )
        duplicate = Bar(
            bar_type=bar_type,
            open=Price(999.0, precision=2),
            high=Price(1000.0, precision=2),
            low=Price(998.0, precision=2),
            close=Price(999.0, precision=2),
            volume=Quantity(1_000_000, precision=0),
            ts_event=ts_ns + 1,  # different ts_event, same ET calendar date
            ts_init=ts_ns + 1,
        )
        result = align_catalog_bars_to_dates([first, duplicate])
        assert result == {date(2026, 1, 16): 480.0}

    def test_empty_bars_returns_empty_dict(self):
        assert align_catalog_bars_to_dates([]) == {}


class TestParseFmpRowsToDates:
    def test_valid_rows_parsed(self):
        rows = [
            {"symbol": "SPY", "date": "2026-07-24", "close": 738.93},
            {"symbol": "SPY", "date": "2026-07-23", "close": 738.18},
        ]
        result = parse_fmp_rows_to_dates(rows)
        assert result == {date(2026, 7, 24): 738.93, date(2026, 7, 23): 738.18}

    def test_missing_close_skipped(self):
        rows = [{"date": "2026-07-24"}]
        assert parse_fmp_rows_to_dates(rows) == {}

    def test_malformed_date_skipped(self):
        rows = [{"date": "not-a-date", "close": 100.0}]
        assert parse_fmp_rows_to_dates(rows) == {}

    def test_empty_rows_returns_empty_dict(self):
        assert parse_fmp_rows_to_dates([]) == {}

    def test_one_bad_row_does_not_drop_good_rows(self):
        rows = [
            {"date": "2026-07-24", "close": 738.93},
            {"date": "bad", "close": 1.0},
            {"date": "2026-07-23", "close": 738.18},
        ]
        result = parse_fmp_rows_to_dates(rows)
        assert result == {date(2026, 7, 24): 738.93, date(2026, 7, 23): 738.18}


def _dates(n: int, start: date = date(2026, 1, 1)) -> list[date]:
    """Return n consecutive calendar dates starting from start."""
    return [start + timedelta(days=i) for i in range(n)]


def _evaluate(catalog: dict, fmp: dict, **overrides):
    defaults = dict(
        ticker="SPY",
        instrument_id="SPY.ARCA",
        catalog_by_date=catalog,
        fmp_by_date=fmp,
        window_start=date(2025, 7, 26),
        window_end=date(2026, 7, 24),
    )
    defaults.update(overrides)
    return evaluate_ticker_deviation(**defaults)


class TestEvaluateTickerDeviation:
    def test_identical_prices_all_pass(self):
        by_date = {date(2026, 7, d): 100.0 + d for d in range(1, 21)}
        result = _evaluate(by_date, dict(by_date))

        assert result.matched_day_count == 20
        assert result.mean_abs_pct_diff == 0.0
        assert result.max_abs_pct_diff == 0.0
        assert result.coverage_passed is False  # 20 < DEFAULT_MIN_MATCHED_DAYS=200
        assert result.mean_tol_passed
        assert result.max_tol_passed
        assert not result.passed  # coverage gate still blocks the overall verdict

    def test_identical_prices_pass_with_relaxed_coverage(self):
        by_date = {date(2026, 7, d): 100.0 + d for d in range(1, 21)}
        result = _evaluate(by_date, dict(by_date), min_matched_days=10)

        assert result.coverage_passed
        assert result.passed

    def test_single_day_outlier_fails_max_but_not_mean(self):
        dates = _dates(200)
        catalog = {d: 100.0 for d in dates}
        fmp = dict(catalog)
        fmp[dates[49]] = 103.0  # 3% outlier on one day out of 200

        result = _evaluate(catalog, fmp, min_matched_days=200)

        assert result.coverage_passed
        assert not result.max_tol_passed
        assert result.max_abs_pct_diff_date == dates[49]
        assert result.mean_tol_passed  # one outlier over 200 days barely moves the mean
        assert not result.passed

    def test_uniform_small_drift_passes_mean(self):
        catalog = {d: 100.0 for d in _dates(200)}
        fmp = {d: v * 1.0005 for d, v in catalog.items()}  # 0.05% uniform drift

        result = _evaluate(catalog, fmp)

        assert result.mean_tol_passed
        assert result.max_tol_passed
        assert result.passed

    def test_uniform_large_drift_fails_mean(self):
        catalog = {d: 100.0 for d in _dates(200)}
        fmp = {d: v * 1.0015 for d, v in catalog.items()}  # 0.15% uniform drift

        result = _evaluate(catalog, fmp)

        assert not result.mean_tol_passed
        assert not result.passed

    def test_exact_boundary_values_pass_inclusive(self):
        # 200 days at 0 deviation, 1 day where catalog is exactly
        # max_abs_pct_tol above the (fixed) fmp reference — denom is fmp_close,
        # so this is the exact boundary value, not an approximation.
        dates = _dates(200)
        fmp = {d: 100.0 for d in dates}
        catalog = dict(fmp)
        catalog[dates[0]] = 100.0 * (1 + DEFAULT_MAX_ABS_PCT_TOL)

        result = _evaluate(catalog, fmp)

        assert result.max_abs_pct_diff == pytest.approx(DEFAULT_MAX_ABS_PCT_TOL)
        assert result.max_tol_passed  # inclusive <=

    def test_near_empty_intersection_fails_coverage_even_if_agreeing(self):
        catalog = {d: 100.0 for d in _dates(5)}  # 5 days
        fmp = dict(catalog)  # perfectly agreeing on those 5 days

        result = _evaluate(catalog, fmp)

        assert result.matched_day_count == 5
        assert result.mean_abs_pct_diff == 0.0
        assert not result.coverage_passed
        assert not result.passed  # vacuous-pass guard: 5/5 agreement isn't enough evidence

    def test_zero_bars_either_side_hard_fails(self):
        result = _evaluate({}, {date(2026, 1, 1): 100.0})
        assert result.matched_day_count == 0
        assert not result.coverage_passed
        assert not result.passed

        result2 = _evaluate({date(2026, 1, 1): 100.0}, {})
        assert result2.matched_day_count == 0
        assert not result2.passed

    def test_unmatched_dates_populated_and_excluded_from_math(self):
        dates = _dates(200)
        catalog_only_date = date(2027, 1, 1)
        fmp_only_date = date(2027, 1, 2)
        catalog = {d: 100.0 for d in dates}
        catalog[catalog_only_date] = 999.0  # catalog-only date, wildly different price
        fmp = {d: 100.0 for d in dates}
        fmp[fmp_only_date] = 1.0  # fmp-only date

        result = _evaluate(catalog, fmp)

        assert result.unmatched_catalog_dates == [catalog_only_date]
        assert result.unmatched_fmp_dates == [fmp_only_date]
        assert result.matched_day_count == 200
        assert result.mean_abs_pct_diff == 0.0  # the outlier dates never enter the math

    def test_notes_explain_every_failure_mode(self):
        result = _evaluate({}, {})
        assert result.notes  # never silently fail with no explanation


class TestBuildReport:
    def test_mixed_pass_fail_aggregation(self):
        passing = _evaluate(
            {d: 100.0 for d in _dates(200)},
            {d: 100.0 for d in _dates(200)},
            ticker="SPY",
        )
        failing = _evaluate({}, {}, ticker="ARKK", instrument_id="ARKK.BATS")

        report = build_report(
            [passing, failing],
            catalog_name="firstrate-etf",
            mean_abs_pct_tol=DEFAULT_MEAN_ABS_PCT_TOL,
            max_abs_pct_tol=DEFAULT_MAX_ABS_PCT_TOL,
            min_matched_days=DEFAULT_MIN_MATCHED_DAYS,
        )

        assert report.tickers_passed == ["SPY"]
        assert report.tickers_failed == ["ARKK"]
        assert not report.overall_passed
        assert report.catalog_name == "firstrate-etf"

    def test_all_pass_is_overall_pass(self):
        passing = _evaluate(
            {d: 100.0 for d in _dates(200)},
            {d: 100.0 for d in _dates(200)},
        )
        report = build_report(
            [passing],
            catalog_name="firstrate-etf",
            mean_abs_pct_tol=DEFAULT_MEAN_ABS_PCT_TOL,
            max_abs_pct_tol=DEFAULT_MAX_ABS_PCT_TOL,
            min_matched_days=DEFAULT_MIN_MATCHED_DAYS,
        )
        assert report.overall_passed

    def test_empty_results_is_not_a_vacuous_pass(self):
        report = build_report(
            [],
            catalog_name="firstrate-etf",
            mean_abs_pct_tol=DEFAULT_MEAN_ABS_PCT_TOL,
            max_abs_pct_tol=DEFAULT_MAX_ABS_PCT_TOL,
            min_matched_days=DEFAULT_MIN_MATCHED_DAYS,
        )
        assert not report.overall_passed

    def test_generated_at_is_utc(self):
        report = build_report(
            [],
            catalog_name="firstrate-etf",
            mean_abs_pct_tol=DEFAULT_MEAN_ABS_PCT_TOL,
            max_abs_pct_tol=DEFAULT_MAX_ABS_PCT_TOL,
            min_matched_days=DEFAULT_MIN_MATCHED_DAYS,
        )
        assert report.generated_at.tzinfo is not None

    def test_report_serialization_round_trips(self):
        passing = _evaluate(
            {d: 100.0 for d in _dates(200)},
            {d: 100.0 for d in _dates(200)},
        )
        report = build_report(
            [passing],
            catalog_name="firstrate-etf",
            mean_abs_pct_tol=DEFAULT_MEAN_ABS_PCT_TOL,
            max_abs_pct_tol=DEFAULT_MAX_ABS_PCT_TOL,
            min_matched_days=DEFAULT_MIN_MATCHED_DAYS,
        )
        from src.models.price_validation_report import PriceValidationReport

        rebuilt = PriceValidationReport.model_validate_json(report.model_dump_json())
        assert rebuilt.overall_passed == report.overall_passed
        assert rebuilt.tickers_passed == report.tickers_passed
