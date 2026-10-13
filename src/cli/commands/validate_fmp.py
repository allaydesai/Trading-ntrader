"""CLI command: validate imported ETF daily prices against FMP historical EOD data.

Defined in its own module (not inline in ``catalog.py``, already near its
size budget) and attached to the ``catalog`` group from there — the same
technique ``main.py`` uses for ``import_firstrate``.
"""

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import click
from dateutil.relativedelta import relativedelta
from rich.console import Console
from sqlalchemy.exc import SQLAlchemyError

from src.config import CatalogSettings, get_settings
from src.db.exceptions import DatabaseConnectionError
from src.db.repositories.catalog_instrument_repository import SyncCatalogInstrumentRepository
from src.db.session_sync import get_sync_session
from src.models.price_validation_report import (
    DEFAULT_MAX_ABS_PCT_TOL,
    DEFAULT_MEAN_ABS_PCT_TOL,
    DEFAULT_MIN_MATCHED_DAYS,
    TickerValidationResult,
    align_catalog_bars_to_dates,
    build_report,
    evaluate_ticker_deviation,
    parse_fmp_rows_to_dates,
)
from src.services.data_catalog import DataCatalogService
from src.services.exceptions import DataNotFoundError
from src.services.metadata.fmp_client import FMPClient
from src.services.price_validation_renderer import render_price_validation_table

console = Console()

DEFAULT_TICKERS: tuple[str, ...] = ("SPY", "QQQ", "IWM", "GLD", "XLF", "TLT", "VTI", "ARKK")

_DAILY_BAR_SPEC = "1-DAY-LAST"


def _resolve_tickers(tickers: Optional[str]) -> list[str]:
    """Return the comma-separated override, upper-cased, or the default 8."""
    if not tickers:
        return list(DEFAULT_TICKERS)
    return [t.strip().upper() for t in tickers.split(",") if t.strip()]


def _hard_fail(
    ticker: str, instrument_id: str, window_start: date, window_end: date, reason: str
) -> TickerValidationResult:
    """Build a failing result for a ticker that never reached deviation math."""
    return TickerValidationResult(
        ticker=ticker,
        instrument_id=instrument_id,
        window_start=window_start,
        window_end=window_end,
        catalog_bar_count=0,
        fmp_bar_count=0,
        matched_day_count=0,
        mean_abs_pct_diff=0.0,
        max_abs_pct_diff=0.0,
        max_abs_pct_diff_date=None,
        mean_tol_passed=False,
        max_tol_passed=False,
        coverage_passed=False,
        passed=False,
        notes=[reason],
    )


def _validate_ticker(
    ticker: str,
    *,
    repo: SyncCatalogInstrumentRepository,
    catalog_service: DataCatalogService,
    fmp_client: FMPClient,
    catalog_name: str,
    months: int,
    mean_tol: float,
    max_tol: float,
    min_matched_days: int,
    today: date,
) -> TickerValidationResult:
    """Resolve, fetch, and evaluate one ticker; never raises for a data-availability gap."""
    row = repo.get_by_ticker(catalog_name, ticker)
    if row is None or row.nautilus_id is None:
        return _hard_fail(ticker, ticker, today, today, "ticker not resolved in catalog")

    anchor = min(row.date_range_end_daily.date(), today) if row.date_range_end_daily else today
    window_start = anchor - relativedelta(months=months)

    try:
        bars = catalog_service.query_bars(
            row.nautilus_id,
            datetime.combine(window_start, datetime.min.time(), tzinfo=timezone.utc),
            datetime.combine(anchor, datetime.max.time(), tzinfo=timezone.utc),
            _DAILY_BAR_SPEC,
        )
    except DataNotFoundError:
        return _hard_fail(
            ticker, row.nautilus_id, window_start, anchor, "catalog returned zero bars for window"
        )

    fmp_rows = fmp_client.fetch_historical_eod(ticker, window_start, anchor)
    if fmp_rows is None:
        return _hard_fail(
            ticker, row.nautilus_id, window_start, anchor, "FMP fetch degraded (see logs)"
        )
    if not fmp_rows:
        return _hard_fail(
            ticker, row.nautilus_id, window_start, anchor, "FMP returned zero rows for window"
        )

    return evaluate_ticker_deviation(
        ticker=ticker,
        instrument_id=row.nautilus_id,
        catalog_by_date=align_catalog_bars_to_dates(bars),
        fmp_by_date=parse_fmp_rows_to_dates(fmp_rows),
        window_start=window_start,
        window_end=anchor,
        mean_abs_pct_tol=mean_tol,
        max_abs_pct_tol=max_tol,
        min_matched_days=min_matched_days,
    )


@click.command("validate-fmp")
@click.option("--catalog", "catalog_name", default=None, help="Named catalog to validate.")
@click.option("--tickers", default=None, help="Comma-separated ticker override.")
@click.option("--months", default=12, show_default=True, help="Lookback window length in months.")
@click.option(
    "--mean-tol-pct",
    default=DEFAULT_MEAN_ABS_PCT_TOL * 100,
    show_default=True,
    help="Mean abs %% diff tolerance (percentage points).",
)
@click.option(
    "--max-tol-pct",
    default=DEFAULT_MAX_ABS_PCT_TOL * 100,
    show_default=True,
    help="Max single-day abs %% diff tolerance (percentage points).",
)
@click.option(
    "--min-matched-days",
    default=DEFAULT_MIN_MATCHED_DAYS,
    show_default=True,
    help="Coverage floor for a valid comparison.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path),
    default=None,
    help="JSON evidence output path.",
)
def validate_fmp(
    catalog_name: Optional[str],
    tickers: Optional[str],
    months: int,
    mean_tol_pct: float,
    max_tol_pct: float,
    min_matched_days: int,
    output_path: Optional[Path],
) -> None:
    """Validate imported daily ETF closes against FMP historical EOD prices.

    Spot-checks each ticker's daily catalog closes against an independent
    FMP fetch over the trailing window, anchored to the ticker's latest
    imported daily bar. A ticker passes only if the matched-day coverage,
    mean deviation, and max single-day deviation all clear their tolerances.
    """
    settings = get_settings()
    name = catalog_name or settings.firstrate.firstrate_catalog_name
    ticker_list = _resolve_tickers(tickers)
    mean_tol = mean_tol_pct / 100.0
    max_tol = max_tol_pct / 100.0
    today = datetime.now(timezone.utc).date()

    try:
        with get_sync_session() as session:
            repo = SyncCatalogInstrumentRepository(session)
            catalog_root = Path(CatalogSettings().catalog_base_path) / name
            catalog_service = DataCatalogService(catalog_path=catalog_root)
            with FMPClient() as fmp_client:
                results = [
                    _validate_ticker(
                        ticker,
                        repo=repo,
                        catalog_service=catalog_service,
                        fmp_client=fmp_client,
                        catalog_name=name,
                        months=months,
                        mean_tol=mean_tol,
                        max_tol=max_tol,
                        min_matched_days=min_matched_days,
                        today=today,
                    )
                    for ticker in ticker_list
                ]
    except (RuntimeError, DatabaseConnectionError, SQLAlchemyError) as exc:
        console.print(f"[yellow]Metadata DB not available — {exc}[/yellow]")
        raise SystemExit(2) from exc

    report = build_report(
        results,
        catalog_name=name,
        mean_abs_pct_tol=mean_tol,
        max_abs_pct_tol=max_tol,
        min_matched_days=min_matched_days,
    )

    console.print(render_price_validation_table(report))

    if output_path is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = Path("logs/fmp-validation") / f"validate-fmp-{stamp}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.model_dump_json(indent=2))
    console.print(f"\nEvidence written to: {output_path}")

    raise SystemExit(0 if report.overall_passed else 1)
