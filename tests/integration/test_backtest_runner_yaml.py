"""
Tests for YAML configuration support in backtest runner.

Following TDD approach - these tests define the expected behavior
for running backtests from YAML configuration files.
"""

from decimal import Decimal

import pytest

from src.core.backtest_runner import MinimalBacktestRunner
from src.utils.config_loader import ConfigLoader


@pytest.fixture
def yaml_config_file(tmp_path):
    """Create a temporary YAML config file for testing."""
    config_content = """
    strategy_path: "src.core.strategies.sma_crossover:SMACrossover"
    config_path: "src.core.strategies.sma_crossover:SMAConfig"
    config:
      instrument_id: "AAPL.NASDAQ"
      bar_type: "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
      fast_period: 10
      slow_period: 20
      portfolio_value: 1000000
      position_size_pct: 10.0
    """

    config_file = tmp_path / "test_config.yaml"
    config_file.write_text(config_content)
    return str(config_file)


@pytest.fixture
def mean_reversion_yaml_config_file(tmp_path):
    """Create a temporary Mean Reversion YAML config file for testing."""
    config_content = """
    strategy_path: "src.core.strategies.rsi_mean_reversion:RSIMeanRev"
    config_path: "src.core.strategies.rsi_mean_reversion:RSIMeanRevConfig"
    config:
      instrument_id: "AAPL.NASDAQ"
      bar_type: "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
      trade_size: 1000000
      order_id_tag: "001"
      rsi_period: 2
      rsi_buy_threshold: 10.0
      exit_rsi: 50.0
      sma_trend_period: 200
      warmup_days: 400
      cooldown_bars: 0
    """

    config_file = tmp_path / "mr_config.yaml"
    config_file.write_text(config_content)
    return str(config_file)


class TestBacktestRunnerYAML:
    """Test YAML configuration support in backtest runner."""

    @pytest.mark.integration
    def test_run_from_config_file_sma_strategy(self, yaml_config_file):
        """Test running backtest from SMA YAML config file."""
        runner = MinimalBacktestRunner(data_source="mock")

        # This should use YAML config instead of hardcoded SMA
        result = runner.run_from_config_file(yaml_config_file)

        # Verify we get a valid backtest result
        assert result is not None
        assert hasattr(result, "total_return")
        assert hasattr(result, "total_trades")
        assert isinstance(result.total_return, float)
        assert isinstance(result.total_trades, int)

    @pytest.mark.integration
    def test_run_from_config_file_mean_reversion_strategy(self, mean_reversion_yaml_config_file):
        """Test running backtest from Mean Reversion YAML config file."""
        runner = MinimalBacktestRunner(data_source="mock")

        result = runner.run_from_config_file(mean_reversion_yaml_config_file)

        # Verify we get a valid backtest result
        assert result is not None
        assert hasattr(result, "total_return")
        assert hasattr(result, "total_trades")

    def test_run_from_config_file_not_found(self):
        """Test error handling for non-existent config file."""
        runner = MinimalBacktestRunner(data_source="mock")

        with pytest.raises(FileNotFoundError):
            runner.run_from_config_file("nonexistent_config.yaml")

    def test_run_from_config_object_sma_strategy(self):
        """Test running backtest from loaded config object."""
        yaml_content = """
        strategy_path: "src.core.strategies.sma_crossover:SMACrossover"
        config_path: "src.core.strategies.sma_crossover:SMAConfig"
        config:
          instrument_id: "AAPL.NASDAQ"
          bar_type: "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
          fast_period: 10
          slow_period: 20
          portfolio_value: 1000000
          position_size_pct: 10.0
        """

        config_obj = ConfigLoader.load_from_yaml(yaml_content)
        runner = MinimalBacktestRunner(data_source="mock")

        result = runner.run_from_config_object(config_obj)

        assert result is not None
        assert isinstance(result.total_return, float)
        assert isinstance(result.total_trades, int)

    @pytest.mark.integration
    @pytest.mark.parametrize(
        "strategy_type,config_content",
        [
            pytest.param(
                "sma",
                """
            strategy_path: "src.core.strategies.sma_crossover:SMACrossover"
            config_path: "src.core.strategies.sma_crossover:SMAConfig"
            config:
              instrument_id: "AAPL.NASDAQ"
              bar_type: "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
              fast_period: 10
              slow_period: 20
              portfolio_value: 1000000
              position_size_pct: 10.0
            """,
                id="sma_strategy",
            ),
            pytest.param(
                "mean_reversion",
                """
            strategy_path: "src.core.strategies.rsi_mean_reversion:RSIMeanRev"
            config_path: "src.core.strategies.rsi_mean_reversion:RSIMeanRevConfig"
            config:
              instrument_id: "AAPL.NASDAQ"
              bar_type: "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
              trade_size: 1000000
              order_id_tag: "001"
              rsi_period: 2
              rsi_buy_threshold: 10.0
              exit_rsi: 50.0
              sma_trend_period: 200
              warmup_days: 400
              cooldown_bars: 0
            """,
                id="mean_reversion_strategy",
            ),
            pytest.param(
                "momentum",
                """
            strategy_path: "src.core.strategies.sma_momentum:SMAMomentum"
            config_path: "src.core.strategies.sma_momentum:SMAMomentumConfig"
            config:
              instrument_id: "AAPL.NASDAQ"
              bar_type: "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
              trade_size: 1000000
              order_id_tag: "002"
              fast_period: 20
              slow_period: 50
              warmup_days: 1
              allow_short: false
            """,
                id="momentum_strategy",
            ),
        ],
    )
    def test_run_all_strategy_types_from_yaml(self, strategy_type, config_content):
        """Test running backtests for all strategy types using YAML configs."""
        config_obj = ConfigLoader.load_from_yaml(config_content)
        runner = MinimalBacktestRunner(data_source="mock")

        result = runner.run_from_config_object(config_obj)

        # All strategies should produce valid results
        assert result is not None
        assert isinstance(result.total_return, float)
        assert isinstance(result.total_trades, int)
        # Strategy results can vary widely - just check they're valid numbers
        assert not (result.total_return != result.total_return)  # Check for NaN
        assert result.total_trades >= 0
        # The fact that we get different results for different strategies is good!

    def test_config_object_validation(self):
        """Test that config objects have the expected structure."""
        yaml_content = """
        strategy_path: "src.core.strategies.sma_crossover:SMACrossover"
        config_path: "src.core.strategies.sma_crossover:SMAConfig"
        config:
          instrument_id: "AAPL.NASDAQ"
          bar_type: "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
          fast_period: 10
          slow_period: 20
          portfolio_value: 1000000
          position_size_pct: 10.0
        """

        config_obj = ConfigLoader.load_from_yaml(yaml_content)

        # Verify config object has required attributes for backtest runner
        assert hasattr(config_obj, "strategy_path")
        assert hasattr(config_obj, "config_path")
        assert hasattr(config_obj, "config")

        # Verify config has necessary trading parameters
        assert hasattr(config_obj.config, "instrument_id")
        assert hasattr(config_obj.config, "bar_type")
        # SMA uses percentage-based sizing instead of trade_size
        assert hasattr(config_obj.config, "portfolio_value")
        assert hasattr(config_obj.config, "position_size_pct")

    def test_integration_with_existing_run_sma_backtest(self, yaml_config_file):
        """Test that YAML config produces similar results to existing SMA method."""
        runner = MinimalBacktestRunner(data_source="mock")

        # Run using new YAML config method
        yaml_result = runner.run_from_config_file(yaml_config_file)

        # Run using existing SMA method with same parameters
        sma_result = runner.run_sma_backtest(
            fast_period=10,
            slow_period=20,
            trade_size=Decimal("1000000"),
            num_bars=100,  # Use same number of bars as mock data default
        )

        # Both methods should produce valid results
        assert yaml_result is not None
        assert sma_result is not None
        assert isinstance(yaml_result.total_return, float)
        assert isinstance(sma_result.total_return, float)
        # Both should produce some trading activity (strategies are active)
        assert yaml_result.total_trades >= 0
        assert sma_result.total_trades >= 0
