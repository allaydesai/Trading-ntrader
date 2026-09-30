"""Tests for SMA Momentum strategy implementation."""

from decimal import Decimal

import pytest
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId

from src.models.strategy import MomentumParameters


@pytest.mark.component
class TestTheLiveDefaultTradeSize:
    """``trade_size`` defaults to 100 shares in all three places that carry it
    (Epic 4 retro; PR #35 code review, P7). ``Settings.trade_size`` is pinned
    in ``tests/unit/test_config.py``; these are the other two. 1,000,000 was
    inert only while the strategy's SMA could never cross (Story 4.4 fixed
    that), so a drift back would reach a real paper account as a real-money-
    scale order."""

    def test_the_parameter_model_default(self):
        assert MomentumParameters().trade_size == Decimal("100")

    def test_the_registry_default(self):
        from src.core.strategies.sma_momentum import SMAMomentum  # noqa: F401  # registers it
        from src.core.strategy_registry import StrategyRegistry

        assert StrategyRegistry.get("momentum").default_config["trade_size"] == 100

    def test_the_three_defaults_agree(self):
        """One value, three carriers — a drift in any one goes red by name."""
        from src.config import Settings
        from src.core.strategies.sma_momentum import SMAMomentum  # noqa: F401  # registers it
        from src.core.strategy_registry import StrategyRegistry

        registry = Decimal(StrategyRegistry.get("momentum").default_config["trade_size"])
        settings = Settings.model_fields["trade_size"].default

        assert MomentumParameters().trade_size == registry == Decimal(settings)


@pytest.mark.component
class TestSMAMomentumStrategy:
    """Test cases for SMA Momentum strategy with Nautilus Trader integration."""

    def test_momentum_parameters_validation(self):
        """Test Momentum parameter validation."""
        # Valid parameters
        params = MomentumParameters(
            fast_period=20,
            slow_period=50,
            trade_size=Decimal("1000000"),
            allow_short=False,
        )
        assert params.fast_period == 20
        assert params.slow_period == 50
        assert params.trade_size == Decimal("1000000")
        assert params.allow_short is False

        # Invalid periods (Pydantic validation)
        with pytest.raises(ValueError, match="Input should be greater than or equal to 1"):
            MomentumParameters(fast_period=0)

        # Invalid relationship (custom validator)
        with pytest.raises(ValueError, match="Slow period must be greater than fast period"):
            MomentumParameters(fast_period=50, slow_period=20)

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

        # Story 4.4 (D-G): two Nautilus SMAs, registered in `on_start`, replaced
        # the hand-rolled deques.
        assert strategy.fast_sma.period == 10
        assert strategy.slow_sma.period == 20
        assert not strategy.fast_sma.initialized
        assert not strategy.slow_sma.initialized
        assert strategy._prev_fast is None
        assert strategy._prev_slow is None

    def test_sma_momentum_with_mock_data(self):
        """INTEGRATION: SMA momentum strategy works with mock data."""
        from src.core.strategies.sma_momentum import SMAMomentum, SMAMomentumConfig
        from src.utils.mock_data import create_test_instrument, generate_mock_bars

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

        # Process bars to warm up moving averages. In a session `Actor.handle_bar`
        # feeds the registered indicators; here they are fed directly.
        for bar in mock_bars:
            strategy.fast_sma.handle_bar(bar)
            strategy.slow_sma.handle_bar(bar)

        # After processing bars, both averages are warm and are true averages
        # of their own windows.
        closes = [float(bar.close) for bar in mock_bars]
        assert strategy.fast_sma.initialized and strategy.slow_sma.initialized
        assert abs(strategy.fast_sma.value - sum(closes[-5:]) / 5) < 1e-9
        assert abs(strategy.slow_sma.value - sum(closes[-10:]) / 10) < 1e-9

    def test_sma_momentum_ma_calculation(self):
        """The average is a moving window, including after the window slides.

        Story 4.4 (D-G, story F8): the deque version this replaced passed the
        old form of this test because it stopped after the **first three**
        prices, before anything had to be evicted. Past that point it returned
        the running sum of every close divided by the period, so ``fast`` sat
        above ``slow`` forever and the strategy could never cross.
        """
        from src.core.strategies.sma_momentum import SMAMomentum, SMAMomentumConfig

        config = SMAMomentumConfig(
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            bar_type=BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"),
            trade_size=Decimal("1000000"),
            order_id_tag="004",
            fast_period=3,
            slow_period=5,
        )

        strategy = SMAMomentum(config)

        # Longer than both windows, so the fast one has slid four times.
        test_prices = [100.0, 102.0, 101.0, 103.0, 102.0, 110.0, 90.0]
        for price in test_prices:
            strategy.fast_sma.update_raw(price)
            strategy.slow_sma.update_raw(price)

        assert abs(strategy.fast_sma.value - sum(test_prices[-3:]) / 3) < 1e-9
        assert abs(strategy.slow_sma.value - sum(test_prices[-5:]) / 5) < 1e-9

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
        from src.models.strategy import TradingStrategy

        # Valid momentum strategy
        strategy = TradingStrategy(
            name="Test SMA Momentum",
            strategy_type="momentum",
            parameters={
                "fast_period": 20,
                "slow_period": 50,
                "trade_size": "1000000",
                "allow_short": False,
            },
        )
        assert strategy.strategy_type == "momentum"

        # Invalid parameters should fail validation
        with pytest.raises(ValueError, match="Invalid Momentum parameters"):
            TradingStrategy(
                name="Invalid Momentum",
                strategy_type="momentum",
                parameters={
                    "fast_period": 0,  # Invalid
                    "slow_period": 50,
                    "trade_size": "1000000",
                },
            )
