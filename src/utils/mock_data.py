"""Mock data generator for backtesting."""

import math
from datetime import datetime, timedelta
from typing import List

import pandas as pd
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.config import get_settings


def generate_mock_bars(
    instrument_id: InstrumentId,
    num_bars: int = None,
    start_price: float = 1.1000,
    volatility: float = 0.002,
    trend_strength: float = 0.0001,
    start_time: datetime = None,
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

    # Generate price series with predictable patterns
    for i in range(num_bars):
        # Create sine wave pattern for predictable crossovers
        cycle_position = (i / 50.0) * 2 * math.pi  # 50-bar cycle

        # Base price with trend and cycle
        base_price = start_price + (i * trend_strength)

        # Add sine wave oscillation
        sine_component = math.sin(cycle_position) * volatility * start_price

        # Generate OHLC prices
        close_price = base_price + sine_component

        # Add some intrabar variation
        high_variation = abs(
            math.sin(cycle_position + 0.5) * volatility * 0.5 * start_price
        )
        low_variation = abs(
            math.sin(cycle_position - 0.5) * volatility * 0.5 * start_price
        )

        high_price = close_price + high_variation
        low_price = close_price - low_variation

        # Open is previous close (with small gap)
        if i == 0:
            open_price = close_price
        else:
            gap = math.sin(cycle_position * 0.1) * volatility * 0.1 * start_price
            open_price = close_price + gap

        # Ensure OHLC relationships are maintained
        high_price = max(high_price, open_price, close_price)
        low_price = min(low_price, open_price, close_price)

        # Generate random but realistic volume
        volume = 1000000 + (500000 * abs(math.sin(cycle_position * 1.3)))

        # Create bar type using string format
        # Use MID-EXTERNAL to match DataClient required aggregation source in backtests
        bar_type = BarType.from_str(f"{instrument_id}-15-MINUTE-MID-EXTERNAL")

        # Create the bar
        bar = Bar(
            bar_type=bar_type,
            open=Price.from_str(f"{open_price:.5f}"),
            high=Price.from_str(f"{high_price:.5f}"),
            low=Price.from_str(f"{low_price:.5f}"),
            close=Price.from_str(f"{close_price:.5f}"),
            volume=Quantity.from_int(int(volume)),
            ts_event=int(current_time.timestamp() * 1_000_000_000),
            ts_init=int(current_time.timestamp() * 1_000_000_000),
        )

        bars.append(bar)
        current_time += timedelta(minutes=15)

    return bars


def generate_mock_dataframe(
    num_bars: int = None,
    start_price: float = 1.1000,
    volatility: float = 0.002,
    trend_strength: float = 0.0001,
    start_time: datetime = None,
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

        high_variation = abs(
            math.sin(cycle_position + 0.5) * volatility * 0.5 * start_price
        )
        low_variation = abs(
            math.sin(cycle_position - 0.5) * volatility * 0.5 * start_price
        )

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


def create_test_instrument() -> tuple:
    """
    Create a test EUR/USD instrument for backtesting.

    Returns
    -------
    tuple
        (instrument, instrument_id) tuple.
    """
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
