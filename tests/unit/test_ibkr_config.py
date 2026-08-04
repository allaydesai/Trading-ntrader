"""Tests for IBKR configuration settings."""

import pytest
from ibapi.common import MarketDataTypeEnum  # type: ignore[import-untyped]

from src.config import get_settings


class TestIBKRConfiguration:
    """Test suite for IBKR settings integration."""

    @pytest.mark.integration
    @pytest.mark.unit
    def test_ibkr_settings_load_from_env(self, monkeypatch):
        """INTEGRATION: IBKR settings load from environment variables."""
        monkeypatch.setenv("TWS_USERNAME", "test_user")
        monkeypatch.setenv("TWS_PASSWORD", "test_pass")
        monkeypatch.setenv("IBKR_HOST", "127.0.0.1")
        monkeypatch.setenv("IBKR_PORT", "7497")

        # Need to clear cached settings first
        from src.config import Settings

        settings = Settings()

        assert settings.ibkr.tws_username == "test_user"
        assert settings.ibkr.tws_password == "test_pass"
        assert settings.ibkr.ibkr_host == "127.0.0.1"
        assert settings.ibkr.ibkr_port == 7497

    @pytest.mark.integration
    @pytest.mark.unit
    def test_ibkr_settings_have_safe_defaults(self):
        """INTEGRATION: IBKR settings have production-safe defaults."""
        settings = get_settings()

        assert settings.ibkr.ibkr_trading_mode == "paper"
        assert settings.ibkr.ibkr_read_only is True
        assert settings.ibkr.ibkr_rate_limit == 45  # 90% of 50 limit

    @pytest.mark.unit
    def test_ibkr_connection_settings_defaults(self):
        """Test IBKR connection settings are loaded correctly."""
        settings = get_settings()

        assert settings.ibkr.ibkr_host == "127.0.0.1"
        # Port should be from env variable (4002 for Gateway, 7497 for TWS)
        assert settings.ibkr.ibkr_port in [4002, 7497]
        assert settings.ibkr.ibkr_client_id >= 1  # Can be customized via .env

    @pytest.mark.unit
    def test_ibkr_timeout_settings(self):
        """Test IBKR timeout settings are properly configured."""
        settings = get_settings()

        assert settings.ibkr.ibkr_connection_timeout == 300  # 5 minutes
        assert settings.ibkr.ibkr_request_timeout == 60  # 1 minute

    @pytest.mark.unit
    def test_ibkr_credentials_optional(self):
        """Test IBKR credentials can be empty (optional)."""
        settings = get_settings()

        # Credentials should be empty strings if not provided
        assert isinstance(settings.ibkr.tws_username, str)
        assert isinstance(settings.ibkr.tws_password, str)

    @pytest.mark.unit
    def test_ibkr_data_settings(self):
        """Test IBKR data-specific settings."""
        settings = get_settings()

        assert settings.ibkr.ibkr_use_rth is True  # Regular Trading Hours
        assert settings.ibkr.ibkr_market_data_type == "DELAYED_FROZEN"

    @pytest.mark.unit
    def test_market_data_type_enum_conversion(self):
        """Test market data type string to enum conversion."""
        settings = get_settings()

        # Test default value
        enum_value = settings.ibkr.get_market_data_type_enum()
        assert enum_value == MarketDataTypeEnum.DELAYED_FROZEN

    @pytest.mark.unit
    def test_account_id_hidden_in_str_repr(self):
        """Account identifier must not appear in str() or repr() of settings."""
        from src.config import IBKRSettings

        # Arrange
        account = "DU1234567"
        settings = IBKRSettings(_env_file=None, tws_account=account)

        # Act & Assert
        assert account not in str(settings)
        assert account not in repr(settings)

    @pytest.mark.unit
    def test_password_hidden_in_str_repr(self):
        """Password must not appear in str() or repr() of settings."""
        from src.config import IBKRSettings

        # Arrange
        password = "super-secret-password"
        settings = IBKRSettings(_env_file=None, tws_password=password)

        # Act & Assert
        assert password not in str(settings)
        assert password not in repr(settings)

    @pytest.mark.unit
    def test_real_money_account_defaults_to_empty(self, monkeypatch):
        """Real-money declaration defaults to empty (paper only)."""
        from src.config import IBKRSettings

        # Arrange — clear env vars and skip .env file. `case_sensitive: False`
        # (src/config.py) means the lowercase spelling is an equally valid env
        # alias, so clearing only the upper-case name leaves the test dependent
        # on the developer's shell.
        monkeypatch.delenv("NTRADER_REAL_MONEY_ACCOUNT", raising=False)
        monkeypatch.delenv("ntrader_real_money_account", raising=False)

        # Act
        settings = IBKRSettings(_env_file=None)

        # Assert
        assert settings.ntrader_real_money_account == ""

    @pytest.mark.unit
    def test_real_money_account_loads_from_env(self, monkeypatch):
        """Real-money declaration is read from NTRADER_REAL_MONEY_ACCOUNT."""
        from src.config import IBKRSettings

        # Arrange
        monkeypatch.setenv("NTRADER_REAL_MONEY_ACCOUNT", "U1234567")

        # Act
        settings = IBKRSettings(_env_file=None)

        # Assert
        assert settings.ntrader_real_money_account == "U1234567"

    @pytest.mark.unit
    def test_real_money_account_hidden_in_str_repr(self, monkeypatch):
        """Real-money account must not appear in str() or repr() of settings."""
        from src.config import IBKRSettings

        # Arrange
        monkeypatch.delenv("NTRADER_REAL_MONEY_ACCOUNT", raising=False)
        monkeypatch.delenv("ntrader_real_money_account", raising=False)
        account = "U1234567"
        settings = IBKRSettings(_env_file=None, ntrader_real_money_account=account)

        # Act & Assert
        assert account not in str(settings)
        assert account not in repr(settings)

    @pytest.mark.unit
    def test_market_data_type_enum_all_values(self, monkeypatch):
        """Test all market data type enum conversions."""
        from src.config import IBKRSettings

        test_cases = {
            "REALTIME": MarketDataTypeEnum.REALTIME,
            "FROZEN": MarketDataTypeEnum.FROZEN,
            "DELAYED": MarketDataTypeEnum.DELAYED,
            "DELAYED_FROZEN": MarketDataTypeEnum.DELAYED_FROZEN,
            "realtime": MarketDataTypeEnum.REALTIME,  # Test case insensitivity
            "invalid": MarketDataTypeEnum.DELAYED_FROZEN,  # Test default fallback
        }

        for string_value, expected_enum in test_cases.items():
            settings = IBKRSettings(ibkr_market_data_type=string_value)
            assert settings.get_market_data_type_enum() == expected_enum
