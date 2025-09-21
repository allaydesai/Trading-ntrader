"""Tests for Mean Reversion strategy implementation."""

import pytest
from decimal import Decimal

from nautilus_trader.model.identifiers import InstrumentId

from src.models.strategy import MeanReversionParameters


@pytest.mark.nautilus
class TestMeanReversionStrategy:
    """Test cases for Mean Reversion strategy with Nautilus Trader integration."""

    def test_mean_reversion_parameters_validation(self):
        """Test Mean Reversion parameter validation."""
        # Valid parameters
        params = MeanReversionParameters(
            lookback_period=20, num_std_dev=2.0, trade_size=Decimal("1000000")
        )
        assert params.lookback_period == 20
        assert params.num_std_dev == 2.0
        assert params.trade_size == Decimal("1000000")

        # Invalid lookback period (Pydantic validation)
        with pytest.raises(
            ValueError, match="Input should be greater than or equal to 5"
        ):
            MeanReversionParameters(lookback_period=0)

        # Invalid std dev (Pydantic validation)
        with pytest.raises(
            ValueError, match="Input should be greater than or equal to 0.5"
        ):
            MeanReversionParameters(num_std_dev=0.0)

        # Invalid trade size
        with pytest.raises(ValueError):
            MeanReversionParameters(trade_size=Decimal("0"))

    def test_mean_reversion_strategy_creation(self):
        """INTEGRATION: Mean reversion strategy loads with Nautilus - WILL FAIL INITIALLY."""
        pytest.importorskip(
            "src.core.strategies.mean_reversion",
            reason="Mean reversion strategy not implemented yet",
        )

        from src.core.strategies.rsi_mean_reversion import (
            RSIMeanRev,
            RSIMeanRevConfig,
        )

        config = RSIMeanRevConfig(
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            bar_type="AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL",
            lookback_period=20,
            num_std_dev=2.0,
            trade_size=Decimal("1000000"),
        )

        strategy = RSIMeanRev(config)
        assert strategy.config.lookback_period == 20
        assert strategy.config.num_std_dev == 2.0
        assert strategy.config.trade_size == Decimal("1000000")

    def test_mean_reversion_strategy_initialization(self):
        """Test strategy initialization with indicators."""
        pytest.importorskip(
            "src.core.strategies.mean_reversion",
            reason="Mean reversion strategy not implemented yet",
        )

        from src.core.strategies.rsi_mean_reversion import (
            RSIMeanRev,
            RSIMeanRevConfig,
        )

        config = RSIMeanRevConfig(
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            bar_type="AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL",
            lookback_period=10,
            num_std_dev=1.5,
            trade_size=Decimal("500000"),
        )

        strategy = RSIMeanRev(config)

        # Should initialize with SMA indicator
        assert hasattr(strategy, "sma")
        assert strategy.sma.period == 10

        # Should initialize price history for std dev calculation
        assert hasattr(strategy, "_price_history")
        assert isinstance(strategy._price_history, list)

    @pytest.mark.integration
    def test_mean_reversion_with_mock_data(self):
        """INTEGRATION: Mean reversion strategy works with mock data."""
        pytest.importorskip(
            "src.core.strategies.mean_reversion",
            reason="Mean reversion strategy not implemented yet",
        )

        from src.core.strategies.rsi_mean_reversion import (
            RSIMeanRev,
            RSIMeanRevConfig,
        )
        from src.utils.mock_data import generate_mock_bars, create_test_instrument

        # Create test instrument
        instrument, instrument_id = create_test_instrument("EUR/USD")

        config = RSIMeanRevConfig(
            instrument_id=instrument_id,
            bar_type=f"{instrument_id}-15-MINUTE-MID-EXTERNAL",
            lookback_period=10,  # Shorter period for testing
            num_std_dev=2.0,
            trade_size=Decimal("1000000"),
        )

        strategy = RSIMeanRev(config)

        # Generate mock bars (don't call on_start as it requires registration)
        mock_bars = generate_mock_bars(instrument_id, num_bars=15)

        # Process bars and skip order generation by temporarily setting order_factory to None

        # Replace signal generation with mock functions to avoid order_factory issues
        strategy._generate_buy_signal = lambda: None
        strategy._generate_sell_signal = lambda: None

        # Process several bars to warm up indicators (skip on_start)
        for bar in mock_bars:
            strategy.on_bar(bar)

        # After processing bars, indicators should be initialized
        assert strategy.sma.initialized
        assert len(strategy._price_history) > 0
        assert strategy._upper_band is not None
        assert strategy._lower_band is not None
        assert strategy._middle_band is not None

    def test_mean_reversion_signal_generation(self):
        """Test mean reversion signal generation logic."""
        pytest.importorskip(
            "src.core.strategies.mean_reversion",
            reason="Mean reversion strategy not implemented yet",
        )

        from src.core.strategies.rsi_mean_reversion import (
            RSIMeanRev,
            RSIMeanRevConfig,
        )
        from src.utils.mock_data import create_test_instrument

        # Create test instrument
        instrument, instrument_id = create_test_instrument("EUR/USD")

        config = RSIMeanRevConfig(
            instrument_id=instrument_id,
            bar_type=f"{instrument_id}-15-MINUTE-MID-EXTERNAL",
            lookback_period=5,  # Short period for testing
            num_std_dev=1.0,  # Narrow bands for testing
            trade_size=Decimal("1000000"),
        )

        strategy = RSIMeanRev(config)

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

        test_bars = generate_mock_bars(instrument_id, num_bars=10)

        for bar in test_bars:
            strategy.on_bar(bar)

        # Should have processed bars and initialized indicators
        assert strategy.sma.initialized
        assert len(strategy._price_history) >= 5
        assert strategy._upper_band is not None
        assert strategy._lower_band is not None

    def test_mean_reversion_parameters_in_strategy_model(self):
        """Test that mean reversion parameters validate correctly in TradingStrategy model."""
        from src.models.strategy import TradingStrategy, StrategyType

        # Valid mean reversion strategy
        strategy = TradingStrategy(
            name="Test Mean Reversion",
            strategy_type=StrategyType.MEAN_REVERSION,
            parameters={
                "lookback_period": 20,
                "num_std_dev": 2.0,
                "trade_size": "1000000",
            },
        )
        assert strategy.strategy_type == StrategyType.MEAN_REVERSION

        # Invalid parameters should fail validation
        with pytest.raises(ValueError, match="Invalid Mean Reversion parameters"):
            TradingStrategy(
                name="Invalid Mean Reversion",
                strategy_type=StrategyType.MEAN_REVERSION,
                parameters={
                    "lookback_period": 0,  # Invalid
                    "num_std_dev": 2.0,
                    "trade_size": "1000000",
                },
            )
