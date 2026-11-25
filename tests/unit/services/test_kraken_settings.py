"""Unit tests for KrakenSettings configuration."""

from unittest.mock import patch

import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from src.config import KrakenSettings
from src.services.exceptions import KrakenConnectionError
from src.services.kraken_client import KrakenHistoricalClient


class TestKrakenSettingsDefaults:
    """Test suite for KrakenSettings default values."""

    def test_default_api_key_is_empty(self, monkeypatch):
        """Default API key is empty string."""
        # Arrange — clear env vars and skip .env file
        monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
        monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)

        # Act
        settings = KrakenSettings(_env_file=None)

        # Assert
        assert settings.kraken_api_key == ""

    def test_default_api_secret_is_empty(self, monkeypatch):
        """Default API secret is empty string."""
        # Arrange — clear env vars and skip .env file
        monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
        monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)

        # Act
        settings = KrakenSettings(_env_file=None)

        # Assert
        assert settings.kraken_api_secret == ""

    def test_default_rate_limit_is_10(self):
        """Default rate limit is 10 requests per second."""
        # Arrange & Act
        settings = KrakenSettings()

        # Assert
        assert settings.kraken_rate_limit == 10

    def test_no_default_fee_settings(self):
        """The unused default fee settings are gone: runs charge the coin_specs.yaml schedule."""
        settings = KrakenSettings()
        assert not hasattr(settings, "kraken_default_maker_fee")
        assert not hasattr(settings, "kraken_default_taker_fee")


class TestKrakenSettingsEnvOverrides:
    """Test suite for KrakenSettings environment variable overrides."""

    def test_api_key_from_env(self, monkeypatch):
        """API key loads from KRAKEN_API_KEY env var."""
        # Arrange
        monkeypatch.setenv("KRAKEN_API_KEY", "test-key-123")
        monkeypatch.setenv("KRAKEN_API_SECRET", "dGVzdC1zZWNyZXQ=")

        # Act
        settings = KrakenSettings()

        # Assert
        assert settings.kraken_api_key == "test-key-123"

    def test_api_secret_from_env(self, monkeypatch):
        """API secret loads from KRAKEN_API_SECRET env var."""
        # Arrange
        monkeypatch.setenv("KRAKEN_API_KEY", "test-key")
        monkeypatch.setenv("KRAKEN_API_SECRET", "dGVzdC1zZWNyZXQ=")

        # Act
        settings = KrakenSettings()

        # Assert
        assert settings.kraken_api_secret == "dGVzdC1zZWNyZXQ="

    def test_rate_limit_from_env(self, monkeypatch):
        """Rate limit loads from KRAKEN_RATE_LIMIT env var."""
        # Arrange
        monkeypatch.setenv("KRAKEN_RATE_LIMIT", "15")

        # Act
        settings = KrakenSettings()

        # Assert
        assert settings.kraken_rate_limit == 15

    def test_leftover_default_fee_env_vars_are_ignored(self, monkeypatch):
        """An old .env that still sets KRAKEN_DEFAULT_*_FEE loads without error."""
        monkeypatch.setenv("KRAKEN_DEFAULT_MAKER_FEE", "0.001")
        monkeypatch.setenv("KRAKEN_DEFAULT_TAKER_FEE", "0.002")

        settings = KrakenSettings()

        assert not hasattr(settings, "kraken_default_maker_fee")
        assert not hasattr(settings, "kraken_default_taker_fee")


class TestKrakenSettingsKeySecretPairValidation:
    """Test suite for API key/secret pair consistency validation."""

    def test_both_key_and_secret_empty_is_valid(self):
        """Both key and secret empty is valid (no auth mode)."""
        # Arrange & Act
        settings = KrakenSettings(kraken_api_key="", kraken_api_secret="")

        # Assert
        assert settings.kraken_api_key == ""
        assert settings.kraken_api_secret == ""

    def test_both_key_and_secret_set_is_valid(self):
        """Both key and secret set is valid (authenticated mode)."""
        # Arrange & Act
        settings = KrakenSettings(kraken_api_key="my-key", kraken_api_secret="bXktc2VjcmV0")

        # Assert
        assert settings.kraken_api_key == "my-key"
        assert settings.kraken_api_secret == "bXktc2VjcmV0"

    def test_key_without_secret_raises_error(self):
        """API key set without secret raises validation error."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="key.*secret"):
            KrakenSettings(kraken_api_key="my-key", kraken_api_secret="")

    def test_secret_without_key_raises_error(self):
        """API secret set without key raises validation error."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="key.*secret"):
            KrakenSettings(kraken_api_key="", kraken_api_secret="my-secret")


class TestKrakenSettingsRateLimitValidation:
    """Test suite for rate limit range validation (1-20)."""

    def test_rate_limit_minimum_valid(self):
        """Rate limit of 1 is valid (minimum)."""
        # Arrange & Act
        settings = KrakenSettings(kraken_rate_limit=1)

        # Assert
        assert settings.kraken_rate_limit == 1

    def test_rate_limit_maximum_valid(self):
        """Rate limit of 20 is valid (maximum)."""
        # Arrange & Act
        settings = KrakenSettings(kraken_rate_limit=20)

        # Assert
        assert settings.kraken_rate_limit == 20

    def test_rate_limit_zero_raises_error(self):
        """Rate limit of 0 raises validation error."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="kraken_rate_limit"):
            KrakenSettings(kraken_rate_limit=0)

    def test_rate_limit_above_max_raises_error(self):
        """Rate limit of 21 raises validation error."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="kraken_rate_limit"):
            KrakenSettings(kraken_rate_limit=21)


class TestKrakenCredentialValidationOnConnect:
    """Test suite for credential validation during connect()."""

    @pytest.mark.asyncio
    async def test_connect_without_credentials_succeeds(self):
        """Spot OHLC and AssetPairs are public endpoints (crypto M7): no key is required."""
        # Arrange
        client = KrakenHistoricalClient(api_key="", api_secret="")

        # Act
        with patch("src.services.kraken_client.SpotMarket"):
            result = await client.connect()

        # Assert
        assert result["connected"] is True

    @pytest.mark.asyncio
    async def test_connect_secret_without_key_raises_descriptive_error(self):
        """Secret set but key empty raises KrakenConnectionError mentioning KRAKEN_API_KEY."""
        client = KrakenHistoricalClient(api_key="", api_secret="c2VjcmV0")
        with pytest.raises(KrakenConnectionError, match="KRAKEN_API_KEY"):
            await client.connect()

    @pytest.mark.asyncio
    async def test_connect_key_without_secret_raises_descriptive_error(self):
        """Key set but secret empty raises KrakenConnectionError mentioning KRAKEN_API_SECRET."""
        # Arrange
        client = KrakenHistoricalClient(api_key="my-key", api_secret="")

        # Act & Assert
        with pytest.raises(KrakenConnectionError, match="KRAKEN_API_SECRET"):
            await client.connect()

    @pytest.mark.asyncio
    async def test_connect_valid_credentials_succeeds(self):
        """Both key + secret set, SDK patched → returns connected: True."""
        # Arrange
        client = KrakenHistoricalClient(api_key="test-key", api_secret="test-secret")

        # Act
        with patch("src.services.kraken_client.SpotMarket"):
            result = await client.connect()

        # Assert
        assert result["connected"] is True

    def test_credentials_hidden_in_str_repr(self):
        """Secret value must not appear in str() or repr() of settings."""
        # Arrange
        secret = "c3VwZXItc2VjcmV0"
        settings = KrakenSettings(kraken_api_key="my-key", kraken_api_secret=secret)

        # Act & Assert
        assert secret not in str(settings)
        assert secret not in repr(settings)


class TestKrakenCredentialSecurityHardening:
    """Test suite for credential security hardening."""

    def test_repr_masks_api_secret(self):
        """Raw secret value must be absent from repr(KrakenSettings(...))."""
        # Arrange
        secret = "dGVzdC1zZWNyZXQ="
        settings = KrakenSettings(kraken_api_key="test-key", kraken_api_secret=secret)

        # Act
        settings_repr = repr(settings)

        # Assert
        assert secret not in settings_repr

    @pytest.mark.asyncio
    async def test_connection_error_does_not_leak_credentials(self):
        """When SDK raises, error message must not contain key or secret."""
        # Arrange
        api_key = "leaked-api-key-value"
        api_secret = "leaked-api-secret-value"
        client = KrakenHistoricalClient(api_key=api_key, api_secret=api_secret)

        # Act
        with patch(
            "src.services.kraken_client.SpotMarket",
            side_effect=Exception(f"auth failed with key={api_key}"),
        ):
            with pytest.raises(KrakenConnectionError) as exc_info:
                await client.connect()

        # Assert
        error_msg = str(exc_info.value)
        assert api_key not in error_msg
        assert api_secret not in error_msg

    @pytest.mark.asyncio
    async def test_log_entries_do_not_leak_secrets(self):
        """Log entries during connect must not contain credential values."""
        # Arrange
        api_key = "secret-key-for-log-test"
        api_secret = "secret-value-for-log-test"
        client = KrakenHistoricalClient(api_key=api_key, api_secret=api_secret)

        # Act
        with (
            patch("src.services.kraken_client.SpotMarket"),
            capture_logs() as cap_logs,
        ):
            await client.connect()

        # Assert
        for entry in cap_logs:
            entry_str = str(entry)
            assert api_key not in entry_str
            assert api_secret not in entry_str


class TestKrakenSettingsCredentialFormatValidation:
    """Test suite for API key/secret format validation."""

    def test_whitespace_only_key_raises_error(self):
        """API key with only whitespace raises validation error."""
        with pytest.raises(ValidationError, match="non-whitespace"):
            KrakenSettings(kraken_api_key="   ", kraken_api_secret="dGVzdA==")

    def test_invalid_base64_secret_raises_error(self):
        """API secret that is not valid base64 raises validation error."""
        with pytest.raises(ValidationError, match="base64"):
            KrakenSettings(kraken_api_key="my-key", kraken_api_secret="not-valid-b64!")

    def test_valid_base64_secret_accepted(self):
        """Valid base64 secret is accepted."""
        # "dGVzdC1zZWNyZXQ=" is base64 for "test-secret"
        settings = KrakenSettings(kraken_api_key="my-key", kraken_api_secret="dGVzdC1zZWNyZXQ=")
        assert settings.kraken_api_secret == "dGVzdC1zZWNyZXQ="

    def test_empty_credentials_skip_format_validation(self):
        """Empty key+secret bypass format checks (no-auth mode)."""
        settings = KrakenSettings(kraken_api_key="", kraken_api_secret="")
        assert settings.kraken_api_key == ""
        assert settings.kraken_api_secret == ""

    def test_client_repr_masks_credentials(self):
        """KrakenHistoricalClient repr must not expose credentials."""
        client = KrakenHistoricalClient(api_key="secret-key-value", api_secret="secret-value-here")
        client_repr = repr(client)
        assert "secret-key-value" not in client_repr
        assert "secret-value-here" not in client_repr
        assert "KrakenHistoricalClient" in client_repr
