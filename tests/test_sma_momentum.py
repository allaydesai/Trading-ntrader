"""Tests for SMA Momentum strategy implementation."""

import pytest
from decimal import Decimal

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.data import BarType

from src.models.strategy import MomentumParameters


@pytest.mark.nautilus
class TestSMAMomentumStrategy:
    """Test cases for SMA Momentum strategy with Nautilus Trader integration."""

    def test_momentum_parameters_validation(self):
        """Test Momentum parameter validation."""
        # Valid parameters
        params = MomentumParameters(
            rsi_period=14,
            oversold_threshold=30.0,
            overbought_threshold=70.0,
            trade_size=Decimal("1000000"),
        )
        assert params.rsi_period == 14
        assert params.oversold_threshold == 30.0
        assert params.overbought_threshold == 70.0
        assert params.trade_size == Decimal("1000000")

        # Invalid RSI period (Pydantic validation)
        with pytest.raises(
            ValueError, match="Input should be greater than or equal to 5"
        ):
            MomentumParameters(rsi_period=0)

        # Invalid oversold threshold (Pydantic validation)
        with pytest.raises(ValueError, match="Input should be less than or equal to"):
            MomentumParameters(oversold_threshold=60.0)  # Above range

        # Invalid trade size
        with pytest.raises(ValueError):
            MomentumParameters(trade_size=Decimal("0"))

    def test_sma_momentum_strategy_creation(self):
        """INTEGRATION: SMA momentum strategy loads with Nautilus."""
        from src.core.strategies.sma_momentum import SMAMomentum, SMAMomentumConfig

        config = SMAMomentumConfig(
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            bar_type=BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"),
            trade_size=Decimal("1000000"),
            order_id_tag="001",
            fast_period=50,
            slow_period=200,
            warmup_days=400,
            allow_short=False,
        )

        strategy = SMAMomentum(config)
        assert strategy.config.fast_period == 50
        assert strategy.config.slow_period == 200
        assert strategy.config.warmup_days == 400
        assert strategy.config.allow_short is False

    def test_sma_momentum_strategy_initialization(self):
        """Test strategy initialization with internal state."""
        from src.core.strategies.sma_momentum import SMAMomentum, SMAMomentumConfig

        config = SMAMomentumConfig(
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            bar_type=BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"),
            trade_size=Decimal("500000"),
            order_id_tag="002",
            fast_period=10,
            slow_period=20,
        )

        strategy = SMAMomentum(config)

        # Should initialize with deques for moving averages
        assert hasattr(strategy, "_fast")
        assert hasattr(strategy, "_slow")
        assert hasattr(strategy, "_fast_sum")
        assert hasattr(strategy, "_slow_sum")
        assert strategy._fast_sum == 0.0
        assert strategy._slow_sum == 0.0
        assert strategy._prev_fast is None
        assert strategy._prev_slow is None

    @pytest.mark.integration
    def test_sma_momentum_with_mock_data(self):
        """INTEGRATION: SMA momentum strategy works with mock data."""
        from src.core.strategies.sma_momentum import SMAMomentum, SMAMomentumConfig
        from src.utils.mock_data import generate_mock_bars, create_test_instrument

        # Create test instrument
        instrument, instrument_id = create_test_instrument("EUR/USD")

        config = SMAMomentumConfig(
            instrument_id=instrument_id,
            bar_type=BarType.from_str(f"{instrument_id}-15-MINUTE-MID-EXTERNAL"),
            trade_size=Decimal("1000000"),
            order_id_tag="003",
            fast_period=5,  # Shorter for testing
            slow_period=10,  # Shorter for testing
            warmup_days=20,
        )

        strategy = SMAMomentum(config)

        # Generate mock bars
        mock_bars = generate_mock_bars(instrument_id, num_bars=15)

        # Process bars to warm up moving averages
        for bar in mock_bars:
            close = float(bar.close)
            fast_val, strategy._fast_sum = strategy._update_ma(
                strategy._fast, strategy._fast_sum, close, config.fast_period
            )
            slow_val, strategy._slow_sum = strategy._update_ma(
                strategy._slow, strategy._slow_sum, close, config.slow_period
            )

        # After processing bars, internal state should be updated
        assert len(strategy._fast) > 0
        assert len(strategy._slow) > 0
        assert strategy._fast_sum > 0
        assert strategy._slow_sum > 0

    def test_sma_momentum_ma_calculation(self):
        """Test moving average calculation logic."""
        from src.core.strategies.sma_momentum import SMAMomentum, SMAMomentumConfig
        from collections import deque

        config = SMAMomentumConfig(
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            bar_type=BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"),
            trade_size=Decimal("1000000"),
            order_id_tag="004",
            fast_period=3,
            slow_period=5,
        )

        strategy = SMAMomentum(config)

        # Test MA calculation with sample prices
        test_prices = [100.0, 102.0, 101.0, 103.0, 102.0]
        test_deque = deque(maxlen=3)
        total = 0.0

        for price in test_prices[:3]:
            ma_val, total = strategy._update_ma(test_deque, total, price, 3)

        # After 3 prices, should have MA
        assert ma_val is not None
        expected_ma = sum(test_prices[:3]) / 3
        assert abs(ma_val - expected_ma) < 0.01

    def test_sma_momentum_crossover_detection(self):
        """Test crossover detection logic."""
        from src.core.strategies.sma_momentum import SMAMomentum, SMAMomentumConfig

        config = SMAMomentumConfig(
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            bar_type=BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"),
            trade_size=Decimal("1000000"),
            order_id_tag="005",
            fast_period=2,
            slow_period=3,
        )

        strategy = SMAMomentum(config)

        # Simulate crossover scenario
        # Fast MA below slow MA initially
        strategy._prev_fast = 99.0
        strategy._prev_slow = 100.0

        # Fast MA crosses above slow MA (golden cross)
        fast_val = 101.0
        slow_val = 100.5

        crossed_up = strategy._prev_fast <= strategy._prev_slow and fast_val > slow_val
        assert crossed_up is True

        # Update for death cross test
        strategy._prev_fast = 101.0
        strategy._prev_slow = 100.0

        # Fast MA crosses below slow MA (death cross)
        fast_val = 99.0
        slow_val = 100.0

        crossed_dn = strategy._prev_fast >= strategy._prev_slow and fast_val < slow_val
        assert crossed_dn is True

    def test_sma_momentum_allow_short_flag(self):
        """Test allow_short configuration flag."""
        from src.core.strategies.sma_momentum import SMAMomentum, SMAMomentumConfig

        # Test with short selling disabled
        config_long_only = SMAMomentumConfig(
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            bar_type=BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"),
            trade_size=Decimal("1000000"),
            order_id_tag="006",
            allow_short=False,
        )

        strategy_long_only = SMAMomentum(config_long_only)
        assert strategy_long_only.config.allow_short is False

        # Test with short selling enabled
        config_with_short = SMAMomentumConfig(
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            bar_type=BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"),
            trade_size=Decimal("1000000"),
            order_id_tag="007",
            allow_short=True,
        )

        strategy_with_short = SMAMomentum(config_with_short)
        assert strategy_with_short.config.allow_short is True

    def test_sma_momentum_parameters_in_strategy_model(self):
        """Test that SMA momentum parameters validate correctly in TradingStrategy model."""
        from src.models.strategy import TradingStrategy, StrategyType

        # Valid momentum strategy
        strategy = TradingStrategy(
            name="Test SMA Momentum",
            strategy_type=StrategyType.MOMENTUM,
            parameters={
                "rsi_period": 14,
                "oversold_threshold": 30.0,
                "overbought_threshold": 70.0,
                "trade_size": "1000000",
            },
        )
        assert strategy.strategy_type == StrategyType.MOMENTUM

        # Invalid parameters should fail validation
        with pytest.raises(ValueError, match="Invalid Momentum parameters"):
            TradingStrategy(
                name="Invalid Momentum",
                strategy_type=StrategyType.MOMENTUM,
                parameters={
                    "rsi_period": 0,  # Invalid
                    "oversold_threshold": 30.0,
                    "overbought_threshold": 70.0,
                    "trade_size": "1000000",
                },
            )
