"""``validate_config``: everything a run needs, checked in seconds without running (S1.4)."""

from datetime import date, datetime
from typing import Any

from src.mcp_server.catalogs import SUPPORTED_TIMEFRAMES, catalog_availability
from src.mcp_server.errors import ToolFailure
from src.mcp_server.request import BacktestSpec, ResolvedRequest, resolve
from src.mcp_server.strategies import default_sources, resolve_strategy
from src.services.fx.symbols import conversion_ticker, fx_ticker
from src.services.sizing_overrides import coin_for_symbol


def _day(iso: str | None) -> date | None:
    return datetime.fromisoformat(iso).date() if iso else None


def _window_problems(
    spec: BacktestSpec, coverage: dict[str, Any]
) -> tuple[list[ToolFailure], list[str]]:
    """Errors and warnings from comparing the requested window with the data's span."""
    timeframe = spec.timeframe.strip().upper().removesuffix("-LAST")
    if timeframe not in SUPPORTED_TIMEFRAMES:
        return [], []  # resolve() already reports the unsupported timeframe
    span = coverage["timeframes"].get(timeframe)
    if span is None:
        available = ", ".join(coverage["timeframes"]) or "none"
        return [
            ToolFailure(
                "no_bars_for_timeframe",
                f"'{coverage['symbol']}' has no {timeframe} bars in '{coverage['catalog']}'.",
                fix=f"Use a timeframe with bars: {available}.",
            )
        ], []
    first, last = _day(span["start"]), _day(span["end"])
    covers = f"{first or 'an unknown start'} to {last or 'an unknown end'}"
    if (first and spec.end < first) or (last and spec.start > last):
        return [
            ToolFailure(
                "window_outside_coverage",
                f"{spec.start} to {spec.end} has no {timeframe} bars; data covers {covers}.",
                fix=f"Choose a window inside {covers}.",
            )
        ], []
    warnings = []
    if (first and spec.start < first) or (last and spec.end > last):
        warnings.append(
            f"Requested {spec.start} to {spec.end}, but {timeframe} data covers {covers}; "
            "the run will use the overlap only."
        )
    if span.get("start_is_exact") is False:
        warnings.append(
            f"The start date is not {timeframe}'s own; its bars may start earlier or later. "
            "A window with no bars fails the job with data_not_found. "
            "Fix: run `ntrader catalog backfill-coverage-starts` for this catalog."
        )
    return [], warnings


def _days(span: dict[str, Any]) -> tuple[date | None, date | None]:
    return _day(span.get("start")), _day(span.get("end"))


def _conversion_problems(spec: BacktestSpec, coverage: dict[str, Any]) -> list[ToolFailure]:
    """An FX cross whose conversion pair misses dates the run trades (QA fix, Q3).

    The traded dates are the requested window clipped to the cross's own bars; the
    conversion pair must have bars at that timeframe from the first to the last of them,
    or the run would fail at its first signal outside them.
    """
    ticker, catalog = coverage["symbol"], coverage["catalog"]
    pair = conversion_ticker(ticker)
    timeframe = spec.timeframe.strip().upper().removesuffix("-LAST")
    if pair is None or timeframe not in coverage["timeframes"]:
        return []
    first, last = _days(coverage["timeframes"][timeframe])
    traded = (max(spec.start, first or spec.start), min(spec.end, last or spec.end))
    try:
        span = catalog_availability(pair, catalog)["timeframes"].get(timeframe)
    except ToolFailure:
        span = None
    if span is None:
        return [
            ToolFailure(
                "conversion_pair_missing",
                f"{ticker} settles in {ticker[3:]}; its USD conversion pair {pair} has no "
                f"{timeframe} bars in '{catalog}', so its P&L cannot be converted to USD.",
                fix=f"Import {pair} {timeframe} into '{catalog}' for {traded[0]} to {traded[1]}.",
            )
        ]
    first, last = _days(span)
    covered = (first or traded[0], last or traded[1])  # an unknown edge is not a refusal
    if covered[0] <= traded[0] and covered[1] >= traded[1]:
        return []
    inside = f"{max(traded[0], covered[0])} to {min(traded[1], covered[1])}"
    return [
        ToolFailure(
            "conversion_outside_coverage",
            f"{ticker}'s USD conversion pair {pair} covers {covered[0]} to {covered[1]} at "
            f"{timeframe}, but the run trades {ticker} from {traded[0]} to {traded[1]}.",
            fix=f"Import {pair} for the whole window, or run inside {inside}.",
        )
    ]


def _coverage(
    spec: BacktestSpec, catalog: str
) -> tuple[dict[str, Any] | None, list[ToolFailure], list[str]]:
    try:
        ticker = fx_ticker(spec.symbol) or coin_for_symbol(spec.symbol) or spec.symbol
        coverage = catalog_availability(ticker, catalog)
    except ToolFailure as failure:
        return None, [failure], []
    if not coverage["backtestable"]:
        reason = coverage.get("reason") or "Instrument has no qualified id."
        fix = "Resolve the instrument's venue, or choose another symbol."
        return coverage, [ToolFailure("not_backtestable", reason, fix=fix)], []
    errors, warnings = _window_problems(spec, coverage)
    return coverage, errors or _conversion_problems(spec, coverage), warnings


def validate(spec: BacktestSpec, *, default_catalog: str) -> dict[str, Any]:
    """Resolve the request and check its data, reporting every problem found."""
    errors: list[ToolFailure] = []
    resolved: ResolvedRequest | None = None
    warnings: list[str] = []
    try:
        resolved = resolve(spec, default_catalog=default_catalog)
    except ToolFailure as failure:
        errors.append(failure)
    else:
        given = frozenset(k for k, v in spec.params.items() if v is not None)
        warnings += default_sources(resolve_strategy(spec.strategy), given)[1]
    coverage: dict[str, Any] | None = None
    catalog = spec.catalog or default_catalog
    if catalog:
        coverage, data_errors, data_warnings = _coverage(spec, catalog)
        errors += data_errors
        warnings += data_warnings
    return {
        "ok": not errors,
        "resolved": resolved.summary() if resolved else None,
        "coverage": coverage,
        "errors": [e.to_dict()["error"] for e in errors],
        "warnings": warnings,
    }
