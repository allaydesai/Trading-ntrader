"""Pure-Python last-date probe for FirstRate source CSV files.

This module MUST NOT import :mod:`nautilus_trader` or any module that
transitively does (e.g., ``FirstRateCsvParser``). Story 1-7 adds an
idempotent re-run classifier that runs *before* the parser; for tickers
that will be skipped, the parser must never be invoked and the Nautilus
C/Rust extensions must never be pulled into process state. See
``CLAUDE.md`` (LogGuard gotcha) and the subprocess isolation test in
``tests/unit/services/firstrate/test_source_probe.py`` for the rules.

Public API:

- :func:`compute_source_last_date` — scan a FirstRate ``.txt`` file and
  return the UTC datetime of the max-date row (or ``None`` if nothing
  parseable exists). Logic mirrors
  :meth:`src.services.firstrate.parsers.firstrate_csv_parser.FirstRateCsvParser._read_lines`
  and ``_parse_timestamp`` by *parity*, not by import.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

#: FirstRate files are headerless 6-column CSVs (Datetime, O, H, L, C, Volume).
_EXPECTED_COLUMNS: int = 6

#: Timestamp formats the probe understands, in the same order the parser
#: tries them. Intraday (``YYYY-MM-DD HH:MM:SS``) first so daily rows do not
#: accidentally match it via the shorter ``YYYY-MM-DD`` format.
_INTRADAY_FMT = "%Y-%m-%d %H:%M:%S"
_DAILY_FMT = "%Y-%m-%d"


def compute_source_last_date(file_path: Path) -> datetime | None:
    """Return the UTC datetime of the latest row in a FirstRate CSV.

    FirstRate files are not guaranteed sorted (see
    ``FirstRateCsvParser.parse_file``'s explicit ``bars.sort`` call), so
    the whole file is scanned; reading only the last line is unsafe. The
    probe streams lines via ``open()`` rather than loading the full file
    with ``read_text()`` — intraday minute-bar CSVs can be multi-hundred-MB
    and loading them whole would balloon RSS on batch re-runs.

    Args:
        file_path: Path to the source ``.txt`` file.

    Returns:
        A UTC-aware :class:`datetime` for the max-date row, or ``None``
        if the file is missing, unreadable, empty, or contains no row
        whose first column parses as a FirstRate timestamp.
    """
    max_dt: datetime | None = None
    try:
        with file_path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                stripped = raw.strip()
                if not stripped:
                    continue
                parts = stripped.split(",")
                if len(parts) != _EXPECTED_COLUMNS:
                    continue
                dt = _parse_timestamp(parts[0].strip())
                if dt is None:
                    continue
                if max_dt is None or dt > max_dt:
                    max_dt = dt
    except FileNotFoundError:
        return None
    except (PermissionError, OSError) as exc:
        logger.warning(
            "source_probe_unreadable",
            file=str(file_path),
            error=str(exc),
        )
        return None

    if max_dt is None:
        return None
    return max_dt.replace(tzinfo=timezone.utc)


def _parse_timestamp(ts_str: str) -> datetime | None:
    """Parse a FirstRate daily or intraday timestamp to a naive datetime.

    Mirrors
    :meth:`src.services.firstrate.parsers.firstrate_csv_parser.FirstRateCsvParser._parse_timestamp`
    by logic parity. Returns ``None`` on unparseable input instead of
    raising, so the probe can silently skip malformed rows the way the
    parser does via its ``try/except`` loop.
    """
    if " " in ts_str:
        try:
            return datetime.strptime(ts_str, _INTRADAY_FMT)
        except ValueError:
            return None
    try:
        return datetime.strptime(ts_str, _DAILY_FMT)
    except ValueError:
        return None
