"""Explorer view models for web UI and REST API.

Provides data structures for ticker list display, pagination,
and explorer page state management.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, computed_field

from src.api.models.chart_timeseries import Candle

EXPLORER_PAGE_SIZE = 25


class ExplorerTimeframe(Enum):
    """Timeframe options for the explorer chart panel.

    Each member maps a display label to a Nautilus bar type spec
    and the corresponding CatalogInstrument bar_count field.
    """

    DAILY = ("D", "1-DAY-LAST", "bar_count_daily")
    HOURLY = ("1H", "1-HOUR-LAST", "bar_count_hourly")
    FIVE_MIN = ("5m", "5-MINUTE-LAST", "bar_count_5min")
    ONE_MIN = ("1m", "1-MINUTE-LAST", "bar_count_minute")

    def __init__(self, label: str, bar_type_spec: str, bar_count_field: str):
        self._label = label
        self._bar_type_spec = bar_type_spec
        self._bar_count_field = bar_count_field

    @property
    def label(self) -> str:
        return self._label

    @property
    def bar_type_spec(self) -> str:
        return self._bar_type_spec

    @property
    def bar_count_field(self) -> str:
        return self._bar_count_field

    @classmethod
    def from_label(cls, label: str) -> "ExplorerTimeframe":
        """Resolve a display label to an ExplorerTimeframe, defaulting to DAILY."""
        for member in cls:
            if member.label == label:
                return member
        return cls.DAILY


class ChartDataResponse(BaseModel):
    """Chart data response for TradingView Lightweight Charts.

    Attributes:
        bars: List of OHLCV candles in TradingView format.
        instrument_id: Nautilus instrument ID (e.g., "SPY.ARCA").
        timeframe: Bar type description (e.g., "1-DAY").
        bar_count: Total number of bars returned.
    """

    bars: list[Candle] = Field(default_factory=list)
    instrument_id: str = Field(..., description="Nautilus instrument ID")
    timeframe: str = Field(..., description="Bar timeframe description")
    bar_count: int = Field(..., ge=0, description="Number of bars returned")


class TickerRow(BaseModel):
    """Single ticker row in the explorer table.

    Attributes:
        ticker: Trading symbol (e.g., "AAPL").
        name: Full instrument name (nullable for ETFs without metadata).
        asset_class: Asset class string (e.g., "STOCK", "ETF").
        date_range_start: Earliest imported data timestamp.
        date_range_end: Latest imported data timestamp.
        bar_count_daily: Number of daily bars.
        bar_count_hourly: Number of hourly bars.
        bar_count_5min: Number of 5-minute bars.
        bar_count_minute: Number of 1-minute bars.
        nautilus_id: Nautilus Trader instrument ID (nullable).
        coverage_pct: Computed coverage percentage based on date range.
    """

    ticker: str
    name: Optional[str] = None
    asset_class: str
    date_range_start: Optional[datetime] = None
    date_range_end: Optional[datetime] = None
    bar_count_daily: int = 0
    bar_count_hourly: int = 0
    bar_count_5min: int = 0
    bar_count_minute: int = 0
    nautilus_id: Optional[str] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def coverage_pct(self) -> Optional[float]:
        """Compute coverage as percentage of date range vs today.

        Returns None if date range is not available.
        """
        if not self.date_range_start or not self.date_range_end:
            return None
        now = datetime.now(timezone.utc)
        total_span = (now - self.date_range_start).days
        if total_span <= 0:
            return 100.0
        covered = (self.date_range_end - self.date_range_start).days
        return round(min(covered / total_span * 100.0, 100.0), 1)


class TickerListResponse(BaseModel):
    """Paginated ticker list response for REST API.

    Attributes:
        tickers: List of ticker rows for current page.
        total_count: Total number of matching tickers.
        page: Current page number (1-based).
        page_size: Number of items per page.
        total_pages: Total number of pages.
    """

    tickers: list[TickerRow] = Field(default_factory=list)
    total_count: int = 0
    page: int = 1
    page_size: int = 25
    total_pages: int = 0


class ExplorerPageState(BaseModel):
    """Explorer page filter/pagination state for URL params.

    Attributes:
        catalog: Selected catalog name.
        search: Search query string.
        asset_class: Active asset class filter (empty = all).
        page: Current page number (1-based).
        sort_by: Sort column name.
    """

    catalog: str
    search: str = ""
    asset_class: str = ""
    page: int = Field(default=1, ge=1)
    sort_by: str = "ticker"
