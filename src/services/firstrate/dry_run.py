"""Pure-Python dry-run scanner, schema validator, and estimator for FirstRate imports.

This module MUST NOT import :mod:`nautilus_trader` or any module that does
(e.g., ``FirstRateCsvParser`` or ``ImportService``). The dry-run code path is
a read-only smoke test that should not touch the DB or the Nautilus C/Rust
extension. See ``_bmad-output/implementation-artifacts/1-6-pre-import-dry-run-validation.md``
and ``CLAUDE.md`` (LogGuard / C extension isolation) for context.

Public API:

- :func:`scan_firstrate_directory` — walk a directory tree, group ``.txt``
  files by inferred timeframe, and return a :class:`DryRunReport` skeleton.
- :func:`validate_schema_sample` — sample the first non-blank line of up to
  ``sample_size`` files per group and report any column-count mismatches.
- :func:`estimate_parquet_bytes` — project Parquet output size from source
  CSV size using :data:`PARQUET_COMPRESSION_RATIO`.
- :func:`build_dry_run_report` — orchestration helper combining all three.
- :func:`format_bytes` — 4-line human-readable byte formatter used by the CLI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from src.api.models.explorer import ExplorerTimeframe
from src.models.catalog import (
    AssetClass,
    DryRunReport,
    SchemaMismatch,
    TimeframeSummary,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Filename suffix → Nautilus timeframe spec. Must stay in sync with
#: ``TIMEFRAME_MAP`` in ``src/cli/commands/import_data.py``. Match is
#: case-insensitive (we lowercase filenames before the substring check).
#: The 30-minute spec is sourced from the central timeframe enum
#: (``ExplorerTimeframe.THIRTY_MIN``) — defined once, never inlined (ADR-9).
_FILENAME_TIMEFRAME_MAP: dict[str, str] = {
    "_1day_": "1-DAY-LAST",
    "_1hour_": "1-HOUR-LAST",
    "_1min_": "1-MINUTE-LAST",
    "_5min_": "5-MINUTE-LAST",
    "_30min_": ExplorerTimeframe.THIRTY_MIN.bar_type_spec,
}

#: Label for files whose suffix does not match any known timeframe. These are
#: still counted so operators know they exist, but flagged for review.
_UNKNOWN_TIMEFRAME = "unknown"

#: Empirical ratio of Parquet output bytes to source CSV bytes for FirstRate
#: OHLCV data with snappy/zstd compression (roughly 0.30–0.40 in practice).
#: Tune against a real FirstRate Stocks sample once available. See
#: ``src/services/data_catalog.py:313-315`` for the inverse ~128 bytes/row
#: estimate used elsewhere.
PARQUET_COMPRESSION_RATIO: float = 0.35

#: Expected column count for the FirstRate 6-column headerless schema
#: (Datetime, Open, High, Low, Close, Volume).
_EXPECTED_COLUMNS: int = 6

#: Marker that separates the ticker from the rest of a FirstRate filename,
#: e.g. ``AAPL_full_1day_adjsplitdiv.txt``.
_FIRSTRATE_TICKER_MARKER = "_full_"


# ---------------------------------------------------------------------------
# Filename → metadata helpers (pure string ops)
# ---------------------------------------------------------------------------


def _infer_timeframe(filename: str) -> str:
    """Return the Nautilus timeframe spec inferred from a FirstRate filename.

    Case-insensitive: ``AAPL_full_1Day_adjsplitdiv.txt`` is treated as a
    ``1-DAY-LAST`` file. Unknown suffixes bucket into :data:`_UNKNOWN_TIMEFRAME`.
    """
    lower = filename.lower()
    for suffix, spec in _FILENAME_TIMEFRAME_MAP.items():
        if suffix in lower:
            return spec
    return _UNKNOWN_TIMEFRAME


def _extract_ticker(file: Path) -> str | None:
    """Extract the ticker symbol from a FirstRate filename.

    Mirrors :meth:`src.services.firstrate.import_service.ImportService._extract_ticker`
    without importing the Nautilus-heavy module. FirstRate filenames follow
    ``{TICKER}_full_{timeframe}_adjsplitdiv.txt``. Returns ``None`` if the
    filename does not contain the ``_full_`` marker — callers should treat
    these as unrecognized (see ``_walk``).
    """
    stem = file.stem
    if _FIRSTRATE_TICKER_MARKER in stem:
        return stem.split(_FIRSTRATE_TICKER_MARKER, 1)[0]
    return None


# ---------------------------------------------------------------------------
# Internal walk state
# ---------------------------------------------------------------------------


@dataclass
class _Bucket:
    tickers: set[str] = field(default_factory=set)
    files: list[Path] = field(default_factory=list)
    bytes_: int = 0


@dataclass
class _ScanState:
    groups: dict[str, _Bucket] = field(default_factory=dict)
    distinct_tickers: set[str] = field(default_factory=set)
    unreadable_count: int = 0
    name_mismatches: list[SchemaMismatch] = field(default_factory=list)
    total_file_count: int = 0
    total_source_bytes: int = 0


def _walk(source_path: Path) -> _ScanState:
    """Walk ``source_path`` and collect scan state in a single pass.

    Uses :func:`os.walk` with ``followlinks=False`` so symlinked directories
    cannot induce infinite recursion. Per-directory :class:`OSError` /
    :class:`PermissionError` are swallowed via ``os.walk``'s ``onerror=None``
    default; per-file stat failures increment ``unreadable_count``.

    Discovery is case-insensitive for the ``.txt`` extension so files with
    ``.TXT`` / ``.Txt`` on case-sensitive filesystems are not silently missed.
    """
    state = _ScanState()

    def _on_walk_error(exc: OSError) -> None:
        # Individual directory iteration failures (e.g., permission denied on
        # a deeply-nested subdir) should not abort the whole scan. We cannot
        # reliably attribute them to a single file, so just count and move on.
        del exc
        state.unreadable_count += 1

    try:
        walker = os.walk(source_path, followlinks=False, onerror=_on_walk_error)
    except (PermissionError, OSError):
        return state

    for dirpath, dirnames, filenames in walker:
        dirnames.sort()
        for name in sorted(filenames):
            if not name.lower().endswith(".txt"):
                continue
            file = Path(dirpath) / name
            try:
                if not file.is_file():
                    continue
                size = file.stat().st_size
            except (PermissionError, OSError):
                state.unreadable_count += 1
                continue

            ticker = _extract_ticker(file)
            if ticker is None:
                state.name_mismatches.append(
                    SchemaMismatch(
                        file_path=file,
                        expected_columns=_EXPECTED_COLUMNS,
                        detected_columns=0,
                        reason="unrecognized filename pattern",
                    )
                )
                continue

            spec = _infer_timeframe(name)
            bucket = state.groups.setdefault(spec, _Bucket())
            bucket.tickers.add(ticker)
            bucket.files.append(file)
            bucket.bytes_ += size

            state.distinct_tickers.add(ticker)
            state.total_file_count += 1
            state.total_source_bytes += size

    return state


def _state_to_report(state: _ScanState, source_path: Path, asset_class: AssetClass) -> DryRunReport:
    timeframes = {
        spec: TimeframeSummary(
            ticker_count=len(bucket.tickers),
            file_count=len(bucket.files),
            source_bytes=bucket.bytes_,
        )
        for spec, bucket in state.groups.items()
    }
    return DryRunReport(
        asset_class=asset_class,
        source_path=str(source_path),
        timeframes=timeframes,
        total_file_count=state.total_file_count,
        total_source_bytes=state.total_source_bytes,
        estimated_parquet_bytes=0,
        distinct_ticker_count=len(state.distinct_tickers),
        unreadable_count=state.unreadable_count,
        schema_mismatches=list(state.name_mismatches),
    )


# ---------------------------------------------------------------------------
# Directory scanner (public)
# ---------------------------------------------------------------------------


def scan_firstrate_directory(source_path: Path, asset_class: AssetClass) -> DryRunReport:
    """Walk ``source_path`` and build a :class:`DryRunReport` skeleton.

    The scanner is stat-only: it never opens a file for reading. Files are
    grouped by the timeframe suffix inferred from their filename (not their
    parent directory), so both supported FirstRate layouts work in one pass:

    - ``source_path == Stocks_1day/`` (alphabetical subdirs directly inside).
    - ``source_path == Stocks/`` (timeframe subdirs inside, alphabetical inside).

    Permission errors on individual subdirectories or files are counted via
    :attr:`DryRunReport.unreadable_count` so operators know the scan was not
    complete. Files with unrecognized filename patterns are surfaced in
    :attr:`DryRunReport.schema_mismatches` with
    ``reason="unrecognized filename pattern"``.

    Args:
        source_path: Root directory to scan.
        asset_class: Asset class label recorded on the returned report.

    Returns:
        A :class:`DryRunReport` with everything except
        ``estimated_parquet_bytes`` populated (callers typically get it via
        :func:`build_dry_run_report`).
    """
    state = _walk(source_path)
    return _state_to_report(state, source_path, asset_class)


# ---------------------------------------------------------------------------
# Schema validator
# ---------------------------------------------------------------------------


def _read_first_nonblank_line(file: Path) -> tuple[str | None, str | None]:
    """Return ``(line, reason)`` for the first non-blank line of ``file``.

    ``line`` is the CRLF-stripped first non-blank line, or ``None`` if the
    file is empty, unreadable, or not valid UTF-8. ``reason`` disambiguates
    the ``None`` case: ``"empty file"`` for truly empty files, ``"decode
    error"`` for files that are not UTF-8, ``"unreadable"`` for I/O errors.

    Matches the blank-line / CRLF handling of
    :meth:`src.services.firstrate.parsers.firstrate_csv_parser.FirstRateCsvParser._read_lines`
    re-implemented here so the dry-run path does not import the Nautilus-heavy
    parser module.
    """
    try:
        with file.open(encoding="utf-8") as handle:
            for raw in handle:
                stripped = raw.strip("\r\n").strip()
                if stripped:
                    return stripped, None
    except UnicodeDecodeError:
        return None, "decode error"
    except (PermissionError, OSError):
        return None, "unreadable"
    return None, "empty file"


def validate_schema_sample(files: list[Path], sample_size: int = 5) -> list[SchemaMismatch]:
    """Sample-check that files look like valid FirstRate 6-column CSVs.

    Reads only the first non-blank line of up to ``sample_size`` files. Users
    who want exhaustive schema validation run the real import and rely on the
    parser's row-level skipping. This is a smoke test, not a formal verifier.

    Args:
        files: Candidate files to sample. Only the first ``sample_size`` are
            inspected.
        sample_size: Maximum number of files to open.

    Returns:
        A list of :class:`SchemaMismatch` records, one per bad file. Empty if
        every sampled file's first line has exactly ``6`` comma-separated
        columns.
    """
    mismatches: list[SchemaMismatch] = []
    for file in files[:sample_size]:
        line, reason = _read_first_nonblank_line(file)
        if line is None:
            mismatches.append(
                SchemaMismatch(
                    file_path=file,
                    expected_columns=_EXPECTED_COLUMNS,
                    detected_columns=0,
                    reason=reason,
                )
            )
            continue
        detected = len(line.split(","))
        if detected != _EXPECTED_COLUMNS:
            mismatches.append(
                SchemaMismatch(
                    file_path=file,
                    expected_columns=_EXPECTED_COLUMNS,
                    detected_columns=detected,
                )
            )
    return mismatches


# ---------------------------------------------------------------------------
# Disk usage estimator
# ---------------------------------------------------------------------------


def estimate_parquet_bytes(source_bytes: int) -> int:
    """Project Parquet output size from source CSV bytes.

    Uses :data:`PARQUET_COMPRESSION_RATIO` (~0.35). Tune the constant against
    one real FirstRate Stocks sample in a follow-up cleanup story.
    """
    return int(source_bytes * PARQUET_COMPRESSION_RATIO)


# ---------------------------------------------------------------------------
# Human-readable byte formatter (used by the CLI renderer)
# ---------------------------------------------------------------------------


def format_bytes(n: int) -> str:
    """Format a byte count as a binary-unit string (``1.23 MiB`` etc.).

    Lives in this module (not the CLI) because the spec pins it here and it
    keeps the dry-run rendering code self-contained.
    """
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024:
            return f"{int(value)} B" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TiB"


# ---------------------------------------------------------------------------
# Orchestration helper
# ---------------------------------------------------------------------------


def build_dry_run_report(
    source_path: Path,
    asset_class: AssetClass,
    sample_size: int = 5,
    catalog: str | None = None,
) -> DryRunReport:
    """Scan, validate, and estimate in one call — used by the CLI command.

    Walks the source tree exactly once (via :func:`_walk`), then runs
    :func:`validate_schema_sample` over each timeframe group's retained file
    list without re-walking. Any schema mismatches are appended to the
    filename-pattern mismatches already surfaced by the walk.
    """
    state = _walk(source_path)
    report = _state_to_report(state, source_path, asset_class)

    schema_mismatches: list[SchemaMismatch] = list(state.name_mismatches)
    for bucket in state.groups.values():
        schema_mismatches.extend(validate_schema_sample(bucket.files, sample_size))

    return report.model_copy(
        update={
            "catalog": catalog,
            "schema_mismatches": schema_mismatches,
            "estimated_parquet_bytes": estimate_parquet_bytes(report.total_source_bytes),
        }
    )
