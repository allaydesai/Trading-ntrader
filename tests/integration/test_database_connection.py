"""Integration test for database connection."""

import pytest

from src.config import get_settings


@pytest.mark.integration
@pytest.mark.asyncio
async def test_can_connect_to_database():
    """INTEGRATION: Verify database is accessible."""
    from src.db.session import test_connection

    settings = get_settings()

    # Skip if database not configured
    if not settings.database_url:
        pytest.skip("Database not configured")

    # Test connection
    is_connected = await test_connection()
    assert is_connected is True, "Should be able to connect to database"


@pytest.mark.integration
def test_database_config_available():
    """INTEGRATION: Verify database is configured in settings."""
    settings = get_settings()

    # Check that database configuration exists
    assert settings.is_database_available is True, "Database should be configured"
    assert settings.database_url is not None
    assert "postgresql" in settings.database_url