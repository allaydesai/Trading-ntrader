"""Tests for SMA crossover strategy."""

from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import BarAggregation, PriceType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.data import TestDataStubs

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


@pytest.mark.trading
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


@pytest.mark.trading
def test_strategy_on_start_subscribes_to_bars():
    """Test that strategy subscribes to bars on start."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    config = SMAConfig(
        instrument_id=instrument.id,
        bar_type=f"{instrument.id}-15-MINUTE-BID-INTERNAL",
    )

    strategy = SMACrossover(config=config)

    # Mock the subscribe_bars method
    with patch.object(strategy, 'subscribe_bars') as mock_subscribe:
        strategy.on_start()
        mock_subscribe.assert_called_once()


@pytest.mark.trading
def test_strategy_on_stop_closes_positions_and_unsubscribes():
    """Test that strategy closes positions and unsubscribes on stop."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    config = SMAConfig(
        instrument_id=instrument.id,
        bar_type=f"{instrument.id}-15-MINUTE-BID-INTERNAL",
    )

    strategy = SMACrossover(config=config)

    # Mock the methods
    with patch.object(strategy, 'close_all_positions') as mock_close, \
         patch.object(strategy, 'unsubscribe_bars') as mock_unsubscribe:

        strategy.on_stop()

        mock_close.assert_called_once_with(instrument.id)
        mock_unsubscribe.assert_called_once()


@pytest.mark.trading
def test_on_bar_updates_indicators_when_not_initialized():
    """Test that on_bar updates indicators but doesn't trade when not initialized."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    config = SMAConfig(
        instrument_id=instrument.id,
        bar_type=f"{instrument.id}-15-MINUTE-BID-INTERNAL",
        fast_period=2,  # Small periods for easier testing
        slow_period=3,
    )

    strategy = SMACrossover(config=config)

    # Create a test bar
    bar = TestDataStubs.bar_5decimal()

    # Mock _check_for_signals to ensure it's not called
    with patch.object(strategy, '_check_for_signals') as mock_check:
        strategy.on_bar(bar)

        # Should not check for signals when indicators aren't initialized
        mock_check.assert_not_called()

        # Indicators should have received the bar
        assert strategy.fast_sma.count == 1
        assert strategy.slow_sma.count == 1


@pytest.mark.trading
def test_on_bar_checks_signals_when_indicators_initialized():
    """Test that on_bar checks for signals when indicators are initialized."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    config = SMAConfig(
        instrument_id=instrument.id,
        bar_type=f"{instrument.id}-15-MINUTE-BID-INTERNAL",
        fast_period=2,
        slow_period=2,  # Same period to initialize quickly
    )

    strategy = SMACrossover(config=config)

    # Feed bars to initialize indicators
    bar1 = TestDataStubs.bar_5decimal()
    bar2 = TestDataStubs.bar_5decimal()

    strategy.on_bar(bar1)
    strategy.on_bar(bar2)  # Both indicators should be initialized now

    # Mock _check_for_signals
    with patch.object(strategy, '_check_for_signals') as mock_check:
        bar3 = TestDataStubs.bar_5decimal()
        strategy.on_bar(bar3)

        # Should check for signals when both indicators are initialized and we have previous values
        mock_check.assert_called_once()


@pytest.mark.trading
def test_check_for_signals_bullish_crossover():
    """Test detection of bullish crossover (fast SMA crosses above slow SMA)."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    config = SMAConfig(
        instrument_id=instrument.id,
        bar_type=f"{instrument.id}-15-MINUTE-BID-INTERNAL",
    )

    strategy = SMACrossover(config=config)

    # Set up crossover scenario: fast was below slow, now fast is above slow
    strategy._prev_fast_sma = 1.0950  # Previously below slow
    strategy._prev_slow_sma = 1.0960

    with patch.object(strategy, '_generate_buy_signal') as mock_buy, \
         patch.object(strategy, '_generate_sell_signal') as mock_sell:

        # Current: fast above slow (crossover)
        strategy._check_for_signals(1.0965, 1.0958)

        mock_buy.assert_called_once()
        mock_sell.assert_not_called()


@pytest.mark.trading
def test_check_for_signals_bearish_crossover():
    """Test detection of bearish crossover (fast SMA crosses below slow SMA)."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    config = SMAConfig(
        instrument_id=instrument.id,
        bar_type=f"{instrument.id}-15-MINUTE-BID-INTERNAL",
    )

    strategy = SMACrossover(config=config)

    # Set up crossover scenario: fast was above slow, now fast is below slow
    strategy._prev_fast_sma = 1.0970  # Previously above slow
    strategy._prev_slow_sma = 1.0960

    with patch.object(strategy, '_generate_buy_signal') as mock_buy, \
         patch.object(strategy, '_generate_sell_signal') as mock_sell:

        # Current: fast below slow (crossover)
        strategy._check_for_signals(1.0955, 1.0962)

        mock_sell.assert_called_once()
        mock_buy.assert_not_called()


@pytest.mark.trading
def test_check_for_signals_no_crossover():
    """Test no signals when there's no crossover."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    config = SMAConfig(
        instrument_id=instrument.id,
        bar_type=f"{instrument.id}-15-MINUTE-BID-INTERNAL",
    )

    strategy = SMACrossover(config=config)

    # Set up no crossover scenario: fast remains above slow
    strategy._prev_fast_sma = 1.0970
    strategy._prev_slow_sma = 1.0960

    with patch.object(strategy, '_generate_buy_signal') as mock_buy, \
         patch.object(strategy, '_generate_sell_signal') as mock_sell:

        # Current: fast still above slow (no crossover)
        strategy._check_for_signals(1.0975, 1.0965)

        mock_buy.assert_not_called()
        mock_sell.assert_not_called()


# Note: Signal generation tests are complex to unit test in isolation
# as they require Nautilus Trader infrastructure (order_factory, cache).
# These are better covered by integration tests.


@pytest.mark.trading
def test_on_event_without_position_id():
    """Test that on_event handles events without position_id attribute."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    config = SMAConfig(
        instrument_id=instrument.id,
        bar_type=f"{instrument.id}-15-MINUTE-BID-INTERNAL",
    )

    strategy = SMACrossover(config=config)

    # Mock event without position_id - should not access cache
    mock_event = Mock(spec=[])  # Empty spec means no attributes

    # This should not raise an exception (no position_id means no cache access)
    strategy.on_event(mock_event)


@pytest.mark.trading
def test_on_dispose_does_nothing():
    """Test that on_dispose completes without error."""
    instrument = TestInstrumentProvider.default_fx_ccy("EUR/USD")
    config = SMAConfig(
        instrument_id=instrument.id,
        bar_type=f"{instrument.id}-15-MINUTE-BID-INTERNAL",
    )

    strategy = SMACrossover(config=config)

    # Should not raise any exception
    strategy.on_dispose()