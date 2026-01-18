"""Mock data generator for backtesting."""

import math
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.config import get_settings


def generate_mock_bars(
    instrument_id: InstrumentId,
    num_bars: Optional[int] = None,
    start_price: float = 1.1000,
    volatility: float = 0.002,
    trend_strength: float = 0.0001,
    start_time: Optional[datetime] = None,
    bar_type_str: Optional[str] = None,
    price_precision: int = 5,
) -> List[Bar]:
    """
    Generate synthetic bar data with predictable patterns.

    Creates OHLCV bars using sine wave patterns to ensure predictable SMA crossovers
    for testing purposes.

    Parameters
    ----------
    instrument_id : InstrumentId
        The instrument to generate data for.
    num_bars : int, optional
        Number of bars to generate. Defaults to config value.
    start_price : float, default 1.1000
        Starting price for the data generation.
    volatility : float, default 0.002
        Price volatility factor.
    trend_strength : float, default 0.0001
        Overall trend strength.
    start_time : datetime, optional
        Start time for the bars. Defaults to 30 days ago.
    bar_type_str : str, optional
        Custom bar type string (e.g., "QQQ.SIM-1-DAY-LAST-EXTERNAL").
        Defaults to "{instrument_id}-15-MINUTE-MID-EXTERNAL".
    price_precision : int, default 5
        Number of decimal places for prices (2 for equities, 5 for FX).

    Returns
    -------
    List[Bar]
        List of generated bars.
    """
    settings = get_settings()

    if num_bars is None:
        num_bars = settings.mock_data_bars

    if start_time is None:
        start_time = datetime.now() - timedelta(days=30)

    bars = []
    current_time = start_time
    prev_close = start_price

    # Generate price series with realistic patterns for RSI strategies
    # Uses a combination of trend, mean reversion, and random noise
    for i in range(num_bars):
        # Multi-frequency oscillation for more realistic price action
        cycle_position = (i / 50.0) * 2 * math.pi  # Primary 50-bar cycle
        fast_cycle = (i / 10.0) * 2 * math.pi  # Fast 10-bar cycle for noise
        momentum_cycle = (i / 100.0) * 2 * math.pi  # Slow momentum cycle

        # Base price with trend
        base_price = start_price + (i * trend_strength)

        # Primary sine wave oscillation
        primary_component = math.sin(cycle_position) * volatility * start_price

        # Add fast oscillation for more varied price action (creates RSI swings)
        fast_component = math.sin(fast_cycle) * volatility * start_price * 0.5

        # Add momentum factor (creates extended up/down runs)
        momentum_component = math.sin(momentum_cycle) * volatility * start_price * 0.3

        # Combine all components for close price
        close_price = base_price + primary_component + fast_component + momentum_component

        # Add some "random" variation using golden ratio for deterministic randomness
        golden_ratio = 1.618033988749895
        pseudo_random = math.sin(i * golden_ratio) * volatility * start_price * 0.2
        close_price += pseudo_random

        # Ensure price stays positive
        close_price = max(close_price, start_price * 0.5)

        # Generate OHLC with more realistic intrabar variation
        daily_range = abs(volatility * start_price * 0.7)
        high_variation = abs(math.sin(cycle_position + 0.5) * daily_range)
        low_variation = abs(math.sin(cycle_position - 0.5) * daily_range)

        high_price = close_price + high_variation
        low_price = close_price - low_variation

        # Open is based on previous close with gap
        if i == 0:
            open_price = close_price
        else:
            gap_factor = math.sin(fast_cycle * 0.7) * volatility * 0.1
            open_price = prev_close * (1 + gap_factor)

        # Ensure OHLC relationships are maintained
        high_price = max(high_price, open_price, close_price)
        low_price = min(low_price, open_price, close_price)

        # Store for next iteration
        prev_close = close_price

        # Generate realistic volume
        volume = 1000000 + (500000 * abs(math.sin(cycle_position * 1.3)))

        # Create bar type using string format
        # Use custom bar_type_str if provided, otherwise default to 15-MINUTE-MID-EXTERNAL
        if bar_type_str:
            bar_type = BarType.from_str(bar_type_str)
        else:
            bar_type = BarType.from_str(f"{instrument_id}-15-MINUTE-MID-EXTERNAL")

        # Create the bar with appropriate price precision
        bar = Bar(
            bar_type=bar_type,
            open=Price.from_str(f"{open_price:.{price_precision}f}"),
            high=Price.from_str(f"{high_price:.{price_precision}f}"),
            low=Price.from_str(f"{low_price:.{price_precision}f}"),
            close=Price.from_str(f"{close_price:.{price_precision}f}"),
            volume=Quantity.from_int(int(volume)),
            ts_event=int(current_time.timestamp() * 1_000_000_000),
            ts_init=int(current_time.timestamp() * 1_000_000_000),
        )

        bars.append(bar)

        # Adjust time delta based on bar type
        if bar_type_str and "DAY" in bar_type_str.upper():
            current_time += timedelta(days=1)
        elif bar_type_str and "HOUR" in bar_type_str.upper():
            current_time += timedelta(hours=1)
        else:
            current_time += timedelta(minutes=15)

    return bars


def generate_mock_dataframe(
    num_bars: Optional[int] = None,
    start_price: float = 1.1000,
    volatility: float = 0.002,
    trend_strength: float = 0.0001,
    start_time: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    Generate synthetic OHLCV data as a pandas DataFrame.

    Parameters
    ----------
    num_bars : int, optional
        Number of bars to generate.
    start_price : float, default 1.1000
        Starting price.
    volatility : float, default 0.002
        Price volatility.
    trend_strength : float, default 0.0001
        Trend strength.
    start_time : datetime, optional
        Start time.

    Returns
    -------
    pd.DataFrame
        DataFrame with OHLCV data.
    """
    settings = get_settings()

    if num_bars is None:
        num_bars = settings.mock_data_bars

    if start_time is None:
        start_time = datetime.now() - timedelta(days=30)

    data = []
    current_time = start_time

    for i in range(num_bars):
        # Create sine wave pattern
        cycle_position = (i / 50.0) * 2 * math.pi

        base_price = start_price + (i * trend_strength)
        sine_component = math.sin(cycle_position) * volatility * start_price

        close_price = base_price + sine_component

        high_variation = abs(math.sin(cycle_position + 0.5) * volatility * 0.5 * start_price)
        low_variation = abs(math.sin(cycle_position - 0.5) * volatility * 0.5 * start_price)

        high_price = close_price + high_variation
        low_price = close_price - low_variation

        if i == 0:
            open_price = close_price
        else:
            gap = math.sin(cycle_position * 0.1) * volatility * 0.1 * start_price
            open_price = close_price + gap

        high_price = max(high_price, open_price, close_price)
        low_price = min(low_price, open_price, close_price)

        volume = 1000000 + (500000 * abs(math.sin(cycle_position * 1.3)))

        data.append(
            {
                "timestamp": current_time,
                "open": round(open_price, 5),
                "high": round(high_price, 5),
                "low": round(low_price, 5),
                "close": round(close_price, 5),
                "volume": int(volume),
            }
        )

        current_time += timedelta(minutes=15)

    return pd.DataFrame(data)


def create_test_instrument(symbol: str = "EUR/USD", venue: str = "SIM") -> tuple:
    """
    Create a test instrument for backtesting.

    Args:
        symbol: Trading symbol (e.g., "EUR/USD", "AAPL", "QQQ")
        venue: Venue for the instrument (default "SIM")

    Returns:
        tuple: (instrument, instrument_id) tuple.
    """
    # For FX pairs, use the FX provider
    if "/" in symbol and len(symbol.split("/")) == 2:
        instrument = TestInstrumentProvider.default_fx_ccy(symbol)
    else:
        # For equity symbols, create an equity instrument
        # Truncate long symbols to fit Nautilus constraints (max 7 chars)
        clean_symbol = symbol.replace("2018", "18").replace("_", "")[:7]

        try:
            instrument = TestInstrumentProvider.equity(symbol=clean_symbol, venue=venue)
        except Exception:
            # If equity creation fails, fall back to using FX template with SIM venue
            # This ensures compatibility with the backtest engine
            instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")

    return instrument, instrument.id


if __name__ == "__main__":
    # Demo usage
    instrument, instrument_id = create_test_instrument()

    # Generate bars
    bars = generate_mock_bars(instrument_id, num_bars=100)
    print(f"Generated {len(bars)} bars")
    print(f"First bar: {bars[0]}")
    print(f"Last bar: {bars[-1]}")

    # Generate DataFrame
    df = generate_mock_dataframe(num_bars=100)
    print(f"\nDataFrame shape: {df.shape}")
    print(f"DataFrame head:\n{df.head()}")
