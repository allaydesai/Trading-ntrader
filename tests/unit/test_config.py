"""Tests for configuration module."""

from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from src.config import Settings, get_settings


@pytest.mark.unit
def test_settings_default_values():
    """Test Settings model with default values (bypassing environment)."""
    # Create settings without environment file loading
    settings = Settings(_env_file=None)

    assert settings.app_name == "NTrader"
    assert settings.app_version == "0.1.0"
    assert settings.debug is False
    assert settings.default_currency == "USD"
    assert settings.default_balance == Decimal("1000000")
    assert settings.fast_ema_period == 10
    assert settings.slow_ema_period == 20
    assert settings.trade_size == Decimal("1000000")
    assert settings.data_directory == Path("data")
    assert settings.mock_data_bars == 1000
    assert settings.log_level == "INFO"


@pytest.mark.unit
def test_settings_custom_values():
    """Test Settings model with custom values."""
    custom_values = {
        "app_name": "CustomTrader",
        "app_version": "1.0.0",
        "debug": True,
        "default_currency": "EUR",
        "default_balance": "500000",
        "fast_ema_period": 5,
        "slow_ema_period": 15,
        "trade_size": "250000",
        "data_directory": "custom_data",
        "mock_data_bars": 2000,
        "log_level": "DEBUG",
    }

    settings = Settings(**custom_values)

    assert settings.app_name == "CustomTrader"
    assert settings.app_version == "1.0.0"
    assert settings.debug is True
    assert settings.default_currency == "EUR"
    assert settings.default_balance == Decimal("500000")
    assert settings.fast_ema_period == 5
    assert settings.slow_ema_period == 15
    assert settings.trade_size == Decimal("250000")
    assert settings.data_directory == Path("custom_data")
    assert settings.mock_data_bars == 2000
    assert settings.log_level == "DEBUG"


@pytest.mark.unit
def test_settings_decimal_validation():
    """Test Settings decimal field validation."""
    # Valid decimal values
    settings = Settings(default_balance="1000.50", trade_size="500000.25")
    assert settings.default_balance == Decimal("1000.50")
    assert settings.trade_size == Decimal("500000.25")

    # Test with Decimal objects
    settings = Settings(default_balance=Decimal("2000.75"), trade_size=Decimal("750000.10"))
    assert settings.default_balance == Decimal("2000.75")
    assert settings.trade_size == Decimal("750000.10")


@pytest.mark.unit
def test_settings_path_validation():
    """Test Settings Path field validation."""
    # String path
    settings = Settings(data_directory="test/path")
    assert settings.data_directory == Path("test/path")

    # Path object
    test_path = Path("another/path")
    settings = Settings(data_directory=test_path)
    assert settings.data_directory == test_path


@pytest.mark.unit
def test_settings_integer_validation():
    """Test Settings integer field validation."""
    settings = Settings(fast_ema_period=25, slow_ema_period=50, mock_data_bars=5000)
    assert settings.fast_ema_period == 25
    assert settings.slow_ema_period == 50
    assert settings.mock_data_bars == 5000

    # Test with string integers
    settings = Settings(fast_ema_period="30", slow_ema_period="60", mock_data_bars="3000")
    assert settings.fast_ema_period == 30
    assert settings.slow_ema_period == 60
    assert settings.mock_data_bars == 3000


@pytest.mark.unit
def test_settings_boolean_validation():
    """Test Settings boolean field validation."""
    # Boolean values
    settings = Settings(debug=True)
    assert settings.debug is True

    settings = Settings(debug=False)
    assert settings.debug is False

    # String boolean values
    settings = Settings(debug="true")
    assert settings.debug is True

    settings = Settings(debug="false")
    assert settings.debug is False

    settings = Settings(debug="1")
    assert settings.debug is True

    settings = Settings(debug="0")
    assert settings.debug is False


@pytest.mark.unit
def test_settings_validation_errors():
    """Test Settings validation errors."""
    # Invalid decimal
    with pytest.raises(ValueError):
        Settings(default_balance="not-a-number")

    # Invalid integer
    with pytest.raises(ValueError):
        Settings(fast_ema_period="not-a-number")


@pytest.mark.unit
def test_settings_model_config():
    """Test Settings model configuration."""
    settings = Settings()

    # Check model configuration
    config = settings.model_config
    assert config["env_file"] == ".env"
    assert config["env_file_encoding"] == "utf-8"
    assert config["case_sensitive"] is False


@pytest.mark.unit
def test_settings_environment_variables():
    """Test Settings loading from environment variables."""
    env_vars = {
        "APP_NAME": "EnvTrader",
        "DEBUG": "true",
        "DEFAULT_BALANCE": "750000",
        "FAST_EMA_PERIOD": "8",
        "LOG_LEVEL": "WARNING",
    }

    with patch.dict("os.environ", env_vars, clear=False):
        settings = Settings()

        assert settings.app_name == "EnvTrader"
        assert settings.debug is True
        assert settings.default_balance == Decimal("750000")
        assert settings.fast_ema_period == 8
        assert settings.log_level == "WARNING"


@pytest.mark.unit
def test_settings_case_insensitive_env():
    """Test Settings case insensitive environment variables."""
    env_vars = {
        "app_name": "LowerCaseTrader",  # lowercase
        "APP_VERSION": "2.0.0",  # uppercase
        "Default_Currency": "GBP",  # mixed case
    }

    with patch.dict("os.environ", env_vars, clear=False):
        settings = Settings()

        assert settings.app_name == "LowerCaseTrader"
        assert settings.app_version == "2.0.0"
        assert settings.default_currency == "GBP"


@pytest.mark.unit
def test_get_settings_function():
    """Test get_settings function returns Settings instance."""
    settings = get_settings()

    assert isinstance(settings, Settings)
    assert settings.app_name == "NTrader"
    assert settings.app_version == "0.1.0"


@pytest.mark.unit
def test_get_settings_singleton_behavior():
    """Test that get_settings behaves like a singleton."""
    # Note: The current implementation doesn't actually cache,
    # but this test ensures the function works consistently
    settings1 = get_settings()
    settings2 = get_settings()

    # Should have same default values
    assert settings1.app_name == settings2.app_name
    assert settings1.app_version == settings2.app_version
    assert settings1.default_balance == settings2.default_balance


@pytest.mark.unit
def test_settings_field_descriptions():
    """Test that Settings fields have proper descriptions."""
    # Check that fields have descriptions (via Field)
    fields = Settings.model_fields

    assert fields["debug"].description == "Enable debug mode"
    assert fields["default_currency"].description == "Default currency for accounts"
    assert fields["default_balance"].description == "Default starting balance"
    assert fields["fast_ema_period"].description == "Fast EMA period for strategies"
    assert fields["slow_ema_period"].description == "Slow EMA period for strategies"
    assert fields["trade_size"].description == "Default trade size in SHARES (not USD notional)"
    assert fields["data_directory"].description == "Directory for data files"
    assert fields["mock_data_bars"].description == "Number of mock data bars to generate"
    assert fields["log_level"].description == "Logging level"


@pytest.mark.unit
def test_settings_serialization():
    """Test Settings serialization and deserialization."""
    original = Settings(
        app_name="TestTrader",
        debug=True,
        default_balance=Decimal("123456.78"),
        data_directory=Path("test/data"),
    )

    # Serialize to dict
    data = original.model_dump()

    # Deserialize back
    restored = Settings.model_validate(data)

    assert restored.app_name == original.app_name
    assert restored.debug == original.debug
    assert restored.default_balance == original.default_balance
    assert restored.data_directory == original.data_directory
