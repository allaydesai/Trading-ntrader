from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.core.strategy_factory import StrategyLoader
from src.models.strategy import (
    StrategyType,
)


class TestStrategyFactory:
    def test_build_strategy_params_sma_defaults(self):
        """Test building SMA params with defaults."""
        settings = MagicMock()
        settings.fast_ema_period = 15
        settings.slow_ema_period = 30
        settings.portfolio_value = Decimal("2000000")
        settings.position_size_pct = Decimal("5.0")

        params = StrategyLoader.build_strategy_params(
            StrategyType.SMA_CROSSOVER, overrides={}, settings=settings
        )

        assert params["fast_period"] == 15
        assert params["slow_period"] == 30
        assert params["portfolio_value"] == Decimal("2000000")
        assert params["position_size_pct"] == Decimal("5.0")

    def test_build_strategy_params_sma_overrides(self):
        """Test building SMA params with overrides."""
        settings = MagicMock()
        # These shouldn't be used because overrides are present
        settings.fast_ema_period = 15
        settings.slow_ema_period = 30
        settings.portfolio_value = Decimal("1000000")
        settings.position_size_pct = Decimal("10.0")

        params = StrategyLoader.build_strategy_params(
            StrategyType.SMA_CROSSOVER,
            overrides={"fast_period": 5, "slow_period": 10},
            settings=settings,
        )

        assert params["fast_period"] == 5
        assert params["slow_period"] == 10

    def test_build_strategy_params_momentum(self):
        """Test building Momentum params."""
        settings = MagicMock()
        settings.trade_size = Decimal("500")
        settings.fast_ema_period = 20  # Default
        settings.slow_ema_period = 50  # Default

        params = StrategyLoader.build_strategy_params(
            StrategyType.MOMENTUM, overrides={"allow_short": True}, settings=settings
        )

        assert params["trade_size"] == Decimal("500")
        assert params["allow_short"] is True
        # Defaults
        assert params["fast_period"] == 20

    def test_build_strategy_params_validation_error(self):
        """Test validation error on invalid params."""
        settings = MagicMock()
        settings.fast_ema_period = 15
        settings.slow_ema_period = 30
        settings.portfolio_value = Decimal("1000000")
        settings.position_size_pct = Decimal("10.0")

        with pytest.raises(ValueError, match="Configuration validation failed"):
            StrategyLoader.build_strategy_params(
                StrategyType.SMA_CROSSOVER,
                overrides={"fast_period": 50, "slow_period": 10},  # Invalid: fast > slow
                settings=settings,
            )

    def test_unknown_strategy_type(self):
        """Test error for unknown strategy type."""
        with pytest.raises(ValueError, match="Unsupported strategy type"):
            StrategyLoader.build_strategy_params("invalid_type", overrides={}, settings=None)
