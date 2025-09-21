"""
Tests for YAML configuration loader.

Following TDD approach - these tests define the expected behavior
for loading YAML strategy configurations.
"""

import pytest
import yaml
from decimal import Decimal

from src.utils.config_loader import ConfigLoader


@pytest.fixture
def sma_yaml_config():
    """Fixture providing valid SMA strategy YAML configuration."""
    return """
    strategy_path: "src.core.strategies.sma_crossover:SMACrossover"
    config_path: "src.core.strategies.sma_crossover:SMAConfig"
    config:
      instrument_id: "AAPL.NASDAQ"
      bar_type: "AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"
      fast_period: 10
      slow_period: 20
      trade_size: 1000000
    """


@pytest.fixture
def mean_reversion_yaml_config():
    """Fixture providing valid Mean Reversion strategy YAML configuration."""
    return """
    strategy_path: "src.core.strategies.rsi_mean_reversion:RSIMeanRev"
    config_path: "src.core.strategies.rsi_mean_reversion:RSIMeanRevConfig"
    config:
      instrument_id: "AAPL.NASDAQ"
      bar_type: "AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"
      trade_size: 1000000
      order_id_tag: "001"
      rsi_period: 2
      rsi_buy_threshold: 10.0
      exit_rsi: 50.0
      sma_trend_period: 200
      warmup_days: 400
      cooldown_bars: 0
    """


@pytest.fixture
def momentum_yaml_config():
    """Fixture providing valid Momentum strategy YAML configuration."""
    return """
    strategy_path: "src.core.strategies.sma_momentum:SMAMomentum"
    config_path: "src.core.strategies.sma_momentum:SMAMomentumConfig"
    config:
      instrument_id: "AAPL.NASDAQ"
      bar_type: "AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"
      trade_size: 1000000
      order_id_tag: "002"
      fast_period: 20
      slow_period: 50
      warmup_days: 1
      allow_short: false
    """


class TestConfigLoader:
    """Test YAML configuration loading functionality."""

    @pytest.mark.integration
    def test_load_from_yaml_string_sma_strategy(self, sma_yaml_config):
        """INTEGRATION: Load SMA strategy config from YAML string."""
        config_obj = ConfigLoader.load_from_yaml(sma_yaml_config)

        # Verify config structure
        assert (
            config_obj.strategy_path == "src.core.strategies.sma_crossover:SMACrossover"
        )
        assert config_obj.config_path == "src.core.strategies.sma_crossover:SMAConfig"
        assert config_obj.config.fast_period == 10
        assert config_obj.config.slow_period == 20
        assert config_obj.config.trade_size == Decimal("1000000")
        assert str(config_obj.config.instrument_id) == "AAPL.NASDAQ"

    @pytest.mark.integration
    def test_load_from_yaml_string_mean_reversion_strategy(
        self, mean_reversion_yaml_config
    ):
        """INTEGRATION: Load Mean Reversion strategy config from YAML string."""
        config_obj = ConfigLoader.load_from_yaml(mean_reversion_yaml_config)

        assert (
            config_obj.strategy_path
            == "src.core.strategies.rsi_mean_reversion:RSIMeanRev"
        )
        assert (
            config_obj.config_path
            == "src.core.strategies.rsi_mean_reversion:RSIMeanRevConfig"
        )
        assert config_obj.config.rsi_period == 2
        assert config_obj.config.rsi_buy_threshold == 10.0
        assert config_obj.config.trade_size == Decimal("1000000")

    @pytest.mark.integration
    def test_load_from_yaml_string_momentum_strategy(self, momentum_yaml_config):
        """INTEGRATION: Load Momentum strategy config from YAML string."""
        config_obj = ConfigLoader.load_from_yaml(momentum_yaml_config)

        assert (
            config_obj.strategy_path == "src.core.strategies.sma_momentum:SMAMomentum"
        )
        assert config_obj.config_path == "src.core.strategies.sma_momentum:SMAMomentumConfig"
        assert config_obj.config.fast_period == 20
        assert config_obj.config.slow_period == 50
        assert config_obj.config.allow_short == False

    @pytest.mark.integration
    @pytest.mark.parametrize(
        "yaml_config_fixture,expected_strategy_path",
        [
            ("sma_yaml_config", "src.core.strategies.sma_crossover:SMACrossover"),
            (
                "mean_reversion_yaml_config",
                "src.core.strategies.rsi_mean_reversion:RSIMeanRev",
            ),
            ("momentum_yaml_config", "src.core.strategies.sma_momentum:SMAMomentum"),
        ],
    )
    def test_load_all_strategy_types_from_yaml(
        self, yaml_config_fixture, expected_strategy_path, request
    ):
        """INTEGRATION: Test loading all strategy types using parametrization."""
        yaml_config = request.getfixturevalue(yaml_config_fixture)
        config_obj = ConfigLoader.load_from_yaml(yaml_config)

        assert config_obj.strategy_path == expected_strategy_path
        assert config_obj.config.trade_size == Decimal("1000000")
        assert str(config_obj.config.instrument_id) == "AAPL.NASDAQ"

    def test_load_from_file_valid_yaml(self, tmp_path, sma_yaml_config):
        """Test loading valid YAML config from file."""
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(sma_yaml_config)

        config_obj = ConfigLoader.load_from_file(str(config_file))

        assert config_obj.config.fast_period == 10
        assert config_obj.config.slow_period == 20
        assert (
            config_obj.strategy_path == "src.core.strategies.sma_crossover:SMACrossover"
        )

    def test_load_from_file_not_found(self):
        """Test error handling for non-existent file."""
        with pytest.raises(FileNotFoundError):
            ConfigLoader.load_from_file("nonexistent_file.yaml")

    @pytest.mark.parametrize(
        "invalid_yaml,expected_exception",
        [
            pytest.param(
                'strategy_path: "invalid\nconfig:\n  missing_quote: value',
                yaml.YAMLError,
                id="invalid_yaml_syntax",
            ),
            pytest.param(
                'config_path: "src.core.strategies.sma_crossover:SMAConfig"\nconfig:\n  fast_period: 10',
                ValueError,
                id="missing_strategy_path",
            ),
            pytest.param(
                'strategy_path: "src.core.strategies.sma_crossover:SMACrossover"\nconfig:\n  fast_period: 10',
                ValueError,
                id="missing_config_path",
            ),
            pytest.param(
                'strategy_path: "src.core.strategies.sma_crossover:SMACrossover"\nconfig_path: "src.core.strategies.sma_crossover:SMAConfig"',
                ValueError,
                id="missing_config_section",
            ),
        ],
    )
    def test_load_from_yaml_error_cases(self, invalid_yaml, expected_exception):
        """Test various error cases when loading YAML configurations."""
        with pytest.raises(expected_exception):
            ConfigLoader.load_from_yaml(invalid_yaml)

    def test_load_from_yaml_invalid_config_parameters(self):
        """Test error handling for invalid config parameters."""
        yaml_content = """
        strategy_path: "src.core.strategies.sma_crossover:SMACrossover"
        config_path: "src.core.strategies.sma_crossover:NonExistentConfig"
        config:
          instrument_id: "AAPL.NASDAQ"
          bar_type: "AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL"
          fast_period: 10
          slow_period: 20
          trade_size: 1000000
        """

        with pytest.raises((ValueError, ImportError)):
            ConfigLoader.load_from_yaml(yaml_content)

    @pytest.fixture
    def mean_reversion_yaml_data(self):
        """Fixture providing parsed YAML data for mean reversion strategy."""
        return {
            "strategy_path": "src.core.strategies.rsi_mean_reversion:RSIMeanRev",
            "config_path": "src.core.strategies.rsi_mean_reversion:RSIMeanRevConfig",
            "config": {
                "instrument_id": "AAPL.NASDAQ",
                "bar_type": "AAPL.NASDAQ-1-MINUTE-LAST-INTERNAL",
                "trade_size": 1000000,
                "order_id_tag": "001",
                "rsi_period": 2,
                "rsi_buy_threshold": 10.0,
                "exit_rsi": 50.0,
                "sma_trend_period": 200,
                "warmup_days": 400,
                "cooldown_bars": 0,
            },
        }

    def test_create_config_object_dynamic_loading(self, mean_reversion_yaml_data):
        """Test dynamic loading of config classes."""
        config_obj = ConfigLoader._create_config_object(mean_reversion_yaml_data)

        assert (
            config_obj.strategy_path
            == "src.core.strategies.rsi_mean_reversion:RSIMeanRev"
        )
        assert config_obj.config.rsi_period == 2

    @pytest.mark.parametrize(
        "yaml_data,should_pass",
        [
            pytest.param(
                {
                    "strategy_path": "src.core.strategies.sma_crossover:SMACrossover",
                    "config_path": "src.core.strategies.sma_crossover:SMAConfig",
                    "config": {"fast_period": 10},
                },
                True,
                id="valid_structure",
            ),
            pytest.param(
                {
                    "strategy_path": "src.core.strategies.sma_crossover:SMACrossover",
                    # Missing config_path and config
                },
                False,
                id="invalid_structure",
            ),
        ],
    )
    def test_validate_yaml_structure(self, yaml_data, should_pass):
        """Test YAML structure validation with valid and invalid data."""
        if should_pass:
            # Should not raise any exception
            ConfigLoader._validate_yaml_structure(yaml_data)
        else:
            with pytest.raises(ValueError):
                ConfigLoader._validate_yaml_structure(yaml_data)

    @pytest.mark.parametrize(
        "config_path,should_pass",
        [
            pytest.param(
                "src.core.strategies.sma_crossover:SMAConfig", True, id="valid_path"
            ),
            pytest.param(
                "nonexistent.module:NonexistentClass", False, id="invalid_path"
            ),
        ],
    )
    def test_load_config_class(self, config_path, should_pass):
        """Test loading config class from module path."""
        if should_pass:
            config_class = ConfigLoader._load_config_class(config_path)
            assert config_class is not None
        else:
            with pytest.raises(ImportError):
                ConfigLoader._load_config_class(config_path)

    def test_config_object_structure(self, sma_yaml_config):
        """Test that loaded config objects have expected structure."""
        config_obj = ConfigLoader.load_from_yaml(sma_yaml_config)

        # Verify the config object has all required attributes
        assert hasattr(config_obj, "strategy_path")
        assert hasattr(config_obj, "config_path")
        assert hasattr(config_obj, "config")

        # Verify config is properly typed and accessible
        assert isinstance(config_obj.config.fast_period, int)
        assert isinstance(config_obj.config.slow_period, int)
        assert isinstance(config_obj.config.trade_size, Decimal)
