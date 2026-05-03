"""AAPL 2018 1-MINUTE reference comparison harness (Story 3.3).

Runs ``sma_crossover`` against the same logical dataset (AAPL 2018-01-01 →
2018-12-31, 1-MINUTE) loaded via two different paths:

- **Legacy CSV path:** ``data/AAPL_1min.csv`` filtered to 2018, imported via
  ``CSVLoader`` into an isolated harness-owned catalog under ``output_dir``,
  backtested via that catalog.
- **FirstRate path:** AAPL Stocks bundle (already imported into the
  ``e2e-test`` catalog by Story 1-x), backtested via the named-catalog
  loader.

The legacy path uses an **isolated catalog directory**, not the default
``NAUTILUS_PATH``, because in this project `NAUTILUS_PATH` is aliased to the
``e2e-test`` catalog directory (see ``.env``). Writing the legacy CSV into
``NAUTILUS_PATH`` would trample the FirstRate AAPL bars and make the
comparison meaningless.

Both runs use byte-identical strategy params and time windows. The harness
compares totals (bars / trades / PnL) against agreed Phase 1 tolerances and
emits a structured JSON report plus a side-by-side rich.Table summary.

Usage::

    uv run python scripts/verify_aapl_2018_reference.py [options]

Exits 0 on full pass, 1 on any tolerance breach.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import click
import pandas as pd
import structlog
from rich.console import Console

from src.cli.commands._backtest_helpers import load_backtest_data
from src.core.backtest_orchestrator import BacktestOrchestrator
from src.models.backtest_request import BacktestRequest
from src.models.comparison_report import (
    BacktestResultSummary,
    ComparisonReport,
    evaluate_tolerance,
)
from src.models.data_load_result import DataLoadResult
from src.services.comparison_renderer import render_comparison_table
from src.services.csv_loader import CSVLoader
from src.services.data_catalog import DataCatalogService
from src.services.firstrate.backtest_loader import build_equity

logger = structlog.get_logger(__name__)

# AC #7 — these values are the canonical reference comparison inputs.
# Both runs MUST use them byte-identically.
REFERENCE_START = datetime(2018, 1, 1, tzinfo=timezone.utc)
REFERENCE_END = datetime(2018, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
INITIAL_BALANCE = Decimal("1000000")
SYMBOL = "AAPL"
VENUE = "NASDAQ"
INSTRUMENT_ID = f"{SYMBOL}.{VENUE}"
TIMEFRAME_SPEC = "1-MINUTE-LAST"
STRATEGY = "sma_crossover"
STRATEGY_PARAMS = {
    "fast_period": 10,
    "slow_period": 20,
    "position_size_pct": Decimal("10"),
}
DATASET = f"{SYMBOL}_2018_{TIMEFRAME_SPEC.split('-LAST')[0]}"


def filter_csv_to_2018(src: Path, dst: Path) -> Path:
    """Filter a 1-minute CSV down to calendar 2018.

    ``data/AAPL_1min.csv`` ships with ~1.6M rows from 2010-01-04 onward; we
    only need the ~98K rows for 2018 to compare against the FirstRate
    catalog.
    """
    df = pd.read_csv(src, parse_dates=["timestamp"])
    # Half-open interval keeps any sub-second bars at 23:59:59.x in 2018,
    # matching REFERENCE_END=2018-12-31T23:59:59.999999 (AC #7 byte-identical
    # window). String "<= 2018-12-31 23:59:59" parsed as .000000 µs would
    # silently drop those bars on higher-resolution datasets.
    mask = (df["timestamp"] >= "2018-01-01") & (df["timestamp"] < "2019-01-01")
    filtered = df.loc[mask].copy()
    if len(filtered) == 0:
        raise ValueError(f"No 2018 rows in {src} — verify the CSV covers 2018-01-01..2018-12-31")
    dst.parent.mkdir(parents=True, exist_ok=True)
    filtered.to_csv(dst, index=False)
    logger.info("csv_filtered_to_2018", src=str(src), dst=str(dst), rows=len(filtered))
    return dst


async def _import_legacy_csv(*, filtered_csv: Path, catalog_service: DataCatalogService) -> dict:
    """Import the filtered CSV into the harness-owned catalog."""
    loader = CSVLoader(catalog_service=catalog_service, conflict_mode="overwrite")
    return await loader.load_file(filtered_csv, SYMBOL, VENUE, TIMEFRAME_SPEC)


def _build_request(*, catalog_name: str | None) -> BacktestRequest:
    """Build a BacktestRequest with the canonical parameters (AC #7)."""
    return BacktestRequest.from_cli_args(
        strategy=STRATEGY,
        symbol=SYMBOL,
        start=REFERENCE_START,
        end=REFERENCE_END,
        bar_type_spec=TIMEFRAME_SPEC,
        persist=False,  # harness uses orchestrator output, not the DB
        starting_balance=INITIAL_BALANCE,
        data_source="catalog",
        catalog_name=catalog_name,
        **STRATEGY_PARAMS,
    )


async def _run_legacy_backtest(
    *, catalog_service: DataCatalogService, console: Console
) -> BacktestResultSummary:
    """Run the legacy-CSV-path backtest using the harness-owned catalog.

    Bypasses ``load_backtest_data`` because that helper's
    ``covers_range(start, end)`` check fails when the catalog's first bar is
    2018-01-02 09:30 (market open) but the requested window starts at the
    New Year's Day calendar boundary. That false-negative would route the
    request through IBKR auto-fetch — and in the harness IBKR is not
    configured, so the call would hang indefinitely on connection retries.
    Instead we mirror the FirstRate path: read bars directly from the
    catalog and synthesise the instrument from ``bars[0]``.
    """
    request = _build_request(catalog_name=None)

    bar_type_str = f"{request.instrument_id}-{request.bar_type}-EXTERNAL"
    bars = await asyncio.to_thread(
        catalog_service.catalog.bars,
        bar_types=[bar_type_str],
        start=request.start_date,
        end=request.end_date,
    )

    if not bars:
        raise RuntimeError(
            f"Legacy catalog at {catalog_service.catalog_path} returned zero "
            f"bars for {bar_type_str} between {request.start_date} and "
            f"{request.end_date}. Did the CSV import run?"
        )

    equity = build_equity(
        nautilus_id=request.instrument_id,
        ticker=SYMBOL,
        bars=bars,
    )
    data_result = DataLoadResult(
        bars=bars,
        instrument=equity,
        data_source_used=f"Legacy CSV catalog ({catalog_service.catalog_path})",
    )

    return await _execute_and_summarise(request=request, data_result=data_result)


async def _run_firstrate_backtest(
    *, firstrate_catalog: str, console: Console
) -> BacktestResultSummary:
    """Run the FirstRate-named-catalog backtest."""
    request = _build_request(catalog_name=firstrate_catalog)

    data_result = await load_backtest_data(
        data_source="catalog",
        instrument_id=request.instrument_id,
        bar_type_spec=request.bar_type,
        start=request.start_date,
        end=request.end_date,
        console=console,
        catalog_name=firstrate_catalog,
    )

    return await _execute_and_summarise(request=request, data_result=data_result)


async def _execute_and_summarise(*, request, data_result) -> BacktestResultSummary:
    """Run a single backtest with proper engine disposal and return its summary."""
    orchestrator = BacktestOrchestrator()
    try:
        result, _run_id = await orchestrator.execute(
            request, data_result.bars, data_result.instrument
        )
    finally:
        # Single-use engine — must dispose before the next run starts
        # (CLAUDE.md Gotcha #4).
        orchestrator.dispose()

    return BacktestResultSummary(
        total_trades=int(result.total_trades),
        total_pnl=float(result.total_pnl) if result.total_pnl is not None else 0.0,
        total_pnl_percentage=(
            float(result.total_pnl_percentage) if result.total_pnl_percentage is not None else 0.0
        ),
        final_balance=float(result.final_balance),
        bar_count=len(data_result.bars),
        instrument_id=str(data_result.instrument.id),
        data_source=data_result.data_source_used,
    )


async def _run_comparison_async(
    *,
    legacy_csv: Path,
    firstrate_catalog: str,
    output_dir: Path,
    skip_import: bool,
) -> ComparisonReport:
    """Async core for the comparison harness (extracted for testability)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    # Pre-flight: verify the output dir is writable. Raises OSError on failure
    # so we fail fast before the expensive backtest runs.
    probe = output_dir / ".write_probe"
    probe.write_text("ok")
    probe.unlink()

    console = Console(quiet=True)

    # Isolated, harness-owned catalog for the legacy-CSV path. Writing into
    # NAUTILUS_PATH would trample the FirstRate catalog when the two are
    # aliased (this project's `.env` does exactly that).
    legacy_catalog_dir = output_dir / "legacy_catalog"
    legacy_catalog_dir.mkdir(parents=True, exist_ok=True)
    legacy_catalog_service = DataCatalogService(catalog_path=str(legacy_catalog_dir))

    if not skip_import:
        filtered = filter_csv_to_2018(legacy_csv, output_dir / "AAPL_2018.csv")
        await _import_legacy_csv(
            filtered_csv=filtered,
            catalog_service=legacy_catalog_service,
        )
        # Rebuild the cache so the post-import data is visible.
        legacy_catalog_service._rebuild_availability_cache()

    # AC #7 guard — both requests must differ only by catalog_name.
    # `_build_request` is the single source of truth, but a future helper
    # divergence (or a regression in `from_cli_args` silently dropping a
    # kwarg) would go unnoticed without this assertion.
    _legacy_req = _build_request(catalog_name=None)
    _fr_req = _build_request(catalog_name=firstrate_catalog)
    _diff = {
        k
        for k in _legacy_req.model_dump()
        if k != "catalog_name" and _legacy_req.model_dump()[k] != _fr_req.model_dump()[k]
    }
    if _diff:
        raise AssertionError(
            f"AC #7 violated: BacktestRequests diverge on fields {sorted(_diff)} "
            "beyond catalog_name. The shared _build_request helper has drifted."
        )

    # Sequential — never run two BacktestEngines concurrently in one process
    # (CLAUDE.md Gotcha #4).
    legacy_summary = await _run_legacy_backtest(
        catalog_service=legacy_catalog_service, console=console
    )
    firstrate_summary = await _run_firstrate_backtest(
        firstrate_catalog=firstrate_catalog, console=console
    )

    report = evaluate_tolerance(legacy_summary, firstrate_summary, dataset=DATASET)

    json_path = output_dir / "aapl_2018_comparison.json"
    json_path.write_text(report.model_dump_json(indent=2))

    return report


def run_comparison_harness(
    *,
    legacy_csv: Path,
    firstrate_catalog: str,
    output_dir: Path,
    skip_import: bool,
) -> ComparisonReport:
    """Synchronous entry point for the comparison harness.

    Wraps the async core with ``asyncio.run`` so callers (integration tests,
    the CLI ``main()``) don't need to manage an event loop. Pre-flight write
    check raises OSError before the expensive backtests run.
    """
    return asyncio.run(
        _run_comparison_async(
            legacy_csv=legacy_csv,
            firstrate_catalog=firstrate_catalog,
            output_dir=output_dir,
            skip_import=skip_import,
        )
    )


@click.command()
@click.option(
    "--legacy-csv",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=Path("data/AAPL_1min.csv"),
    help="Legacy reference CSV (filtered to 2018 internally).",
)
@click.option(
    "--firstrate-catalog",
    type=str,
    default="e2e-test",
    help="Named FirstRate catalog containing AAPL 1-MINUTE bars.",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("/tmp/story-3-3-evidence/"),
    help="Directory for evidence JSON + filtered CSV + isolated legacy catalog.",
)
@click.option(
    "--skip-import",
    is_flag=True,
    help="Skip the legacy-CSV → harness-catalog re-import (idempotency hint).",
)
def main(
    legacy_csv: Path,
    firstrate_catalog: str,
    output_dir: Path,
    skip_import: bool,
) -> None:
    """Drive the AAPL 2018 reference comparison end-to-end."""
    console = Console()
    report = run_comparison_harness(
        legacy_csv=legacy_csv,
        firstrate_catalog=firstrate_catalog,
        output_dir=output_dir,
        skip_import=skip_import,
    )
    console.print(render_comparison_table(report))
    console.print(f"\nEvidence written to: {output_dir / 'aapl_2018_comparison.json'}")
    sys.exit(0 if report.overall_passed else 1)


if __name__ == "__main__":
    main()
