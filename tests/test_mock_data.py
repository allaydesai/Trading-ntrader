"""Tests for mock data generator."""

import pytest

from src.utils.mock_data import (
    generate_mock_bars,
    generate_mock_dataframe,
    create_test_instrument,
)


def test_create_test_instrument():
    """Test creating a test instrument."""
    instrument, instrument_id = create_test_instrument()

    assert instrument is not None
    assert instrument_id is not None
    assert str(instrument_id).startswith("EUR/USD")


def test_generate_mock_dataframe():
    """Test mock DataFrame generation."""
    df = generate_mock_dataframe(num_bars=100)

    # Check basic structure
    assert len(df) == 100
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]

    # Check OHLC relationships
    for _, row in df.iterrows():
        assert row["high"] >= max(row["open"], row["close"])
        assert row["low"] <= min(row["open"], row["close"])
        assert row["volume"] > 0

    # Check price progression
    assert df["close"].iloc[0] > 0
    assert df["close"].iloc[-1] > 0


def test_generate_mock_bars():
    """Test mock bar generation."""
    instrument, instrument_id = create_test_instrument()

    bars = generate_mock_bars(instrument_id, num_bars=50)

    # Check basic structure
    assert len(bars) == 50

    # Check each bar
    for bar in bars:
        assert bar.open.as_double() > 0
        assert bar.high.as_double() >= max(bar.open.as_double(), bar.close.as_double())
        assert bar.low.as_double() <= min(bar.open.as_double(), bar.close.as_double())
        assert bar.close.as_double() > 0
        assert bar.volume.as_double() > 0


@pytest.mark.trading
def test_mock_data_predictable_pattern():
    """Test that mock data has predictable patterns for SMA crossovers."""
    df = generate_mock_dataframe(num_bars=200)

    # Calculate simple 10 and 20 period SMAs
    df["sma_10"] = df["close"].rolling(window=10).mean()
    df["sma_20"] = df["close"].rolling(window=20).mean()

    # Remove NaN values
    df_clean = df.dropna()

    # Should have crossovers due to sine wave pattern
    crossovers = 0
    for i in range(1, len(df_clean)):
        prev_diff = df_clean["sma_10"].iloc[i - 1] - df_clean["sma_20"].iloc[i - 1]
        curr_diff = df_clean["sma_10"].iloc[i] - df_clean["sma_20"].iloc[i]

        # Detect crossover (sign change)
        if prev_diff * curr_diff < 0:
            crossovers += 1

    # Should have multiple crossovers due to the sine wave pattern
    assert crossovers > 0, "Should generate predictable SMA crossovers"
