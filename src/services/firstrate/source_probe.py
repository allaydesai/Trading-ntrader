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

from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog

logger = structlog.get_logger(__name__)

#: FirstRate files are headerless 6-column CSVs (Datetime, O, H, L, C, Volume). Futures
#: daily files add a 7th open-interest column, which the futures parser drops; FX intraday
#: rows have 7 because the date and time are separate columns.
_ACCEPTED_COLUMNS: tuple[int, ...] = (6, 7)

#: Timestamp formats the probe understands, in the same order the parser
#: tries them. Intraday (``YYYY-MM-DD HH:MM:SS``) first so daily rows do not
#: accidentally match it via the shorter ``YYYY-MM-DD`` format.
_INTRADAY_FMT = "%Y-%m-%d %H:%M:%S"
_DAILY_FMT = "%Y-%m-%d"

#: Separators stripped before the fast path's digit check.
_SEPARATORS = str.maketrans("", "", "-: ")

#: FirstRate CSV timestamps are local US Eastern Time with DST. Must match
#: ``FirstRateCsvParser._parse_timestamp`` exactly so the source_probe and
#: parser agree on what UTC datetime each row represents.
_FIRSTRATE_TIMEZONE = ZoneInfo("America/New_York")

#: New York session open of a FirstRate FX daily bar (``pair_specs.yaml`` ``session.open``;
#: not imported, this module stays free of the spec loader).
_FX_SESSION_OPEN = "17:00:00"


def compute_source_last_date(
    file_path: Path, *, source_tz: tzinfo = _FIRSTRATE_TIMEZONE
) -> datetime | None:
    """Return the UTC datetime of the latest row in a FirstRate CSV.

    FirstRate files are not guaranteed sorted (see
    ``FirstRateCsvParser.parse_file``'s explicit ``bars.sort`` call), so
    the whole file is scanned; reading only the last line is unsafe. The
    probe streams lines via ``open()`` rather than loading the full file
    with ``read_text()`` — intraday minute-bar CSVs can be multi-hundred-MB
    and loading them whole would balloon RSS on batch re-runs.

    ``YYYY-MM-DD[ HH:MM:SS]`` stamps sort lexicographically in wall-clock order,
    and the ET->UTC conversion never reorders two wall-clock times, so the scan
    keeps the largest well-shaped stamp string and parses only that one (odd-shaped
    stamps such as ``2025-1-3`` are parsed individually). Parsing
    every row cost ~60 s on a 6.4M-row futures file. If the largest string does
    not parse, a second pass ignores non-digit stamps (``2025-01-0X``); only if that
    winner fails too (``2025-13-45``) does the probe parse every row.

    Args:
        file_path: Path to the source ``.txt`` file.
        source_tz: The wall clock the file's stamps are in: US/Eastern for every FirstRate
            series except crypto, whose files are UTC (crypto M2 passes ``timezone.utc``).

    Returns:
        A UTC-aware :class:`datetime` for the max-date row, or ``None``
        if the file is missing, unreadable, empty, or contains no row
        whose first column parses as a FirstRate timestamp.
    """
    try:
        return _probe(file_path, source_tz)
    except FileNotFoundError:
        return None
    except (PermissionError, OSError) as exc:
        logger.warning(
            "source_probe_unreadable",
            file=str(file_path),
            error=str(exc),
        )
        return None


def _first_columns(file_path: Path):
    """Yield the stamp of every row with an accepted column count.

    Equity and futures rows yield their stripped first column. FirstRate FX rows carry a
    compact date (``YYYYMMDD``) and are rewritten to the canonical shape the scan compares:
    an intraday ``YYYYMMDD,HH:MM:SS,...`` row to ``YYYY-MM-DD HH:MM:SS``, and a daily
    ``YYYYMMDD,O,H,L,C,V`` row to its session open ``D-1 17:00`` (FX decision D5, the
    timestamp the FX parser gives the bar).
    """
    with file_path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            parts = stripped.split(",")
            if len(parts) in _ACCEPTED_COLUMNS:
                first = parts[0].strip()
                yield _fx_stamp(first, parts) if _is_compact_date(first) else first


def _is_compact_date(text: str) -> bool:
    return len(text) == 8 and text.isdigit()


def _fx_stamp(date: str, parts: list[str]) -> str:
    """The canonical stamp of a FirstRate FX row (see :func:`_first_columns`)."""
    iso = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    if len(parts) == 7:
        return f"{iso} {parts[1].strip()}"
    try:
        day = datetime.strptime(iso, _DAILY_FMT) - timedelta(days=1)
    except ValueError:
        return iso  # unparseable; the scan skips it like any malformed stamp
    return f"{day:%Y-%m-%d} {_FX_SESSION_OPEN}"


def _separator_shaped(stamp: str) -> bool:
    """``YYYY-MM-DD`` or ``YYYY-MM-DD HH:MM:SS`` by length and separators."""
    if len(stamp) not in (10, 19) or stamp[4] != "-" or stamp[7] != "-":
        return False
    return len(stamp) == 10 or (stamp[10] == " " and stamp[13] == ":" and stamp[16] == ":")


def _latest(
    file_path: Path, source_tz: tzinfo, check_digits: bool = False
) -> tuple[str | None, datetime | None]:
    """Largest canonical stamp string, and the latest parsed odd-shaped stamp.

    Canonical stamps are compared as strings; anything else (e.g. ``2025-1-3``, which
    ``strptime`` accepts) is parsed so it can still win. With ``check_digits`` a stamp must
    also be all digits to become the best string, so a non-digit one such as ``2025-01-0X``
    goes to the per-row parse path instead (it only runs when the cheap pass's winner did
    not parse: the check costs ~4 s on a 6.4M-row file).
    """
    best: str | None = None
    other: datetime | None = None
    for stamp in _first_columns(file_path):
        if _separator_shaped(stamp):
            if best is not None and stamp <= best:
                continue
            if not check_digits or stamp.translate(_SEPARATORS).isdigit():
                best = stamp
                continue
        dt = _parse_timestamp(stamp, source_tz)
        if dt is not None and (other is None or dt > other):
            other = dt
    return best, other


def _probe(file_path: Path, source_tz: tzinfo) -> datetime | None:
    """Cheap pass; a digit-checked pass if its winner does not parse; full parse last."""
    for check_digits in (False, True):
        best, other = _latest(file_path, source_tz, check_digits)
        if best is None:
            return other
        parsed = _parse_timestamp(best, source_tz)
        if parsed is not None:
            return parsed if other is None else max(parsed, other)
    return _max_parsed_stamp(file_path, source_tz)


def _max_parsed_stamp(file_path: Path, source_tz: tzinfo) -> datetime | None:
    max_dt: datetime | None = None
    for stamp in _first_columns(file_path):
        dt = _parse_timestamp(stamp, source_tz)
        if dt is not None and (max_dt is None or dt > max_dt):
            max_dt = dt
    return max_dt


def _parse_timestamp(ts_str: str, source_tz: tzinfo = _FIRSTRATE_TIMEZONE) -> datetime | None:
    """Parse a FirstRate timestamp string to a UTC-aware datetime.

    FirstRate CSV timestamps are US Eastern wall-clock time with DST, so
    ``2018-01-16 09:30:00`` is 09:30 EST (= 14:30 UTC) and
    ``2018-07-16 09:30:00`` is 09:30 EDT (= 13:30 UTC). Daily rows
    localize to midnight ET. ``source_tz=timezone.utc`` (crypto) takes the stamp as UTC.

    Mirrors
    :meth:`src.services.firstrate.parsers.firstrate_csv_parser.FirstRateCsvParser._parse_timestamp`
    by logic parity. Returns ``None`` on unparseable input instead of
    raising, so the probe can silently skip malformed rows the way the
    parser does via its ``try/except`` loop.
    """
    if " " in ts_str:
        try:
            naive = datetime.strptime(ts_str, _INTRADAY_FMT)
        except ValueError:
            return None
    else:
        try:
            naive = datetime.strptime(ts_str, _DAILY_FMT)
        except ValueError:
            return None
    return naive.replace(tzinfo=source_tz).astimezone(timezone.utc)
