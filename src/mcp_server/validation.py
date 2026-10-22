"""``validate_config``: everything a run needs, checked in seconds without running (S1.4)."""

from datetime import date, datetime
from typing import Any

from src.mcp_server.catalogs import SUPPORTED_TIMEFRAMES, catalog_availability
from src.mcp_server.errors import ToolFailure
from src.mcp_server.request import BacktestSpec, ResolvedRequest, resolve
from src.mcp_server.strategies import default_sources, resolve_strategy


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


def _coverage(
    spec: BacktestSpec, catalog: str
) -> tuple[dict[str, Any] | None, list[ToolFailure], list[str]]:
    try:
        coverage = catalog_availability(spec.symbol, catalog)
    except ToolFailure as failure:
        return None, [failure], []
    if not coverage["backtestable"]:
        reason = coverage.get("reason") or "Instrument has no qualified id."
        fix = "Resolve the instrument's venue, or choose another symbol."
        return coverage, [ToolFailure("not_backtestable", reason, fix=fix)], []
    errors, warnings = _window_problems(spec, coverage)
    return coverage, errors, warnings


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
