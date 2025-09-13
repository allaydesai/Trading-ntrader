"""Tests for SMA crossover strategy."""

from decimal import Decimal

from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.core.strategies.sma_crossover import SMACrossover, SMAConfig


def test_sma_config_creation():
    """Test SMA strategy configuration creation."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")

    config = SMAConfig(
        instrument_id=instrument.id,
        bar_type=f"{instrument.id}-15-MINUTE-BID-INTERNAL",
        fast_period=10,
        slow_period=20,
        trade_size=Decimal("1_000_000"),
    )

    assert config.instrument_id == instrument.id
    assert config.fast_period == 10
    assert config.slow_period == 20
    assert config.trade_size == Decimal("1_000_000")


def test_sma_strategy_initialization():
    """Test SMA strategy initialization."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")

    config = SMAConfig(
        instrument_id=instrument.id,
        bar_type=f"{instrument.id}-15-MINUTE-BID-INTERNAL",
        fast_period=10,
        slow_period=20,
        trade_size=Decimal("1_000_000"),
    )

    strategy = SMACrossover(config=config)

    assert strategy.instrument_id == instrument.id
    assert strategy.fast_sma.period == 10
    assert strategy.slow_sma.period == 20
    assert not strategy.fast_sma.initialized
    assert not strategy.slow_sma.initialized
