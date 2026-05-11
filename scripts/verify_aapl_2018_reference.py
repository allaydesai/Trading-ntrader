"""AAPL 2018 1-MINUTE parity comparison harness (Story 3.4).

Runs ``sma_crossover`` against the same logical dataset (AAPL 2018-01-01 →
2018-12-31, 1-MINUTE-LAST) loaded via two independently-trusted paths:

- **IBKR path:** AAPL 2018 fetched from IBKR via
  ``DataCatalogService.fetch_or_load`` into an isolated
  ``output_dir/ibkr_catalog/``. Default ``useRTH=True`` returns Regular
  Trading Hours bars only (~98K).
- **FirstRate path:** AAPL Stocks bundle (already imported into the named
  ``e2e-test`` catalog by Story 1-x), loaded via the named-catalog loader.
  FirstRate intraday includes pre/post-market — the harness filters in
  memory to the IBKR timestamp set so both engines see the same bars.

Key data semantics:

- IBKR ``whatToShow=TRADES`` returns as-traded prices (no split adjustment).
  FirstRate Stocks bundles are split-adjusted retroactively. AAPL had a
  4:1 split on 2020-08-31 which post-dates the 2018 window — FirstRate
  2018 prices are therefore ~$40 (back-adjusted), IBKR 2018 prices ~$160.
  The strategy uses ``position_size_pct=10``, so notional sizing makes
  trade timing scale-invariant. Total dollar PnL should match within
  the agreed 0.1% tolerance, modulo whole-share quantization.
- Both runs use ``useRTH=True`` semantics; FirstRate is filtered in
  memory to the IBKR timestamp set so DST transitions, half-day
  Wednesdays, and NYSE holiday calendars align exactly.

Both runs use byte-identical strategy params and time windows. The
harness compares totals (bars / trades / PnL) against agreed Phase 1
tolerances and emits a structured JSON report plus a side-by-side
rich.Table summary.

Note: ``ComparisonReport.legacy_metrics`` carries the IBKR summary in
this story; the field name is inherited from Story 3.3's ``Path A`` and
not renamed per Story 3.4 scope ("Reuse 3.3's framework unchanged").
The renderer's "Legacy CSV" column header is similarly unchanged — the
harness prints a clarifying line above the table.

Usage::

    uv run python scripts/verify_aapl_2018_reference.py [options]

Exits 0 on full pass, 1 on any tolerance breach.

The matching pytest harness lives at
``tests/integration/core/test_aapl_2018_ibkr_vs_firstrate.py`` (Story 3.4).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import click
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
from src.services.data_catalog import DataCatalogService
from src.services.exceptions import DataNotFoundError
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
DATASET = f"{SYMBOL}_2018_{TIMEFRAME_SPEC.split('-LAST')[0]}_IBKR_vs_FirstRate"

# IBKR fetch_or_load uses useRTH=True by default; FirstRate intraday includes
# pre/post-market. The harness filters FirstRate to IBKR's timestamp set so
# both engines run on the same bar set.
USE_RTH = True

# IBKR fetch chunk size for 1-MINUTE bars. The upstream Nautilus
# `_calculate_duration_segments` doesn't honor IBKR's per-request
# duration limits per bar size; passing a 364-day window for 1-min bars
# silently truncates to ~1 day. Empirically a 30-day window returns the
# full RTH bar count (~11K-13K bars per chunk), so we segment in the
# harness and reconstitute via the catalog. See Story 3.4 Run 2 notes.
IBKR_CHUNK_DAYS = 30


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


#: Maximum tolerated divergence between RAW IBKR and RAW FirstRate bar counts
#: at pre-flight time. With ``useRTH=True`` IBKR returns ~98K RTH bars while
#: FirstRate returns ~166K full-session bars (RTH + pre/post-market) for
#: AAPL 2018 — that's a ~41% Δ. The tolerance is sized to allow this
#: structural feed-shape difference while still catching real divergences
#: (partial import, useRTH semantic flip, year-window mismatch).
PRE_FLIGHT_RAW_TOL: float = 0.50


def _assert_pre_flight_alignment(
    *,
    ibkr_raw_count: int,
    firstrate_raw_count: int,
    firstrate_metadata_count: int | None,
    use_rth: bool,
    bar_count_tol: float = PRE_FLIGHT_RAW_TOL,
) -> None:
    """Halt the run before the expensive backtests if RAW bar counts diverge wildly.

    This check fires BEFORE the symmetric timestamp intersection — comparing
    post-intersection counts is a tautology (both sides equal by construction).
    Raw counts surface real ingest-shape mismatches: missing months in the
    FirstRate import, an IBKR fetch that hit a permission gate, a useRTH flag
    flip on either side.

    Logs the four-way comparison (IBKR raw, FirstRate raw, FirstRate metadata,
    useRTH flag) so divergence sources are visible in the evidence stream.
    Raises ``RuntimeError`` if the raw counts diverge by more than
    ``bar_count_tol``. The metadata count is logged but not used in the
    delta — metadata is whole-catalog while the comparison runs on a
    single year window.
    """
    bar_max = max(ibkr_raw_count, firstrate_raw_count, 1)
    delta = abs(ibkr_raw_count - firstrate_raw_count) / bar_max

    msg = (
        f"IBKR raw bars: {ibkr_raw_count:,} (useRTH={use_rth}) "
        f"vs FirstRate raw bars: {firstrate_raw_count:,} "
        f"(metadata total: {firstrate_metadata_count}, Δ={delta:.4%})"
    )
    logger.info(
        "pre_flight_alignment",
        ibkr_raw_count=ibkr_raw_count,
        firstrate_raw_count=firstrate_raw_count,
        firstrate_metadata_count=firstrate_metadata_count,
        use_rth=use_rth,
        bar_count_delta=delta,
        message=msg,
    )

    if delta > bar_count_tol:
        raise RuntimeError(
            f"Pre-flight raw bar-count alignment failed: {msg}. "
            f"Expected raw divergence ≤ {bar_count_tol:.2%}. "
            "Likely causes: useRTH flag mismatch, FirstRate import gap "
            "(year-window partial), or NYSE holiday calendar drift. "
            "Investigate before re-running."
        )


def _filter_to_timestamp_set(bars, timestamp_set: frozenset[int]):
    """Return the subset of ``bars`` whose ``ts_event`` is in ``timestamp_set``.

    Nautilus Bar objects expose ``ts_event`` as an integer (nanoseconds
    since Unix epoch, UTC). Set intersection is O(n) and handles DST
    transitions, half-day sessions (e.g., the Wednesday before
    Thanksgiving), and NYSE holiday calendars correctly without
    re-implementing market-session logic.
    """
    return [bar for bar in bars if bar.ts_event in timestamp_set]


def _dedup_by_ts_event(bars):
    """Return ``bars`` deduplicated by ``ts_event``, keeping the first occurrence.

    The chunked IBKR fetch can leave the catalog with duplicate bars at
    chunk boundaries when IBKR extends a request slightly beyond the
    asked-for window (e.g., to align with the previous trading session).
    Re-reading via ``query_bars`` returns both copies; the strategy
    would then process the same minute twice.
    """
    seen: set[int] = set()
    result = []
    for bar in bars:
        if bar.ts_event in seen:
            continue
        seen.add(bar.ts_event)
        result.append(bar)
    return result


def _iter_chunks(start: datetime, end: datetime, chunk_days: int):
    """Yield midnight-aligned ``(chunk_start, chunk_end)`` covering ``[start, end]``.

    **Why midnight alignment.** Nautilus's ``_calculate_duration_segments``
    decomposes any range with sub-day remainder into ``N D + S S`` segments
    (e.g. 30-days-minus-1-second → ``29 D + 86399 S``). IBKR's
    ``reqHistoricalData`` silently ignores ``useRTH=True`` for
    seconds-duration requests, returning full 24-hour data instead of
    RTH-only — that's how a single seconds-segment leaks ~1,050 extra
    bars (1,440 full-day vs 390 RTH).

    By tiling on full-day boundaries the segmenter emits exactly one
    ``chunk_days D`` segment per chunk, keeping useRTH semantics
    consistent across the year.

    The final chunk's end is rounded up to the next midnight after
    ``end`` so the requested range is fully covered. Chunk boundaries
    are end-exclusive (IBKR's ``endDateTime`` semantics), so adjacent
    chunks tile without overlap.
    """
    if chunk_days <= 0:
        raise ValueError(f"chunk_days must be positive, got {chunk_days}")

    midnight = datetime.min.time()

    def _round_up_to_midnight(ts: datetime) -> datetime:
        if ts.time() == midnight:
            return ts
        return ts.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=ts.tzinfo) + timedelta(
            days=1
        )

    cursor = start.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=start.tzinfo)
    end_midnight = _round_up_to_midnight(end)
    delta = timedelta(days=chunk_days)

    if cursor >= end_midnight:
        # Zero-length or sub-day range — yield a single chunk so callers
        # always get at least one window.
        yield start, end
        return

    while cursor < end_midnight:
        chunk_end = min(cursor + delta, end_midnight)
        yield cursor, chunk_end
        cursor = chunk_end


async def _fetch_ibkr_chunked(
    *,
    catalog_service: DataCatalogService,
    chunk_days: int = IBKR_CHUNK_DAYS,
) -> None:
    """Fetch AAPL 2018 in ``chunk_days`` windows, persisting each to the catalog.

    Workaround for the upstream Nautilus duration-segmenter bug
    (``_calculate_duration_segments`` truncates >day-but-not->year ranges
    for high-resolution bars). Each chunk goes through the standard
    ``DataCatalogService.fetch_or_load`` path so bars land in the
    isolated ``output_dir/ibkr_catalog/`` and are reconstituted via a
    single ``query_bars`` after all chunks complete.
    """
    chunks = list(_iter_chunks(REFERENCE_START, REFERENCE_END, chunk_days))
    logger.info(
        "ibkr_chunked_fetch_starting",
        total_chunks=len(chunks),
        chunk_days=chunk_days,
        instrument_id=INSTRUMENT_ID,
    )
    for idx, (chunk_start, chunk_end) in enumerate(chunks, start=1):
        logger.info(
            "ibkr_chunk_fetching",
            chunk_index=idx,
            total_chunks=len(chunks),
            start=chunk_start.isoformat(),
            end=chunk_end.isoformat(),
        )
        await catalog_service.fetch_or_load(
            instrument_id=INSTRUMENT_ID,
            start=chunk_start,
            end=chunk_end,
            bar_type_spec=TIMEFRAME_SPEC,
            data_source="ibkr",
            correlation_id=f"story-3-4-ibkr-chunk-{idx}",
        )


async def _fetch_or_query_ibkr_bars(
    *,
    catalog_service: DataCatalogService,
    skip_fetch: bool,
):
    """Return AAPL 2018 1-MINUTE bars from IBKR (cached or freshly fetched).

    With ``skip_fetch=True`` the harness reads from the isolated
    ``output_dir/ibkr_catalog/`` directly via ``query_bars`` and surfaces
    a clear error if the cache is empty (i.e., the user passed
    ``--skip-fetch`` on a first run).

    Without ``skip_fetch``, the harness fetches the year in 30-day chunks
    via ``_fetch_ibkr_chunked`` (workaround for the upstream segmenter
    bug) and then reads the full year back from the catalog as one set.
    """
    if not skip_fetch:
        await _fetch_ibkr_chunked(catalog_service=catalog_service)

    try:
        return await asyncio.to_thread(
            catalog_service.query_bars,
            INSTRUMENT_ID,
            REFERENCE_START,
            REFERENCE_END,
            TIMEFRAME_SPEC,
        )
    except DataNotFoundError as e:
        if skip_fetch:
            raise RuntimeError(
                f"--skip-fetch was set but the IBKR catalog at "
                f"{catalog_service.catalog_path} has no AAPL 2018 bars. "
                "Run once without --skip-fetch to populate the cache."
            ) from e
        raise RuntimeError(
            f"IBKR chunked fetch completed but the catalog at "
            f"{catalog_service.catalog_path} returned no AAPL 2018 bars. "
            "Likely all chunks returned empty — investigate IBKR Gateway "
            "data permissions for AAPL 2018 1-MINUTE."
        ) from e


async def _run_ibkr_backtest(*, bars, catalog_path: Path) -> BacktestResultSummary:
    """Run the IBKR-path backtest using bars already loaded from the cache."""
    request = _build_request(catalog_name=None)

    if not bars:
        raise RuntimeError(
            f"IBKR catalog at {catalog_path} returned zero bars for "
            f"{INSTRUMENT_ID} between {REFERENCE_START} and {REFERENCE_END}. "
            "Did the IBKR fetch run?"
        )

    equity = build_equity(nautilus_id=INSTRUMENT_ID, ticker=SYMBOL, bars=bars)
    data_result = DataLoadResult(
        bars=bars,
        instrument=equity,
        data_source_used=f"IBKR catalog ({catalog_path})",
    )

    return await _execute_and_summarise(request=request, data_result=data_result)


async def _run_firstrate_backtest(*, bars, firstrate_catalog: str) -> BacktestResultSummary:
    """Run the FirstRate-path backtest on the RTH-filtered bar set.

    Builds the instrument from the filtered bars (price precision is
    inferred from ``bars[0].open.precision`` — see ``build_equity``). The
    request still carries ``catalog_name=firstrate_catalog`` for AC #7's
    "differ only by catalog_name" assertion, but the bars handed to the
    orchestrator are the in-memory filtered set.
    """
    request = _build_request(catalog_name=firstrate_catalog)

    if not bars:
        raise RuntimeError(
            f"FirstRate catalog '{firstrate_catalog}' returned zero bars "
            f"for {INSTRUMENT_ID} after RTH filtering — the IBKR timestamp "
            "set has no overlap with the FirstRate bar set. Investigate."
        )

    equity = build_equity(nautilus_id=INSTRUMENT_ID, ticker=SYMBOL, bars=bars)
    data_result = DataLoadResult(
        bars=bars,
        instrument=equity,
        data_source_used=f"FirstRate catalog ({firstrate_catalog}, RTH-filtered)",
    )

    return await _execute_and_summarise(request=request, data_result=data_result)


async def _execute_and_summarise(*, request, data_result) -> BacktestResultSummary:
    """Run a single backtest with proper engine disposal and return its summary."""
    orchestrator = BacktestOrchestrator()
    try:
        result, _ = await orchestrator.execute(request, data_result.bars, data_result.instrument)
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


async def _load_firstrate_bars(*, firstrate_catalog: str, console: Console):
    """Load the full-session FirstRate bars via the named-catalog loader."""
    request = _build_request(catalog_name=firstrate_catalog)
    return await load_backtest_data(
        data_source="catalog",
        instrument_id=request.instrument_id,
        bar_type_spec=request.bar_type,
        start=request.start_date,
        end=request.end_date,
        console=console,
        catalog_name=firstrate_catalog,
    )


def _firstrate_metadata_count(firstrate_catalog: str) -> int | None:
    """Return ``catalog_instruments.bar_count_minute`` for AAPL, if present.

    Returns ``None`` when the metadata service is unavailable (e.g.,
    the DB session can't be created) — that's a pre-flight log
    annotation, not a halt condition. The blocking check is on the
    post-filter FirstRate bar count vs IBKR.
    """
    try:
        from src.cli.commands._backtest_helpers import _build_named_catalog_dependencies

        _, metadata_service, _ = _build_named_catalog_dependencies()
        row = metadata_service.get_instrument_sync(firstrate_catalog, SYMBOL)
        if row is None:
            return None
        return getattr(row, "bar_count_minute", None)
    except Exception as e:
        logger.warning("firstrate_metadata_lookup_failed", error=str(e))
        return None


async def _run_comparison_async(
    *,
    firstrate_catalog: str,
    output_dir: Path,
    skip_fetch: bool,
) -> ComparisonReport:
    """Async core for the comparison harness (extracted for testability)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    # Pre-flight: verify the output dir is writable. Raises OSError on failure
    # so we fail fast before the expensive backtest runs.
    probe = output_dir / ".write_probe"
    probe.write_text("ok")
    probe.unlink()

    console = Console(quiet=True)

    # Isolated, harness-owned IBKR catalog. Writing into NAUTILUS_PATH would
    # trample the FirstRate catalog when the two are aliased (this project's
    # ``.env`` does exactly that — same lesson as Story 3.3).
    ibkr_catalog_dir = output_dir / "ibkr_catalog"
    ibkr_catalog_dir.mkdir(parents=True, exist_ok=True)
    ibkr_catalog_service = DataCatalogService(catalog_path=str(ibkr_catalog_dir))

    # AC #7 guard — strategy params + window must be byte-identical across
    # both runs. ``catalog_name`` and the derived ``instrument_id`` are
    # legitimately different by design: the named-catalog path uses an
    # ``AAPL.NAMED_CATALOG`` placeholder that the loader resolves to the
    # DB-authoritative ``nautilus_id`` at load time, while the default path
    # resolves the symbol against ``NAUTILUS_PATH``'s availability cache. Both
    # paths still hit the same real instrument; the divergence in placeholder
    # form is not a strategy-input divergence.
    #
    # TODO: When ``BacktestRequest.from_cli_args`` defers ``instrument_id``
    # resolution to load time, tighten ``_ALLOWED_DIFFS`` back to
    # ``{"catalog_name"}`` and add a post-resolution equality assertion on
    # the loaded instrument's ``id``. Tracked in deferred-work for Story 3.4.
    _ibkr_req = _build_request(catalog_name=None)
    _fr_req = _build_request(catalog_name=firstrate_catalog)
    _ALLOWED_DIFFS: set[str] = {"catalog_name", "instrument_id"}
    _diff = {
        k
        for k in _ibkr_req.model_dump()
        if k not in _ALLOWED_DIFFS and _ibkr_req.model_dump()[k] != _fr_req.model_dump()[k]
    }
    if _diff:
        raise AssertionError(
            f"AC #7 violated: BacktestRequests diverge on fields {sorted(_diff)} "
            f"beyond {sorted(_ALLOWED_DIFFS)}. The shared _build_request helper has drifted."
        )
    if _ibkr_req.instrument_id != _fr_req.instrument_id:
        # Visible signal that the placeholder split happened — without this,
        # a real instrument_id regression (e.g. wrong venue) would land
        # silently inside the allowed-diff set.
        logger.info(
            "ac7_instrument_id_placeholder_diverged",
            ibkr=_ibkr_req.instrument_id,
            firstrate=_fr_req.instrument_id,
            note="expected: named-catalog placeholder vs default resolution; both should "
            "resolve to the same real instrument at load time",
        )

    # Step 1: IBKR fetch (or read from harness cache on --skip-fetch)
    ibkr_bars = await _fetch_or_query_ibkr_bars(
        catalog_service=ibkr_catalog_service, skip_fetch=skip_fetch
    )

    # Step 2: FirstRate full-session load (catalog query)
    firstrate_data = await _load_firstrate_bars(
        firstrate_catalog=firstrate_catalog, console=console
    )

    # Step 3a: dedup IBKR and FirstRate bars by ts_event. The chunked
    # IBKR fetch can leave the catalog with duplicate bars at chunk
    # boundaries when IBKR aligns the requested range to a session
    # boundary; processing the same minute twice would distort the
    # backtest.
    ibkr_dedup = _dedup_by_ts_event(ibkr_bars)
    firstrate_dedup = _dedup_by_ts_event(firstrate_data.bars)

    # Step 3b: pre-flight halt on RAW count divergence (AC #8) — runs
    # BEFORE intersection so it can actually catch ingest-shape mismatches
    # (post-intersection counts are equal by construction). With
    # useRTH=True the expected raw delta is ~30-40% (IBKR RTH-only vs
    # FirstRate full-session); ``PRE_FLIGHT_RAW_TOL`` is sized accordingly.
    firstrate_metadata_count = _firstrate_metadata_count(firstrate_catalog)
    _assert_pre_flight_alignment(
        ibkr_raw_count=len(ibkr_dedup),
        firstrate_raw_count=len(firstrate_dedup),
        firstrate_metadata_count=firstrate_metadata_count,
        use_rth=USE_RTH,
    )

    # Step 3c: build the SYMMETRIC timestamp intersection so both
    # backtests see the same bars. IBKR Gateway 10.45 with
    # ``useRTH=True`` was observed to leak ~3h of pre-market data per
    # trading day (~557 bars/day instead of the RTH-only ~390), so a
    # one-way "filter FirstRate to IBKR" left FirstRate covering only
    # ~45% of IBKR's extended set. The intersection guarantees
    # apples-to-apples comparison regardless of either source's session
    # policy.
    ibkr_timestamps: frozenset[int] = frozenset(bar.ts_event for bar in ibkr_dedup)
    firstrate_timestamps: frozenset[int] = frozenset(bar.ts_event for bar in firstrate_dedup)
    common_timestamps: frozenset[int] = ibkr_timestamps & firstrate_timestamps

    if not common_timestamps:
        # Empty intersection means raw counts looked plausible but the
        # actual minute-bar timestamps don't overlap — the canonical
        # symptom of TZ/DST drift between sources (the original Story 3.4
        # symptom before the FirstRate parser fix).
        raise RuntimeError(
            f"IBKR ∩ FirstRate timestamp intersection is empty "
            f"(IBKR raw={len(ibkr_dedup):,}, FirstRate raw={len(firstrate_dedup):,}). "
            "Likely cause: timezone/DST drift between sources, or a window "
            "mismatch where the two ranges genuinely don't overlap. "
            "Investigate before re-running."
        )

    ibkr_filtered = _filter_to_timestamp_set(ibkr_dedup, common_timestamps)
    firstrate_filtered = _filter_to_timestamp_set(firstrate_dedup, common_timestamps)

    logger.info(
        "timestamp_intersection_built",
        ibkr_raw=len(ibkr_bars),
        firstrate_raw=len(firstrate_data.bars),
        common=len(common_timestamps),
        ibkr_filtered=len(ibkr_filtered),
        firstrate_filtered=len(firstrate_filtered),
    )

    # Step 4: sequential backtests on the intersection (CLAUDE.md Gotcha
    # #4 — never two engines concurrently in one process).
    ibkr_summary = await _run_ibkr_backtest(bars=ibkr_filtered, catalog_path=ibkr_catalog_dir)
    firstrate_summary = await _run_firstrate_backtest(
        bars=firstrate_filtered, firstrate_catalog=firstrate_catalog
    )

    # ``ComparisonReport.legacy_metrics`` is the Path A field in 3.3's model,
    # which 3.4 reuses unchanged. Path A is the IBKR side here.
    report = evaluate_tolerance(ibkr_summary, firstrate_summary, dataset=DATASET)

    json_path = output_dir / "aapl_2018_comparison.json"
    json_path.write_text(report.model_dump_json(indent=2))

    return report


def run_comparison_harness(
    *,
    firstrate_catalog: str,
    output_dir: Path,
    skip_fetch: bool,
) -> ComparisonReport:
    """Synchronous entry point for the comparison harness.

    Wraps the async core with ``asyncio.run`` so callers (integration
    tests, the CLI ``main()``) don't need to manage an event loop.
    Pre-flight write check raises OSError before the expensive
    backtests run.
    """
    return asyncio.run(
        _run_comparison_async(
            firstrate_catalog=firstrate_catalog,
            output_dir=output_dir,
            skip_fetch=skip_fetch,
        )
    )


@click.command()
@click.option(
    "--firstrate-catalog",
    type=str,
    default="e2e-test",
    help="Named FirstRate catalog containing AAPL 1-MINUTE bars.",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("/tmp/story-3-4-evidence/"),
    help="Directory for evidence JSON + isolated IBKR catalog.",
)
@click.option(
    "--skip-fetch",
    is_flag=True,
    help="Skip the IBKR fetch and read bars from the harness-owned cache "
    "(only valid after a prior run populated output_dir/ibkr_catalog/).",
)
def main(
    firstrate_catalog: str,
    output_dir: Path,
    skip_fetch: bool,
) -> None:
    """Drive the AAPL 2018 IBKR-vs-FirstRate parity comparison end-to-end."""
    console = Console()
    report = run_comparison_harness(
        firstrate_catalog=firstrate_catalog,
        output_dir=output_dir,
        skip_fetch=skip_fetch,
    )
    console.print(
        "[dim]Note: the 'Legacy CSV' column shows IBKR-fetched bars "
        "(Story 3.4 reuses Story 3.3's renderer unchanged).[/dim]"
    )
    console.print(render_comparison_table(report))
    console.print(f"\nEvidence written to: {output_dir / 'aapl_2018_comparison.json'}")
    sys.exit(0 if report.overall_passed else 1)


if __name__ == "__main__":
    main()
