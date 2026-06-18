"""Unit tests for FMPSettings configuration."""

import pytest
from pydantic import ValidationError

from src.config import FMPSettings, Settings, get_settings


class TestFMPSettingsDefaults:
    """Test suite for FMPSettings default values."""

    def test_default_base_url(self, monkeypatch):
        """Default base URL is the FMP stable endpoint."""
        # Arrange — required key set so construction succeeds, skip .env
        monkeypatch.setenv("FMP_API_KEY", "test-fmp-key")

        # Act
        settings = FMPSettings(_env_file=None)

        # Assert
        assert settings.fmp_base_url == "https://financialmodelingprep.com/stable"

    def test_default_rate_limit_is_300(self, monkeypatch):
        """Default rate limit is 300 requests per minute (Starter quota)."""
        # Arrange
        monkeypatch.setenv("FMP_API_KEY", "test-fmp-key")

        # Act
        settings = FMPSettings(_env_file=None)

        # Assert
        assert settings.fmp_rate_limit == 300

    def test_default_request_timeout_is_30(self, monkeypatch):
        """Default request timeout is 30 seconds."""
        # Arrange
        monkeypatch.setenv("FMP_API_KEY", "test-fmp-key")

        # Act
        settings = FMPSettings(_env_file=None)

        # Assert
        assert settings.fmp_request_timeout == 30


class TestFMPSettingsOptionalKey:
    """Test suite for the optional fmp_api_key field.

    The key defaults to an empty string (mirroring IBKR/Kraken credentials) so
    that constructing ``Settings()`` never fails when FMP_API_KEY is unset —
    only code that actually uses the FMP client requires a real key. A hard
    required field would break every module that builds settings at import time.
    """

    def test_missing_api_key_defaults_to_empty(self, monkeypatch):
        """Unset FMP_API_KEY yields an empty string, not a ValidationError."""
        # Arrange — clear env var and skip .env so the field is truly absent
        monkeypatch.delenv("FMP_API_KEY", raising=False)

        # Act
        settings = FMPSettings(_env_file=None)

        # Assert
        assert settings.fmp_api_key == ""


class TestFMPSettingsEnvOverrides:
    """Test suite for FMPSettings environment variable overrides."""

    def test_api_key_from_env(self, monkeypatch):
        """API key loads from FMP_API_KEY env var."""
        # Arrange
        monkeypatch.setenv("FMP_API_KEY", "live-fmp-key-123")

        # Act
        settings = FMPSettings(_env_file=None)

        # Assert
        assert settings.fmp_api_key == "live-fmp-key-123"

    def test_base_url_from_env(self, monkeypatch):
        """Base URL loads from FMP_BASE_URL env var."""
        # Arrange
        monkeypatch.setenv("FMP_API_KEY", "test-fmp-key")
        monkeypatch.setenv("FMP_BASE_URL", "https://example.test/v4")

        # Act
        settings = FMPSettings(_env_file=None)

        # Assert
        assert settings.fmp_base_url == "https://example.test/v4"

    def test_rate_limit_from_env(self, monkeypatch):
        """Rate limit loads from FMP_RATE_LIMIT env var."""
        # Arrange
        monkeypatch.setenv("FMP_API_KEY", "test-fmp-key")
        monkeypatch.setenv("FMP_RATE_LIMIT", "750")

        # Act
        settings = FMPSettings(_env_file=None)

        # Assert
        assert settings.fmp_rate_limit == 750

    def test_request_timeout_from_env(self, monkeypatch):
        """Request timeout loads from FMP_REQUEST_TIMEOUT env var."""
        # Arrange
        monkeypatch.setenv("FMP_API_KEY", "test-fmp-key")
        monkeypatch.setenv("FMP_REQUEST_TIMEOUT", "10")

        # Act
        settings = FMPSettings(_env_file=None)

        # Assert
        assert settings.fmp_request_timeout == 10


class TestFMPSettingsRangeValidation:
    """Test suite for numeric range validation (ge=1)."""

    def test_rate_limit_zero_raises_error(self, monkeypatch):
        """Rate limit of 0 raises validation error (ge=1)."""
        # Arrange
        monkeypatch.setenv("FMP_API_KEY", "test-fmp-key")

        # Act & Assert
        with pytest.raises(ValidationError, match="fmp_rate_limit"):
            FMPSettings(_env_file=None, fmp_rate_limit=0)

    def test_request_timeout_zero_raises_error(self, monkeypatch):
        """Request timeout of 0 raises validation error (ge=1)."""
        # Arrange
        monkeypatch.setenv("FMP_API_KEY", "test-fmp-key")

        # Act & Assert
        with pytest.raises(ValidationError, match="fmp_request_timeout"):
            FMPSettings(_env_file=None, fmp_request_timeout=0)


class TestFMPSettingsCredentialHardening:
    """Test suite for API key credential hygiene (repr=False)."""

    def test_api_key_hidden_in_repr(self, monkeypatch):
        """Raw API key value must be absent from repr(FMPSettings(...))."""
        # Arrange
        api_key = "super-secret-fmp-key"
        monkeypatch.setenv("FMP_API_KEY", api_key)

        # Act
        settings = FMPSettings(_env_file=None)

        # Assert
        assert api_key not in repr(settings)

    def test_api_key_hidden_in_str(self, monkeypatch):
        """Raw API key value must be absent from str(FMPSettings(...))."""
        # Arrange
        api_key = "super-secret-fmp-key"
        monkeypatch.setenv("FMP_API_KEY", api_key)

        # Act
        settings = FMPSettings(_env_file=None)

        # Assert
        assert api_key not in str(settings)


class TestFMPSettingsNesting:
    """Test suite for FMPSettings nested under the top-level Settings."""

    def test_settings_fmp_is_fmp_settings_instance(self, monkeypatch):
        """Settings.fmp is an FMPSettings instance (mirrors ibkr/kraken)."""
        # Arrange
        monkeypatch.setenv("FMP_API_KEY", "test-fmp-key")

        # Act
        settings = Settings()

        # Assert
        assert isinstance(settings.fmp, FMPSettings)

    def test_settings_fmp_reads_api_key_from_env(self, monkeypatch):
        """Settings().fmp.fmp_api_key reads from env via the nested factory."""
        # Arrange
        monkeypatch.setenv("FMP_API_KEY", "nested-fmp-key")

        # Act
        settings = Settings()

        # Assert
        assert settings.fmp.fmp_api_key == "nested-fmp-key"

    def test_get_settings_fmp_is_fmp_settings_instance(self, monkeypatch):
        """get_settings().fmp is an FMPSettings instance."""
        # Arrange
        monkeypatch.setenv("FMP_API_KEY", "test-fmp-key")

        # Act & Assert
        assert isinstance(get_settings().fmp, FMPSettings)
