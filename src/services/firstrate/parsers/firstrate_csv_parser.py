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
from pathlib import Path

import structlog
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import BarAggregation
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity

from src.models.catalog import AssetClass
from src.services.firstrate.parsers.base import BaseParser, RawBarData, register_parser

logger = structlog.get_logger(__name__)


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
        raw_lines = self._read_lines(file_path)
        if not raw_lines:
            return []

        is_daily = bar_type.spec.aggregation == BarAggregation.DAY
        bars: list[Bar] = []
        for line_num, line in enumerate(raw_lines, start=1):
            row = self._parse_line(line, line_num)
            if row is None:
                continue
            bar = self._row_to_bar(row, bar_type, line_num, is_daily)
            if bar is not None:
                bars.append(bar)

        bars.sort(key=lambda b: b.ts_init)
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
    def _row_to_bar(
        row: RawBarData, bar_type: BarType, line_num: int, is_daily: bool
    ) -> Bar | None:
        """Convert a RawBarData to a Nautilus Bar, or None on failure."""
        try:
            dt = FirstRateCsvParser._parse_timestamp(row.timestamp)
            ts_nanos = calendar.timegm(dt.timetuple()) * 1_000_000_000

            if is_daily:
                volume = Quantity.from_int(int(float(row.volume)))
            else:
                volume = Quantity.from_str(row.volume)

            return Bar(
                bar_type=bar_type,
                open=Price.from_str(row.open),
                high=Price.from_str(row.high),
                low=Price.from_str(row.low),
                close=Price.from_str(row.close),
                volume=volume,
                ts_event=ts_nanos,
                ts_init=ts_nanos,
            )
        except Exception:
            logger.warning("skipping_unparseable_row", line_num=line_num, exc_info=True)
            return None

    @staticmethod
    def _parse_timestamp(ts_str: str) -> datetime:
        """Parse daily or intraday timestamp string to UTC datetime."""
        if " " in ts_str:
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        else:
            dt = datetime.strptime(ts_str, "%Y-%m-%d")
        return dt.replace(tzinfo=timezone.utc)
