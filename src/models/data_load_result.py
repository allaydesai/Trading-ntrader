"""Shared result dataclass for backtest data loading."""

from dataclasses import dataclass, field

from nautilus_trader.model.data import Bar
from nautilus_trader.model.instruments import Instrument


@dataclass
class DataLoadResult:
    """Result of loading backtest data from catalog or mock generation.

    Attributes:
        bars: List of loaded or generated Bar objects
        instrument: The instrument for the backtest
        data_source_used: Source description ("Parquet Catalog", "IBKR Auto-fetch", "Mock")
        rate_sources: FX only: conversion-only ``(bars, instrument)`` pairs the run's USD
            account converts with (GBPUSD for an EURGBP run), from the same catalog and
            timeframe; empty for every other run (FX M3)
    """

    bars: list[Bar]
    instrument: Instrument
    data_source_used: str
    rate_sources: list[tuple[list[Bar], Instrument]] = field(default_factory=list)
