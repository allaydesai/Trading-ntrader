"""FirstRate headerless CSV parser.

Parses 6-column headerless CSV files (Datetime/Date, O, H, L, C, Volume)
into Nautilus Bar objects. Handles both daily (date-only) and intraday
(datetime) formats, along with known data issues: leading blank lines,
CRLF line endings, empty files, and float volume notation.

The format is shared by all FirstRate data products (ETF, Stocks, etc.),
so this parser is registered for every supported asset class.
"""

import calendar
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity

from src.models.catalog import AssetClass
from src.services.firstrate.parsers.base import BaseParser, RawBarData, register_parser

logger = structlog.get_logger(__name__)

# FirstRate CSV timestamps are local US Eastern Time with DST.
_FIRSTRATE_TIMEZONE = ZoneInfo("America/New_York")


@register_parser(asset_class=AssetClass.ETF)
@register_parser(asset_class=AssetClass.STOCK)
class FirstRateCsvParser(BaseParser):
    """Parser for FirstRate headerless CSV files.

    Handles both daily (``YYYY-MM-DD``) and intraday
    (``YYYY-MM-DD HH:MM:SS``) timestamp formats. The file format is
    identical across FirstRate asset-class products, so this parser is
    registered for ETF and STOCK alike.
    """

    def parse_file(
        self,
        file_path: Path,
        instrument_id: InstrumentId,
        bar_type: BarType,
    ) -> list[Bar]:
        """Parse a FirstRate CSV file into sorted Bar objects.

        Args:
            file_path: Path to the headerless CSV/txt file.
            instrument_id: Nautilus InstrumentId for the ticker.
            bar_type: Nautilus BarType for the bars.

        Returns:
            List of Bar objects sorted by ts_init ascending.
        """
        self.last_validation = None
        raw_lines = self._read_lines(file_path)
        if not raw_lines:
            return []

        parsed_rows: list[tuple[int, RawBarData]] = []
        for line_num, line in enumerate(raw_lines, start=1):
            row = self._parse_line(line, line_num)
            if row is not None:
                parsed_rows.append((line_num, row))

        # Integrity gate (documented in BaseParser.validate_bars): surface
        # structural issues — high < low, non-positive prices, negative volume —
        # that Nautilus' Bar constructor does NOT reject. Previously only tests
        # called this, so the gate never ran on a real import.
        validation = self.validate_bars([row for _, row in parsed_rows])
        # Expose the raw-row validation so the import loop can surface
        # OHLC/integrity violations in the summary (Story 2.5 AC1/AC4). Invalid
        # rows are dropped before becoming bars, so this is the only place the
        # violations remain visible.
        self.last_validation = validation
        if not validation.valid:
            logger.warning(
                "bar_validation_failed",
                file=str(file_path),
                invalid_rows=validation.invalid_rows,
                row_count=validation.row_count,
                errors=validation.errors[:10],
            )

        bars: list[Bar] = []
        for line_num, row in parsed_rows:
            bar = self._row_to_bar(row, bar_type, line_num)
            if bar is not None:
                bars.append(bar)

        bars.sort(key=lambda b: b.ts_init)
        bars = self._dedup_by_timestamp(bars)
        return bars

    def map_instrument_id(self, ticker: str, bar_type_spec: str) -> InstrumentId:
        """Map ticker to InstrumentId with a default ARCA venue.

        Args:
            ticker: Raw ticker symbol (e.g. "SPY").
            bar_type_spec: Bar type spec string.

        Returns:
            InstrumentId with default .ARCA venue.
        """
        return InstrumentId.from_str(f"{ticker}.ARCA")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _dedup_by_timestamp(bars: list[Bar]) -> list[Bar]:
        """Remove duplicate bars by ts_init, keeping the last occurrence.

        FirstRate source files sometimes contain the last day's bars
        duplicated at the end of the file. When duplicates have different
        OHLCV values, the last occurrence (end-of-day correction) is kept.

        Args:
            bars: Sorted list of Bar objects.

        Returns:
            Deduplicated list preserving sort order.
        """
        if not bars:
            return bars

        seen: dict[int, int] = {}
        for idx, bar in enumerate(bars):
            seen[bar.ts_init] = idx

        if len(seen) == len(bars):
            return bars

        removed = len(bars) - len(seen)
        logger.warning("duplicate_timestamps_removed", count=removed)
        return [bars[idx] for idx in sorted(seen.values())]

    @staticmethod
    def _read_lines(file_path: Path) -> list[str]:
        """Read file and return non-blank lines with CR stripped."""
        text = file_path.read_text(encoding="utf-8")
        lines: list[str] = []
        for raw in text.splitlines():
            stripped = raw.strip("\r").strip()
            if stripped:
                lines.append(stripped)
        return lines

    @staticmethod
    def _parse_line(line: str, line_num: int) -> RawBarData | None:
        """Parse a single CSV line into RawBarData, or None on failure."""
        parts = line.split(",")
        if len(parts) != 6:
            logger.warning("skipping_malformed_row", line_num=line_num, columns=len(parts))
            return None
        return RawBarData(
            timestamp=parts[0].strip(),
            open=parts[1].strip(),
            high=parts[2].strip(),
            low=parts[3].strip(),
            close=parts[4].strip(),
            volume=parts[5].strip(),
        )

    @staticmethod
    def _parse_volume(raw: str) -> Quantity:
        """Parse a FirstRate volume string into an exact Quantity.

        FirstRate emits volume in mixed notation across products — plain
        integers, trailing-zero floats (``"1234567.0"``), and occasional
        scientific notation (``"1.23e6"``, the "float volume notation" quirk
        noted in the module docstring). Normalizing through ``Decimal`` then
        ``format(..., "f")`` handles all three exactly. The previous daily path
        used ``Quantity.from_int(int(float(v)))`` which truncated fractional /
        exponential volume, giving the *same instrument* different precision on
        daily vs intraday bars.
        """
        return Quantity.from_str(format(Decimal(raw), "f"))

    @staticmethod
    def _row_to_bar(row: RawBarData, bar_type: BarType, line_num: int) -> Bar | None:
        """Convert a RawBarData to a Nautilus Bar, or None on failure."""
        try:
            dt = FirstRateCsvParser._parse_timestamp(row.timestamp)
            ts_nanos = calendar.timegm(dt.timetuple()) * 1_000_000_000

            return Bar(
                bar_type=bar_type,
                open=Price.from_str(row.open),
                high=Price.from_str(row.high),
                low=Price.from_str(row.low),
                close=Price.from_str(row.close),
                volume=FirstRateCsvParser._parse_volume(row.volume),
                ts_event=ts_nanos,
                ts_init=ts_nanos,
            )
        except Exception:
            logger.warning("skipping_unparseable_row", line_num=line_num, exc_info=True)
            return None

    @staticmethod
    def _parse_timestamp(ts_str: str) -> datetime:
        """Parse a FirstRate daily or intraday timestamp to a UTC datetime.

        FirstRate CSV files use local US Eastern wall-clock time with DST.
        A row labelled ``2018-01-16 09:30:00`` is 09:30 EST (= 14:30 UTC),
        and ``2018-07-16 09:30:00`` is 09:30 EDT (= 13:30 UTC). Daily
        rows (``YYYY-MM-DD``) localize to midnight ET.

        The previous implementation stamped naive timestamps as UTC,
        producing a 4–5 hour shift (DST-dependent) on every bar. That
        silently broke any downstream comparison against UTC-correct
        feeds (e.g. IBKR ``reqHistoricalData``).
        """
        if " " in ts_str:
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        else:
            dt = datetime.strptime(ts_str, "%Y-%m-%d")
        return dt.replace(tzinfo=_FIRSTRATE_TIMEZONE).astimezone(timezone.utc)
