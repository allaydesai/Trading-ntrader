"""Tests for Apolo RSI Mean Reversion strategy implementation."""

from decimal import Decimal

import pytest
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId


@pytest.mark.component
class TestApoloRSIStrategy:
    """Test cases for Apolo RSI Mean Reversion strategy.

    Strategy Rules:
    - Buy: RSI(2) < 10 (oversold after sharp decline)
    - Sell: RSI(2) > 50 (mean reversion back up)
    - Long only
    """

    def test_apolo_rsi_parameters_validation(self):
        """Test Apolo RSI parameter validation.

        Note: Nautilus Trader's RSI indicator returns values in 0-1 range (not 0-100).
        Thresholds should be specified as decimals (e.g., 0.10 for RSI < 10).
        """
        from src.models.strategy import ApoloRSIParameters

        # Valid default parameters (0-1 range for Nautilus RSI)
        params = ApoloRSIParameters()
        assert params.rsi_period == 2
        assert params.buy_threshold == 0.10  # RSI < 10 in traditional terms
        assert params.sell_threshold == 0.50  # RSI > 50 in traditional terms
        assert params.trade_size == Decimal("100.0")
        assert params.order_id_tag == "APOLO"

        # Valid custom parameters (0-1 range)
        params = ApoloRSIParameters(
            rsi_period=2,
            buy_threshold=0.05,  # RSI < 5
            sell_threshold=0.60,  # RSI > 60
            trade_size=Decimal("500.0"),
        )
        assert params.buy_threshold == 0.05
        assert params.sell_threshold == 0.60

        # Invalid: RSI period must be >= 2
        with pytest.raises(ValueError):
            ApoloRSIParameters(rsi_period=1)

        # Invalid: buy threshold must be between 0-1 (Nautilus RSI range)
        with pytest.raises(ValueError):
            ApoloRSIParameters(buy_threshold=-0.05)

        # Invalid: sell threshold must be between 0-1 (Nautilus RSI range)
        with pytest.raises(ValueError):
            ApoloRSIParameters(sell_threshold=1.5)

        # Invalid: trade size must be > 0
        with pytest.raises(ValueError):
            ApoloRSIParameters(trade_size=Decimal("0"))

    def test_apolo_rsi_buy_threshold_less_than_sell(self):
        """Test that buy threshold must be less than sell threshold.

        Note: Thresholds use 0-1 range (Nautilus RSI format).
        """
        from src.models.strategy import ApoloRSIParameters

        # Valid: buy < sell (0-1 range)
        params = ApoloRSIParameters(buy_threshold=0.10, sell_threshold=0.50)
        assert params.buy_threshold < params.sell_threshold

        # Invalid: buy >= sell should raise (0-1 range)
        with pytest.raises(ValueError, match="Buy threshold must be less than sell threshold"):
            ApoloRSIParameters(buy_threshold=0.60, sell_threshold=0.50)

    def test_apolo_rsi_strategy_creation(self):
        """Test Apolo RSI strategy instantiation."""
        from src.core.strategies.apolo_rsi import ApoloRSI, ApoloRSIConfig

        config = ApoloRSIConfig(
            instrument_id=InstrumentId.from_str("AAPL.NASDAQ"),
            bar_type=BarType.from_str("AAPL.NASDAQ-1-DAY-LAST-INTERNAL"),
            trade_size=Decimal("100.0"),
            order_id_tag="TEST",
            rsi_period=2,
            buy_threshold=10.0,
            sell_threshold=50.0,
        )

        strategy = ApoloRSI(config)
        assert strategy.config.rsi_period == 2
        assert strategy.config.buy_threshold == 10.0
        assert strategy.config.sell_threshold == 50.0

    def test_apolo_rsi_strategy_has_rsi_indicator(self):
        """Test that strategy initializes RSI indicator correctly."""
        from src.core.strategies.apolo_rsi import ApoloRSI, ApoloRSIConfig

        config = ApoloRSIConfig(
            instrument_id=InstrumentId.from_str("SPY.NYSE"),
            bar_type=BarType.from_str("SPY.NYSE-1-DAY-LAST-INTERNAL"),
            trade_size=Decimal("100.0"),
            order_id_tag="TEST",
            rsi_period=2,
        )

        strategy = ApoloRSI(config)

        # Should have RSI indicator initialized
        assert hasattr(strategy, "rsi")
        assert strategy.rsi.period == 2

    def test_apolo_rsi_strategy_registration(self):
        """Test that strategy is registered in the registry."""
        # Import to trigger registration
        import src.core.strategies.apolo_rsi  # noqa: F401
        from src.core.strategy_registry import StrategyRegistry

        # Should be registered with name and aliases
        definition = StrategyRegistry.get("apolo_rsi")
        assert definition is not None

        # Should have config and param model registered
        assert definition.config_class is not None
        assert definition.param_model is not None

    def test_apolo_rsi_in_strategy_type_enum(self):
        """Test that APOLO_RSI is in StrategyType enum."""
        from src.models.strategy import StrategyType

        assert hasattr(StrategyType, "APOLO_RSI")
        assert StrategyType.APOLO_RSI.value == "apolo_rsi"

    def test_apolo_rsi_trading_strategy_validation(self):
        """Test Apolo RSI parameters validate in TradingStrategy model.

        Note: Thresholds use 0-1 range (Nautilus RSI format).
        """
        from src.models.strategy import StrategyType, TradingStrategy

        # Valid strategy (0-1 range for thresholds)
        strategy = TradingStrategy(
            name="Test Apolo RSI",
            strategy_type=StrategyType.APOLO_RSI,
            parameters={
                "rsi_period": 2,
                "buy_threshold": 0.10,  # RSI < 10 in traditional terms
                "sell_threshold": 0.50,  # RSI > 50 in traditional terms
                "trade_size": "100.0",
            },
        )
        assert strategy.strategy_type == StrategyType.APOLO_RSI

        # Invalid parameters should fail
        with pytest.raises(ValueError, match="Invalid Apolo RSI parameters"):
            TradingStrategy(
                name="Invalid Apolo RSI",
                strategy_type=StrategyType.APOLO_RSI,
                parameters={
                    "rsi_period": 0,  # Invalid
                    "buy_threshold": 0.10,
                    "sell_threshold": 0.50,
                },
            )
