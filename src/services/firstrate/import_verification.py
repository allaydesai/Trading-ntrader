"""Import verification surfacing for the FirstRate import (Story 2.5 AC1/AC4).

A pure helper that turns the parser's raw-row OHLC/integrity validation into the
non-blocking warning list threaded onto each ``ImportResult``. Lives apart from
``import_service.py`` (already over the 500-line size limit) so the new logic
does not grow it and unit-tests cheaply.

Why the raw-row layer (not the bars): Nautilus ``Bar`` construction *rejects*
out-of-range OHLC (``high < low``, ``high < open``, …), so an invalid row is
dropped before it ever becomes a bar and a bar-level check would be vacuous. The
parser's :meth:`BaseParser.validate_bars` already inspects the raw rows for the
Story 2.5 AC1 conditions (``high >= low``, ``volume >= 0``) and the related
integrity invariants; it logs ``bar_validation_failed`` but otherwise discards
the result. This module exposes that result so the violations are surfaced in the
import summary instead of silently passing (AC4).

Row-count parity (AC2) and sample-point accuracy (AC3) remain blocking checks in
``ImportService`` (``_verify_row_count`` / ``_verify_sample_points``); a mismatch
there fails the ticker.
"""

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.models.catalog import ValidationResult

#: Cap on surfaced verification warnings per ticker/timeframe so a fully-corrupt
#: file does not produce a multi-million-line summary. Once exceeded, a single
#: truncation note is appended.
MAX_VERIFICATION_WARNINGS = 20


def collect_ohlc_warnings(
    validation: "Optional[ValidationResult]",
    *,
    max_warnings: int = MAX_VERIFICATION_WARNINGS,
) -> list[str]:
    """Turn a parser :class:`ValidationResult` into capped warning strings.

    Args:
        validation: The parser's raw-row OHLC/integrity validation for this
            ticker/timeframe, or ``None`` when no validation ran (e.g. an empty
            source file).
        max_warnings: Hard cap on returned strings. Once exceeded, the first
            ``max_warnings`` are kept and a single truncation note is appended.

    Returns:
        Human-readable violation strings (empty when the import is clean or no
        validation ran). Each string is one flagged row from
        :meth:`BaseParser.validate_bars` (``high < low``, ``volume < 0``, and the
        related integrity invariants — Story 2.5 AC1).
    """
    if validation is None or validation.valid:
        return []

    errors = validation.errors
    if len(errors) <= max_warnings:
        return list(errors)
    return [
        *errors[:max_warnings],
        f"... (more than {max_warnings} OHLC/integrity violations; output truncated)",
    ]
