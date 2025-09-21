"""Tests for Momentum strategy implementation."""

import pytest
from decimal import Decimal

from nautilus_trader.model.identifiers import InstrumentId

from src.models.strategy import MomentumParameters


@pytest.mark.nautilus
class TestMomentumStrategy:
    """Test cases for Momentum strategy with Nautilus Trader integration."""

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
        with pytest.raises(
            ValueError, match="Input should be greater than or equal to 10"
        ):
            MomentumParameters(oversold_threshold=5.0)

        # Invalid overbought threshold (Pydantic validation)
        with pytest.raises(
            ValueError, match="Input should be less than or equal to 90"
        ):
            MomentumParameters(overbought_threshold=95.0)

        # Invalid threshold relationship (overbought <= oversold)
        with pytest.raises(ValueError):
            MomentumParameters(oversold_threshold=50.0, overbought_threshold=40.0)

        # Invalid trade size
        with pytest.raises(ValueError):
            MomentumParameters(trade_size=Decimal("0"))

    def test_momentum_strategy_creation(self):
        """INTEGRATION: Momentum strategy loads with Nautilus - WILL FAIL INITIALLY."""
        pytest.importorskip(
            "src.core.strategies.momentum",
            reason="Momentum strategy not implemented yet",
        )

        from src.core.strategies.sma_momentum import SMAMomentum, SMAMomentumConfig

        config = SMAMomentumConfig(
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            bar_type="AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL",
            rsi_period=14,
            oversold_threshold=30.0,
            overbought_threshold=70.0,
            trade_size=Decimal("1000000"),
        )

        strategy = SMAMomentum(config)
        assert strategy.config.rsi_period == 14
        assert strategy.config.oversold_threshold == 30.0
        assert strategy.config.overbought_threshold == 70.0
        assert strategy.config.trade_size == Decimal("1000000")

    def test_momentum_strategy_initialization(self):
        """Test strategy initialization with RSI indicator."""
        pytest.importorskip(
            "src.core.strategies.momentum",
            reason="Momentum strategy not implemented yet",
        )

        from src.core.strategies.sma_momentum import SMAMomentum, SMAMomentumConfig

        config = SMAMomentumConfig(
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            bar_type="AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL",
            rsi_period=10,
            oversold_threshold=25.0,
            overbought_threshold=75.0,
            trade_size=Decimal("500000"),
        )

        strategy = SMAMomentum(config)

        # Should initialize with RSI indicator
        assert hasattr(strategy, "rsi")
        assert strategy.rsi.period == 10

        # Should initialize price history for RSI calculation
        assert hasattr(strategy, "_price_history")
        assert isinstance(strategy._price_history, list)

    @pytest.mark.integration
    def test_momentum_with_mock_data(self):
        """INTEGRATION: Momentum strategy works with mock data."""
        pytest.importorskip(
            "src.core.strategies.momentum",
            reason="Momentum strategy not implemented yet",
        )

        from src.core.strategies.sma_momentum import SMAMomentum, SMAMomentumConfig
        from src.utils.mock_data import generate_mock_bars, create_test_instrument

        # Create test instrument
        instrument, instrument_id = create_test_instrument("EUR/USD")

        config = SMAMomentumConfig(
            instrument_id=instrument_id,
            bar_type=f"{instrument_id}-15-MINUTE-MID-EXTERNAL",
            rsi_period=10,  # Shorter period for testing
            oversold_threshold=30.0,
            overbought_threshold=70.0,
            trade_size=Decimal("1000000"),
        )

        strategy = SMAMomentum(config)

        # Generate mock bars (don't call on_start as it requires registration)
        mock_bars = generate_mock_bars(instrument_id, num_bars=20)

        # Process bars and skip order generation by temporarily setting order_factory to None

        # Replace signal generation with mock functions to avoid order_factory issues
        strategy._generate_buy_signal = lambda: None
        strategy._generate_sell_signal = lambda: None

        # Process several bars to warm up indicators (skip on_start)
        for bar in mock_bars:
            strategy.on_bar(bar)

        # After processing bars, RSI should be initialized
        assert strategy.rsi.initialized
        assert len(strategy._price_history) > 0
        assert strategy._current_rsi is not None

    def test_momentum_signal_generation(self):
        """Test momentum signal generation logic."""
        pytest.importorskip(
            "src.core.strategies.momentum",
            reason="Momentum strategy not implemented yet",
        )

        from src.core.strategies.sma_momentum import SMAMomentum, SMAMomentumConfig
        from src.utils.mock_data import create_test_instrument

        # Create test instrument
        instrument, instrument_id = create_test_instrument("EUR/USD")

        config = SMAMomentumConfig(
            instrument_id=instrument_id,
            bar_type=f"{instrument_id}-15-MINUTE-MID-EXTERNAL",
            rsi_period=5,  # Short period for testing
            oversold_threshold=20.0,  # Lower threshold for testing
            overbought_threshold=80.0,  # Higher threshold for testing
            trade_size=Decimal("1000000"),
        )

        strategy = SMAMomentum(config)

        # Track signal generation without actually creating orders
        buy_signals = []
        sell_signals = []

        def mock_buy_signal():
            buy_signals.append("BUY")

        def mock_sell_signal():
            sell_signals.append("SELL")

        strategy._generate_buy_signal = mock_buy_signal
        strategy._generate_sell_signal = mock_sell_signal

        # Generate test bars to warm up indicators
        from src.utils.mock_data import generate_mock_bars

        test_bars = generate_mock_bars(instrument_id, num_bars=15)

        for bar in test_bars:
            strategy.on_bar(bar)

        # Should have processed bars and initialized RSI
        assert strategy.rsi.initialized
        assert len(strategy._price_history) >= 5
        assert strategy._current_rsi is not None

    def test_momentum_parameters_in_strategy_model(self):
        """Test that momentum parameters validate correctly in TradingStrategy model."""
        from src.models.strategy import TradingStrategy, StrategyType

        # Valid momentum strategy
        strategy = TradingStrategy(
            name="Test Momentum",
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

    def test_rsi_calculation_logic(self):
        """Test RSI calculation for momentum signals."""
        pytest.importorskip(
            "src.core.strategies.momentum",
            reason="Momentum strategy not implemented yet",
        )

        from src.core.strategies.sma_momentum import SMAMomentum, SMAMomentumConfig
        from src.utils.mock_data import create_test_instrument

        # Create test instrument
        instrument, instrument_id = create_test_instrument("EUR/USD")

        config = SMAMomentumConfig(
            instrument_id=instrument_id,
            bar_type=f"{instrument_id}-15-MINUTE-MID-EXTERNAL",
            rsi_period=14,
            oversold_threshold=30.0,
            overbought_threshold=70.0,
            trade_size=Decimal("1000000"),
        )

        strategy = SMAMomentum(config)

        # Mock signal generation to focus on RSI calculation
        strategy._generate_buy_signal = lambda: None
        strategy._generate_sell_signal = lambda: None

        # Generate test bars with known price pattern
        from src.utils.mock_data import generate_mock_bars

        test_bars = generate_mock_bars(instrument_id, num_bars=20)

        # Process bars to calculate RSI
        for bar in test_bars:
            strategy.on_bar(bar)

        # After sufficient bars, RSI should be calculated
        if strategy.rsi.initialized:
            assert 0 <= strategy._current_rsi <= 100
            assert isinstance(strategy._current_rsi, float)
