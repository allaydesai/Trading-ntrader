"""Base parser ABC and parser registry for FirstRate data files.

Provides the abstract base class that all asset-specific parsers must
implement, a decorator-based registry for parser discovery, and shared
OHLC validation logic.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId

from src.models.catalog import AssetClass, ValidationResult


@dataclass(frozen=True)
class RawBarData:
    """Intermediate representation of a CSV row before Bar construction.

    Holds string values exactly as parsed from CSV so that validation
    can flag issues (negative prices, high < low) before Nautilus Bar
    construction rejects them.
    """

    timestamp: str
    open: str
    high: str
    low: str
    close: str
    volume: str


# ---------------------------------------------------------------------------
# Parser Registry
# ---------------------------------------------------------------------------

_PARSER_REGISTRY: dict[AssetClass, type["BaseParser"]] = {}


def register_parser(asset_class: AssetClass):
    """Decorator to register a parser class for a given asset class.

    Args:
        asset_class: The asset class this parser handles.

    Returns:
        Decorator that registers the class and returns it unchanged.
    """

    def decorator(cls: type["BaseParser"]) -> type["BaseParser"]:
        _PARSER_REGISTRY[asset_class] = cls
        return cls

    return decorator


def get_parser(asset_class: AssetClass) -> "BaseParser":
    """Return a new parser instance for the given asset class.

    Args:
        asset_class: The asset class to look up.

    Returns:
        A fresh parser instance.

    Raises:
        ValueError: If no parser is registered for the asset class.
    """
    if asset_class not in _PARSER_REGISTRY:
        raise ValueError(
            f"No parser registered for {asset_class!r}. Registered: {list(_PARSER_REGISTRY.keys())}"
        )
    return _PARSER_REGISTRY[asset_class]()


# ---------------------------------------------------------------------------
# Base Parser ABC
# ---------------------------------------------------------------------------


class BaseParser(ABC):
    """Abstract base class for FirstRate CSV parsers.

    Each asset-specific parser must implement ``parse_file`` and
    ``map_instrument_id``.  The shared ``validate_bars`` method is
    provided by this base class.

    Attributes:
        last_validation: The :class:`ValidationResult` from the most recent
            ``parse_file`` call (Story 2.5). ``None`` until a file has been
            parsed (or when the file was empty). The import loop reads this to
            surface OHLC/integrity violations in the import summary — invalid
            rows are dropped before becoming bars, so the raw-row validation is
            the only place those violations are visible.
    """

    #: Set by ``parse_file`` so the import loop can surface OHLC-sanity flags.
    last_validation: "ValidationResult | None" = None

    @abstractmethod
    def parse_file(
        self,
        file_path: Path,
        instrument_id: InstrumentId,
        bar_type: BarType,
    ) -> list[Bar]:
        """Parse a CSV file into a list of Nautilus Bar objects.

        Args:
            file_path: Path to the headerless CSV file.
            instrument_id: Nautilus InstrumentId for the ticker.
            bar_type: Nautilus BarType describing the bar specification.

        Returns:
            List of Bar objects sorted by ts_init ascending.
        """

    @abstractmethod
    def map_instrument_id(
        self,
        ticker: str,
        bar_type_spec: str,
    ) -> InstrumentId:
        """Map a raw ticker string to a Nautilus InstrumentId.

        Args:
            ticker: Raw ticker symbol (e.g. "SPY").
            bar_type_spec: Bar type spec string (e.g. "1-DAY-LAST").

        Returns:
            Resolved InstrumentId.
        """

    def validate_bars(self, rows: list[RawBarData]) -> ValidationResult:
        """Validate OHLC integrity for raw parsed row data.

        Runs before Nautilus Bar construction so that invalid rows
        (high < low, negative volume, negative prices) can be reported
        without triggering Nautilus constructor errors.

        Checks per row:
        - all OHLC/volume fields are valid numbers
        - all OHLC prices > 0
        - high >= low
        - open and close within [low, high]
        - volume >= 0

        Args:
            rows: List of RawBarData parsed from CSV.

        Returns:
            ValidationResult with row-level error details.
        """
        errors: list[str] = []
        invalid_count = 0

        for idx, row in enumerate(rows):
            row_errors: list[str] = []

            try:
                high = Decimal(row.high)
                low = Decimal(row.low)
                open_ = Decimal(row.open)
                close = Decimal(row.close)
                volume = Decimal(row.volume)
            except InvalidOperation:
                row_errors.append(
                    f"Row {idx + 1}: non-numeric value detected "
                    f"(O={row.open!r} H={row.high!r} L={row.low!r} "
                    f"C={row.close!r} V={row.volume!r})"
                )
                invalid_count += 1
                errors.extend(row_errors)
                continue

            if open_ <= 0 or high <= 0 or low <= 0 or close <= 0:
                row_errors.append(
                    f"Row {idx + 1}: non-positive price detected "
                    f"(O={row.open} H={row.high} L={row.low} C={row.close})"
                )

            if high < low:
                row_errors.append(f"Row {idx + 1}: high ({row.high}) < low ({row.low})")

            if open_ > high or open_ < low:
                row_errors.append(f"Row {idx + 1}: open ({row.open}) outside high-low range")

            if close > high or close < low:
                row_errors.append(f"Row {idx + 1}: close ({row.close}) outside high-low range")

            if volume < 0:
                row_errors.append(f"Row {idx + 1}: volume ({row.volume}) < 0")

            if row_errors:
                invalid_count += 1
                errors.extend(row_errors)

        return ValidationResult(
            valid=invalid_count == 0,
            errors=errors,
            row_count=len(rows),
            invalid_rows=invalid_count,
        )
