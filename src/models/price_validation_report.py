"""FMP price-validation report model and pure deviation evaluator.

Compares a ticker's daily catalog closes against an independent FMP
historical-EOD fetch and produces a structured per-ticker and overall
verdict. Sibling to ``comparison_report.py`` (same Pydantic-model-plus-pure-
evaluator shape) but for raw OHLC deviation rather than backtest-metric
deviation — a deliberately separate model tree.
"""

from datetime import date, datetime, timezone
from typing import Any, Final
from zoneinfo import ZoneInfo

import structlog
from nautilus_trader.model.data import Bar
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

_EASTERN: Final = ZoneInfo("America/New_York")

DEFAULT_MEAN_ABS_PCT_TOL: Final[float] = 0.001  # 0.10%
DEFAULT_MAX_ABS_PCT_TOL: Final[float] = 0.01  # 1%
#: ~80% of a 252-trading-day year — guards against a near-empty date
#: intersection (e.g. a timezone bug shifting every date by one day)
#: trivially "passing" on zero real comparisons.
DEFAULT_MIN_MATCHED_DAYS: Final[int] = 200


class PriceDeviationRow(BaseModel):
    """One trading day's catalog-vs-FMP close comparison for one ticker."""

    trade_date: date
    catalog_close: float
    fmp_close: float
    abs_pct_diff: float
    signed_pct_diff: float  # (catalog - fmp) / fmp — surfaces systematic bias direction


class TickerValidationResult(BaseModel):
    """Aggregate verdict for one ticker over the validated window."""

    ticker: str
    instrument_id: str
    window_start: date
    window_end: date
    catalog_bar_count: int
    fmp_bar_count: int
    matched_day_count: int
    unmatched_catalog_dates: list[date] = Field(default_factory=list)
    unmatched_fmp_dates: list[date] = Field(default_factory=list)
    mean_abs_pct_diff: float
    max_abs_pct_diff: float
    max_abs_pct_diff_date: date | None
    mean_tol_passed: bool
    max_tol_passed: bool
    coverage_passed: bool
    passed: bool
    rows: list[PriceDeviationRow] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class PriceValidationReport(BaseModel):
    """Top-level verdict across all validated tickers."""

    generated_at: datetime
    catalog_name: str
    mean_abs_pct_tol: float
    max_abs_pct_tol: float
    min_matched_days: int
    results: list[TickerValidationResult]
    overall_passed: bool
    tickers_passed: list[str]
    tickers_failed: list[str]


def align_catalog_bars_to_dates(bars: list[Bar]) -> dict[date, float]:
    """Map each daily catalog Bar to its America/New_York trading date -> close.

    Inverts FirstRateCsvParser._parse_timestamp's daily-row transform
    (midnight ET localized, then converted to UTC) exactly: convert
    ``ts_event`` (UTC nanoseconds) back to ET and take the date. A duplicate
    date (same calendar date, different ts_event) keeps the first occurrence.
    """
    result: dict[date, float] = {}
    for bar in bars:
        trade_date = (
            datetime.fromtimestamp(bar.ts_event / 1_000_000_000, tz=timezone.utc)
            .astimezone(_EASTERN)
            .date()
        )
        if trade_date in result:
            logger.debug("duplicate_catalog_bar_date", trade_date=trade_date.isoformat())
            continue
        result[trade_date] = bar.close.as_double()
    return result


def parse_fmp_rows_to_dates(rows: list[dict[str, Any]]) -> dict[date, float]:
    """Map raw FMP historical-EOD rows to trading date -> close.

    Rows missing ``date``/``close`` or with an unparseable date are skipped
    and logged rather than raising — a single malformed row from FMP should
    not abort the ticker's comparison.
    """
    result: dict[date, float] = {}
    for row in rows:
        try:
            trade_date = date.fromisoformat(row["date"])
            close = float(row["close"])
        except (KeyError, TypeError, ValueError):
            logger.debug("skipping_malformed_fmp_row", row=row)
            continue
        result[trade_date] = close
    return result


def evaluate_ticker_deviation(
    *,
    ticker: str,
    instrument_id: str,
    catalog_by_date: dict[date, float],
    fmp_by_date: dict[date, float],
    window_start: date,
    window_end: date,
    mean_abs_pct_tol: float = DEFAULT_MEAN_ABS_PCT_TOL,
    max_abs_pct_tol: float = DEFAULT_MAX_ABS_PCT_TOL,
    min_matched_days: int = DEFAULT_MIN_MATCHED_DAYS,
) -> TickerValidationResult:
    """Compute deviation over the date intersection and evaluate tolerances.

    Deliberately compares only ``catalog_by_date.keys() & fmp_by_date.keys()``
    rather than asserting equal date sets — legitimate holiday-calendar
    mismatches between vendors shouldn't false-fail a ticker. The
    ``coverage_passed`` gate is what catches a suspiciously small
    intersection (e.g. zero bars either side, or a systematic date-shift
    bug) from trivially "passing" with a vacuous comparison.
    """
    catalog_dates = set(catalog_by_date)
    fmp_dates = set(fmp_by_date)
    matched = sorted(catalog_dates & fmp_dates)
    unmatched_catalog = sorted(catalog_dates - fmp_dates)
    unmatched_fmp = sorted(fmp_dates - catalog_dates)

    rows: list[PriceDeviationRow] = []
    for trade_date in matched:
        catalog_close = catalog_by_date[trade_date]
        fmp_close = fmp_by_date[trade_date]
        denom = fmp_close if fmp_close != 0 else 1.0
        abs_pct = abs(catalog_close - fmp_close) / denom
        signed_pct = (catalog_close - fmp_close) / denom
        rows.append(
            PriceDeviationRow(
                trade_date=trade_date,
                catalog_close=catalog_close,
                fmp_close=fmp_close,
                abs_pct_diff=abs_pct,
                signed_pct_diff=signed_pct,
            )
        )

    matched_day_count = len(rows)
    coverage_passed = matched_day_count >= min_matched_days

    notes: list[str] = []
    if not coverage_passed:
        notes.append(f"matched_day_count={matched_day_count} < min_matched_days={min_matched_days}")

    if matched_day_count == 0:
        mean_abs = 0.0
        max_abs = 0.0
        max_date: date | None = None
        mean_passed = False
        max_passed = False
        notes.append("zero matched trading days — comparison is vacuous")
    else:
        mean_abs = sum(row.abs_pct_diff for row in rows) / matched_day_count
        worst = max(rows, key=lambda row: row.abs_pct_diff)
        max_abs = worst.abs_pct_diff
        max_date = worst.trade_date
        mean_passed = mean_abs <= mean_abs_pct_tol
        max_passed = max_abs <= max_abs_pct_tol
        if not mean_passed:
            notes.append(f"mean_abs_pct_diff={mean_abs:.4%} > {mean_abs_pct_tol:.2%}")
        if not max_passed:
            notes.append(f"max_abs_pct_diff={max_abs:.4%} > {max_abs_pct_tol:.2%} on {max_date}")

    return TickerValidationResult(
        ticker=ticker,
        instrument_id=instrument_id,
        window_start=window_start,
        window_end=window_end,
        catalog_bar_count=len(catalog_by_date),
        fmp_bar_count=len(fmp_by_date),
        matched_day_count=matched_day_count,
        unmatched_catalog_dates=unmatched_catalog,
        unmatched_fmp_dates=unmatched_fmp,
        mean_abs_pct_diff=mean_abs,
        max_abs_pct_diff=max_abs,
        max_abs_pct_diff_date=max_date,
        mean_tol_passed=mean_passed,
        max_tol_passed=max_passed,
        coverage_passed=coverage_passed,
        passed=coverage_passed and mean_passed and max_passed,
        rows=rows,
        notes=notes,
    )


def build_report(
    results: list[TickerValidationResult],
    *,
    catalog_name: str,
    mean_abs_pct_tol: float,
    max_abs_pct_tol: float,
    min_matched_days: int,
) -> PriceValidationReport:
    """Aggregate per-ticker results into the overall verdict.

    An empty ``results`` list (e.g. every ticker failed to resolve before
    evaluation even ran) is not a vacuous pass — ``overall_passed`` requires
    at least one result and zero failures.
    """
    tickers_passed = [r.ticker for r in results if r.passed]
    tickers_failed = [r.ticker for r in results if not r.passed]
    return PriceValidationReport(
        generated_at=datetime.now(timezone.utc),
        catalog_name=catalog_name,
        mean_abs_pct_tol=mean_abs_pct_tol,
        max_abs_pct_tol=max_abs_pct_tol,
        min_matched_days=min_matched_days,
        results=results,
        overall_passed=bool(results) and not tickers_failed,
        tickers_passed=tickers_passed,
        tickers_failed=tickers_failed,
    )
