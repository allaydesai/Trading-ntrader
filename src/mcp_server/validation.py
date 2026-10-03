"""``validate_config``: everything a run needs, checked in seconds without running (S1.4)."""

from datetime import date, datetime
from typing import Any

from src.mcp_server.catalogs import SUPPORTED_TIMEFRAMES, catalog_availability
from src.mcp_server.errors import ToolFailure
from src.mcp_server.request import BacktestSpec, ResolvedRequest, resolve


def _day(iso: str) -> date:
    return datetime.fromisoformat(iso).date()


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
    if spec.end < first or spec.start > last:
        return [
            ToolFailure(
                "window_outside_coverage",
                f"{spec.start} to {spec.end} has no {timeframe} bars; "
                f"data covers {first} to {last}.",
                fix=f"Choose a window inside {first} to {last}.",
            )
        ], []
    if spec.start < first or spec.end > last:
        return [], [
            f"Requested {spec.start} to {spec.end}, but {timeframe} data covers {first} to "
            f"{last}; the run will use the overlap only."
        ]
    return [], []


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
    try:
        resolved = resolve(spec, default_catalog=default_catalog)
    except ToolFailure as failure:
        errors.append(failure)
    coverage: dict[str, Any] | None = None
    warnings: list[str] = []
    catalog = spec.catalog or default_catalog
    if catalog:
        coverage, data_errors, warnings = _coverage(spec, catalog)
        errors += data_errors
    return {
        "ok": not errors,
        "resolved": resolved.summary() if resolved else None,
        "coverage": coverage,
        "errors": [e.to_dict()["error"] for e in errors],
        "warnings": warnings,
    }
