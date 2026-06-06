"""Shared result dataclass for backtest data loading."""

from dataclasses import dataclass

from nautilus_trader.model.data import Bar
from nautilus_trader.model.instruments import Instrument


@dataclass
class DataLoadResult:
    """Result of loading backtest data from catalog or mock generation.

    Attributes:
        bars: List of loaded or generated Bar objects
        instrument: The instrument for the backtest
        data_source_used: Source description ("Parquet Catalog", "IBKR Auto-fetch", "Mock")
    """

    bars: list[Bar]
    instrument: Instrument
    data_source_used: str
