"""Parsers for FirstRate supplementary data (dividends and stock splits).

These are pure functions that emit lightweight DB-bound rows, NOT Nautilus
``Bar`` objects, so they are deliberately *not* registered with the
``register_parser`` registry in ``parsers/base.py`` (that registry maps
``AssetClass -> Bar`` parsers). The supplementary loader calls them directly.

Real FirstRate format (verified on disk, comma-delimited, reverse-chronological,
no header):

- Dividends: ``{TICKER}_divs.txt`` lines of ``yyyy-MM-dd,amount``.
- Splits: ``{TICKER}.txt`` lines of ``yyyy-MM-dd,ratio`` where ratio is a
  single decimal (new shares per old share); reverse splits have ratio < 1.

Malformed lines are logged and skipped (robustness) — a single bad line must
never abort parsing of an otherwise-valid file.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, TypeVar

import structlog

logger = structlog.get_logger(__name__)

_T = TypeVar("_T")


@dataclass(frozen=True)
class DividendRow:
    """A single parsed dividend record.

    Attributes:
        ex_date: Ex-dividend date.
        amount: Dividend amount per share, as Decimal (precision preserved).
    """

    ex_date: date
    amount: Decimal


@dataclass(frozen=True)
class SplitRow:
    """A single parsed stock split record.

    Attributes:
        effective_date: Date the split became effective.
        ratio: New shares per old share, as Decimal (< 1 for reverse splits).
    """

    effective_date: date
    ratio: Decimal


def _parse_date(value: str) -> date:
    """Parse a ``yyyy-MM-dd`` string into a ``date``.

    Args:
        value: Date string.

    Returns:
        Parsed date.

    Raises:
        ValueError: If the string is not a valid ``yyyy-MM-dd`` date.
    """
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


def _parse_finite_decimal(value: str, *, positive: bool = False) -> Decimal:
    """Parse a decimal string, rejecting non-finite (optionally non-positive) values.

    ``Decimal("nan")`` / ``Decimal("inf")`` do NOT raise on construction, so an
    explicit finiteness check is needed to keep corrupt values out of the
    ``Numeric`` columns. Split ratios must also be strictly positive.

    Args:
        value: Decimal string.
        positive: When True, also reject values ``<= 0`` (e.g. split ratios).

    Returns:
        Parsed finite Decimal.

    Raises:
        InvalidOperation: If the string is not a finite (positive) decimal.
    """
    parsed = Decimal(value.strip())
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise InvalidOperation(f"non-finite or out-of-range decimal: {value!r}")
    return parsed


def parse_dividends(file_path: Path) -> list[DividendRow]:
    """Parse a FirstRate dividend history file into typed rows.

    Splits each non-blank line on ``,`` into ``(date, amount)``, parsing the
    date as ``yyyy-MM-dd`` and the amount as ``Decimal`` (precision preserved).
    Source ordering (reverse-chronological) is preserved. Malformed lines are
    logged and skipped rather than raised.

    Args:
        file_path: Path to ``{TICKER}_divs.txt``.

    Returns:
        List of :class:`DividendRow` in source order. Empty if the file has no
        valid rows.
    """
    rows: list[DividendRow] = []
    for line_no, raw in enumerate(_read_lines(file_path), start=1):
        parts = raw.split(",")
        if len(parts) != 2:
            logger.warning(
                "dividend_line_malformed",
                file=str(file_path),
                line=line_no,
                content=raw,
                reason="expected 2 comma-separated fields",
            )
            continue
        try:
            ex_date = _parse_date(parts[0])
            amount = _parse_finite_decimal(parts[1])
        except (ValueError, InvalidOperation) as e:
            logger.warning(
                "dividend_line_malformed",
                file=str(file_path),
                line=line_no,
                content=raw,
                reason=str(e),
            )
            continue
        rows.append(DividendRow(ex_date=ex_date, amount=amount))
    return _dedup_by_date(rows, lambda r: r.ex_date)


def parse_splits(file_path: Path) -> list[SplitRow]:
    """Parse a FirstRate stock split history file into typed rows.

    Splits each non-blank line on ``,`` (NOT slash — the ``_splits_readme.txt``
    documents a slash but the real files are CSV) into ``(date, ratio)``,
    parsing the date as ``yyyy-MM-dd`` and the ratio as ``Decimal`` exactly as
    delivered (``4``, ``0.5`` for reverse splits). Source ordering is preserved.
    Malformed lines are logged and skipped rather than raised.

    Args:
        file_path: Path to ``{TICKER}.txt``.

    Returns:
        List of :class:`SplitRow` in source order. Empty if the file has no
        valid rows.
    """
    rows: list[SplitRow] = []
    for line_no, raw in enumerate(_read_lines(file_path), start=1):
        parts = raw.split(",")
        if len(parts) != 2:
            logger.warning(
                "split_line_malformed",
                file=str(file_path),
                line=line_no,
                content=raw,
                reason="expected 2 comma-separated fields",
            )
            continue
        try:
            effective_date = _parse_date(parts[0])
            ratio = _parse_finite_decimal(parts[1], positive=True)
        except (ValueError, InvalidOperation) as e:
            logger.warning(
                "split_line_malformed",
                file=str(file_path),
                line=line_no,
                content=raw,
                reason=str(e),
            )
            continue
        rows.append(SplitRow(effective_date=effective_date, ratio=ratio))
    return _dedup_by_date(rows, lambda r: r.effective_date)


def _dedup_by_date(rows: list[_T], key: Callable[[_T], date]) -> list[_T]:
    """Remove rows sharing a date key, keeping the last occurrence.

    FirstRate supplementary files sometimes repeat a row (a trailing-dup
    block), which would violate the ``(catalog, ticker, date)`` unique
    constraint on insert. Mirrors the bar parser's ``_dedup_by_timestamp``:
    the last occurrence wins, preserving the relative order of kept rows.

    Args:
        rows: Parsed rows in source order.
        key: Callable returning the date used as the dedup key for a row.

    Returns:
        Deduplicated list; the input unchanged when there are no duplicates.
    """
    if not rows:
        return rows

    last_index: dict[date, int] = {}
    for idx, row in enumerate(rows):
        last_index[key(row)] = idx

    if len(last_index) == len(rows):
        return rows

    removed = len(rows) - len(last_index)
    logger.warning("duplicate_supplementary_dates_removed", count=removed)
    return [rows[idx] for idx in sorted(last_index.values())]


def _read_lines(file_path: Path) -> list[str]:
    """Read a file and return its non-blank, stripped lines.

    Args:
        file_path: Path to the source file.

    Returns:
        List of non-empty stripped lines.
    """
    with open(file_path, newline="", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]
