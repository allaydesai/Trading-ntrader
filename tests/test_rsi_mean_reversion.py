"""Tests for RSI Mean Reversion strategy implementation."""

import pytest
from decimal import Decimal

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.data import BarType

from src.models.strategy import MeanReversionParameters


@pytest.mark.nautilus
class TestRSIMeanReversionStrategy:
    """Test cases for RSI Mean Reversion strategy with Nautilus Trader integration."""

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

    def test_rsi_mean_reversion_strategy_creation(self):
        """INTEGRATION: RSI mean reversion strategy loads with Nautilus."""
        from src.core.strategies.rsi_mean_reversion import (
            RSIMeanRev,
            RSIMeanRevConfig,
        )

        config = RSIMeanRevConfig(
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            bar_type=BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"),
            trade_size=Decimal("1000000"),
            order_id_tag="001",
            rsi_period=2,
            rsi_buy_threshold=10.0,
            exit_rsi=50.0,
            sma_trend_period=200,
            warmup_days=400,
            cooldown_bars=0,
        )

        strategy = RSIMeanRev(config)
        assert strategy.config.rsi_period == 2
        assert strategy.config.rsi_buy_threshold == 10.0
        assert strategy.config.exit_rsi == 50.0
        assert strategy.config.sma_trend_period == 200

    def test_rsi_mean_reversion_strategy_initialization(self):
        """Test strategy initialization with internal state."""
        from src.core.strategies.rsi_mean_reversion import (
            RSIMeanRev,
            RSIMeanRevConfig,
        )

        config = RSIMeanRevConfig(
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            bar_type=BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"),
            trade_size=Decimal("500000"),
            order_id_tag="002",
            rsi_period=14,
            rsi_buy_threshold=30.0,
            exit_rsi=60.0,
            sma_trend_period=100,
        )

        strategy = RSIMeanRev(config)

        # Should initialize with internal state
        assert hasattr(strategy, "_closes")
        assert hasattr(strategy, "_rsi_ready")
        assert hasattr(strategy, "_avg_gain")
        assert hasattr(strategy, "_avg_loss")
        assert hasattr(strategy, "_sma_sum")
        assert strategy._rsi_ready is False
        assert strategy._prev_close is None

    @pytest.mark.integration
    def test_rsi_mean_reversion_with_mock_data(self):
        """INTEGRATION: RSI mean reversion strategy works with mock data."""
        from src.core.strategies.rsi_mean_reversion import (
            RSIMeanRev,
            RSIMeanRevConfig,
        )
        from src.utils.mock_data import generate_mock_bars, create_test_instrument

        # Create test instrument
        instrument, instrument_id = create_test_instrument("EUR/USD")

        config = RSIMeanRevConfig(
            instrument_id=instrument_id,
            bar_type=BarType.from_str(f"{instrument_id}-15-MINUTE-MID-EXTERNAL"),
            trade_size=Decimal("1000000"),
            order_id_tag="003",
            rsi_period=2,
            rsi_buy_threshold=10.0,
            exit_rsi=50.0,
            sma_trend_period=10,  # Shorter for testing
            warmup_days=20,
        )

        strategy = RSIMeanRev(config)

        # Generate mock bars
        mock_bars = generate_mock_bars(instrument_id, num_bars=15)

        # Process bars to warm up indicators
        for bar in mock_bars:
            # Update RSI calculation
            close = float(bar.close)
            _ = strategy._update_rsi(close)
            _ = strategy._update_sma(close)

            # After enough bars, RSI should be calculated
            if len(mock_bars) > config.rsi_period + 1:
                # Eventually RSI should be ready
                pass

        # After processing bars, internal state should be updated
        assert len(strategy._closes) > 0
        assert strategy._sma_sum > 0

    def test_rsi_mean_reversion_rsi_calculation(self):
        """Test RSI calculation logic."""
        from src.core.strategies.rsi_mean_reversion import (
            RSIMeanRev,
            RSIMeanRevConfig,
        )

        config = RSIMeanRevConfig(
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            bar_type=BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"),
            trade_size=Decimal("1000000"),
            order_id_tag="004",
            rsi_period=2,
        )

        strategy = RSIMeanRev(config)

        # Test RSI calculation with sample prices
        test_prices = [100.0, 102.0, 101.0, 103.0, 102.5, 101.5, 104.0]

        for price in test_prices:
            _ = strategy._update_rsi(price)

        # After processing enough prices, RSI should be calculated
        assert strategy._rsi_ready is True
        assert strategy._avg_gain >= 0
        assert strategy._avg_loss >= 0

    def test_rsi_mean_reversion_sma_calculation(self):
        """Test SMA calculation logic."""
        from src.core.strategies.rsi_mean_reversion import (
            RSIMeanRev,
            RSIMeanRevConfig,
        )

        config = RSIMeanRevConfig(
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            bar_type=BarType.from_str("AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"),
            trade_size=Decimal("1000000"),
            order_id_tag="005",
            sma_trend_period=5,  # Short period for testing
        )

        strategy = RSIMeanRev(config)

        # Test SMA calculation with sample prices
        test_prices = [100.0, 102.0, 101.0, 103.0, 102.0]
        expected_sma = sum(test_prices) / len(test_prices)

        sma_result = None
        for price in test_prices:
            sma_result = strategy._update_sma(price)

        # After processing enough prices, SMA should be calculated
        assert sma_result is not None
        assert abs(sma_result - expected_sma) < 0.01

    def test_rsi_mean_reversion_parameters_in_strategy_model(self):
        """Test that RSI mean reversion parameters validate correctly in TradingStrategy model."""
        from src.models.strategy import TradingStrategy, StrategyType

        # Valid mean reversion strategy
        strategy = TradingStrategy(
            name="Test RSI Mean Reversion",
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
