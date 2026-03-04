"""Unit tests for KrakenSettings configuration."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.config import KrakenSettings


class TestKrakenSettingsDefaults:
    """Test suite for KrakenSettings default values."""

    def test_default_api_key_is_empty(self):
        """Default API key is empty string."""
        # Arrange & Act
        settings = KrakenSettings()

        # Assert
        assert settings.kraken_api_key == ""

    def test_default_api_secret_is_empty(self):
        """Default API secret is empty string."""
        # Arrange & Act
        settings = KrakenSettings()

        # Assert
        assert settings.kraken_api_secret == ""

    def test_default_rate_limit_is_10(self):
        """Default rate limit is 10 requests per second."""
        # Arrange & Act
        settings = KrakenSettings()

        # Assert
        assert settings.kraken_rate_limit == 10

    def test_default_maker_fee(self):
        """Default maker fee is 0.0016 (0.16%)."""
        # Arrange & Act
        settings = KrakenSettings()

        # Assert
        assert settings.kraken_default_maker_fee == Decimal("0.0016")

    def test_default_taker_fee(self):
        """Default taker fee is 0.0026 (0.26%)."""
        # Arrange & Act
        settings = KrakenSettings()

        # Assert
        assert settings.kraken_default_taker_fee == Decimal("0.0026")


class TestKrakenSettingsEnvOverrides:
    """Test suite for KrakenSettings environment variable overrides."""

    def test_api_key_from_env(self, monkeypatch):
        """API key loads from KRAKEN_API_KEY env var."""
        # Arrange
        monkeypatch.setenv("KRAKEN_API_KEY", "test-key-123")
        monkeypatch.setenv("KRAKEN_API_SECRET", "test-secret")

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

    def test_maker_fee_from_env(self, monkeypatch):
        """Maker fee loads from KRAKEN_DEFAULT_MAKER_FEE env var."""
        # Arrange
        monkeypatch.setenv("KRAKEN_DEFAULT_MAKER_FEE", "0.001")

        # Act
        settings = KrakenSettings()

        # Assert
        assert settings.kraken_default_maker_fee == Decimal("0.001")

    def test_taker_fee_from_env(self, monkeypatch):
        """Taker fee loads from KRAKEN_DEFAULT_TAKER_FEE env var."""
        # Arrange
        monkeypatch.setenv("KRAKEN_DEFAULT_TAKER_FEE", "0.002")

        # Act
        settings = KrakenSettings()

        # Assert
        assert settings.kraken_default_taker_fee == Decimal("0.002")


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
        settings = KrakenSettings(kraken_api_key="my-key", kraken_api_secret="my-secret")

        # Assert
        assert settings.kraken_api_key == "my-key"
        assert settings.kraken_api_secret == "my-secret"

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


class TestKrakenSettingsFeeValidation:
    """Test suite for fee range validation (0-1)."""

    def test_maker_fee_zero_is_valid(self):
        """Maker fee of 0 is valid."""
        # Arrange & Act
        settings = KrakenSettings(kraken_default_maker_fee=Decimal("0"))

        # Assert
        assert settings.kraken_default_maker_fee == Decimal("0")

    def test_maker_fee_one_is_valid(self):
        """Maker fee of 1 is valid (100%)."""
        # Arrange & Act
        settings = KrakenSettings(kraken_default_maker_fee=Decimal("1"))

        # Assert
        assert settings.kraken_default_maker_fee == Decimal("1")

    def test_maker_fee_negative_raises_error(self):
        """Maker fee of -1 raises validation error."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="kraken_default_maker_fee"):
            KrakenSettings(kraken_default_maker_fee=Decimal("-1"))

    def test_maker_fee_above_one_raises_error(self):
        """Maker fee above 1 raises validation error."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="kraken_default_maker_fee"):
            KrakenSettings(kraken_default_maker_fee=Decimal("2"))

    def test_taker_fee_negative_raises_error(self):
        """Taker fee of -1 raises validation error."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="kraken_default_taker_fee"):
            KrakenSettings(kraken_default_taker_fee=Decimal("-1"))

    def test_taker_fee_above_one_raises_error(self):
        """Taker fee above 1 raises validation error."""
        # Arrange & Act & Assert
        with pytest.raises(ValidationError, match="kraken_default_taker_fee"):
            KrakenSettings(kraken_default_taker_fee=Decimal("2"))
