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


class TestIBKRClientIdIsolation:
    """Test suite for live/historical IBKR client ID isolation (FR5, AR18).

    IBKR evicts the incumbent when a second connection presents an ID already in
    use, so a live session sharing the catalog fetcher's ID would be knocked off
    its socket mid-position by an ordinary historical import.
    """

    @staticmethod
    def _clear_client_id_env(monkeypatch):
        """Clear both casings of both client-ID env vars.

        `case_sensitive: False` (src/config.py) makes the lowercase spelling an
        equally valid alias, so clearing only the upper-case name leaves the test
        dependent on the developer's shell.
        """
        for name in ("IBKR_CLIENT_ID", "IBKR_LIVE_CLIENT_ID"):
            monkeypatch.delenv(name, raising=False)
            monkeypatch.delenv(name.lower(), raising=False)

    @pytest.mark.unit
    def test_client_id_defaults_are_distinct(self, monkeypatch):
        """Historical defaults to 1 and live to 10 — the pair is the contract."""
        from src.config import IBKRSettings

        # Arrange
        self._clear_client_id_env(monkeypatch)

        # Act
        settings = IBKRSettings(_env_file=None)

        # Assert
        assert settings.ibkr_client_id == 1
        assert settings.ibkr_live_client_id == 10

    @pytest.mark.unit
    def test_live_client_id_loads_from_env(self, monkeypatch):
        """IBKR_LIVE_CLIENT_ID overrides the default (no env_prefix in play)."""
        from src.config import IBKRSettings

        # Arrange
        self._clear_client_id_env(monkeypatch)
        monkeypatch.setenv("IBKR_LIVE_CLIENT_ID", "20")

        # Act
        settings = IBKRSettings(_env_file=None)

        # Assert
        assert settings.ibkr_live_client_id == 20

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "overrides",
        [
            pytest.param({"ibkr_live_client_id": 1}, id="live-collides-with-default-historical"),
            pytest.param({"ibkr_client_id": 10}, id="historical-collides-with-default-live"),
            pytest.param(
                {"ibkr_client_id": 7, "ibkr_live_client_id": 7}, id="both-overridden-equal"
            ),
        ],
    )
    def test_equal_client_ids_are_rejected(self, monkeypatch, overrides):
        """Equality is rejected from either override direction (model, not field, validator)."""
        from pydantic import ValidationError

        from src.config import IBKRSettings

        # Arrange
        self._clear_client_id_env(monkeypatch)

        # Act
        with pytest.raises(ValidationError) as excinfo:
            IBKRSettings(_env_file=None, **overrides)

        # Assert — an operator reading the traceback needs both setting names
        message = str(excinfo.value)
        assert "ibkr_client_id" in message
        assert "ibkr_live_client_id" in message

    @pytest.mark.unit
    def test_distinct_client_ids_are_permitted(self, monkeypatch):
        """Non-equal IDs construct without error."""
        from src.config import IBKRSettings

        # Arrange
        self._clear_client_id_env(monkeypatch)

        # Act
        settings = IBKRSettings(_env_file=None, ibkr_client_id=1, ibkr_live_client_id=10)

        # Assert
        assert settings.ibkr_client_id == 1
        assert settings.ibkr_live_client_id == 10

    @pytest.mark.unit
    def test_env_driven_collision_is_rejected(self, monkeypatch):
        """A stale IBKR_CLIENT_ID=10 in the environment is rejected — the real-world shape."""
        from pydantic import ValidationError

        from src.config import IBKRSettings

        # Arrange
        self._clear_client_id_env(monkeypatch)
        monkeypatch.setenv("IBKR_CLIENT_ID", "10")

        # Act & Assert
        with pytest.raises(ValidationError) as excinfo:
            IBKRSettings(_env_file=None)

        message = str(excinfo.value)
        assert "ibkr_client_id" in message
        assert "ibkr_live_client_id" in message

    @pytest.mark.unit
    def test_reservation_is_documented_in_field_metadata(self):
        """The client-ID reservation is machine-readable, so it cannot rot silently."""
        from src.config import IBKRSettings

        # Act
        description = IBKRSettings.model_fields["ibkr_live_client_id"].description

        # Assert — loose substrings survive rewording but fail if the reservation is dropped
        assert description is not None
        assert "ibkr_client_id" in description
        assert "+ 1" in description

    # -- Rotation-range isolation (code review 2026-08-04) --------------------
    #
    # Equality alone is not the contract. IBKRHistoricalClient.connect() rotates
    # base..base+5 on connect timeouts (src/services/ibkr_client.py), so the
    # historical client's *effective* range must not touch either reserved live
    # ID. With the default live ID of 10, every base in 5..11 collides.

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "historical",
        [
            pytest.param(5, id="rotation-ceiling-reaches-live-id"),
            pytest.param(6, id="rotation-covers-live-and-reconcile"),
            pytest.param(9, id="base-just-below-live"),
            pytest.param(11, id="base-is-the-reconcile-id"),
        ],
    )
    def test_historical_rotation_range_may_not_reach_the_reserved_ids(
        self, monkeypatch, historical
    ):
        """A base that is merely unequal is not safe if its rotation range collides."""
        from pydantic import ValidationError

        from src.config import IBKRSettings

        # Arrange
        self._clear_client_id_env(monkeypatch)

        # Act & Assert — live defaults to 10, so reconcile is 11
        with pytest.raises(ValidationError) as excinfo:
            IBKRSettings(_env_file=None, ibkr_client_id=historical)

        message = str(excinfo.value)
        assert "ibkr_client_id" in message
        assert "ibkr_live_client_id" in message

    @pytest.mark.unit
    def test_live_id_may_not_sit_inside_the_historical_rotation_range(self, monkeypatch):
        """The collision is symmetric — lowering the live ID is as fatal as raising the base."""
        from pydantic import ValidationError

        from src.config import IBKRSettings

        # Arrange
        self._clear_client_id_env(monkeypatch)

        # Act & Assert — historical 1 rotates 1..6, so a live ID of 3 sits inside it
        with pytest.raises(ValidationError):
            IBKRSettings(_env_file=None, ibkr_client_id=1, ibkr_live_client_id=3)

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("historical", "live"),
        [
            pytest.param(1, 10, id="repo-defaults"),
            pytest.param(4, 10, id="highest-safe-base"),
            pytest.param(1, 20, id="live-raised"),
            pytest.param(20, 1, id="historical-far-above-live"),
        ],
    )
    def test_non_overlapping_ranges_are_permitted(self, monkeypatch, historical, live):
        """Configurations with genuine clearance still construct."""
        from src.config import IBKRSettings

        # Arrange
        self._clear_client_id_env(monkeypatch)

        # Act
        settings = IBKRSettings(_env_file=None, ibkr_client_id=historical, ibkr_live_client_id=live)

        # Assert
        assert settings.ibkr_client_id == historical
        assert settings.ibkr_live_client_id == live

    # -- Bounds ---------------------------------------------------------------

    @pytest.mark.unit
    @pytest.mark.parametrize("field", ["ibkr_client_id", "ibkr_live_client_id"])
    @pytest.mark.parametrize("value", [0, -5])
    def test_client_ids_below_one_are_rejected(self, monkeypatch, field, value):
        """Client ID 0 is IBKR's master client (binds manual TWS orders); negatives are invalid."""
        from pydantic import ValidationError

        from src.config import IBKRSettings

        # Arrange
        self._clear_client_id_env(monkeypatch)

        # Act & Assert
        with pytest.raises(ValidationError):
            IBKRSettings(_env_file=None, **{field: value})

    # -- Error output hygiene -------------------------------------------------

    @pytest.mark.unit
    def test_validation_error_does_not_echo_the_environment(self, monkeypatch):
        """A collision must not print unrelated env values into the traceback.

        pydantic-settings passes the whole environment in as the model input, so
        without hide_input_in_errors a config mistake renders secrets held in
        other variables (e.g. FMP_API_KEY) into logs and CI output.
        """
        from pydantic import ValidationError

        from src.config import IBKRSettings

        # Arrange
        self._clear_client_id_env(monkeypatch)
        monkeypatch.setenv("FMP_API_KEY", "super-secret-key-value")
        monkeypatch.setenv("IBKR_CLIENT_ID", "10")

        # Act
        with pytest.raises(ValidationError) as excinfo:
            IBKRSettings(_env_file=None)

        # Assert — the message explains the problem without quoting the input
        message = str(excinfo.value)
        assert "super-secret-key-value" not in message
        assert "input_value=" not in message
        assert "ibkr_live_client_id" in message
