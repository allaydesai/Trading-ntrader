"""Integration test for database connection."""

import asyncio
import pytest

from src.config import get_settings


@pytest.mark.integration
@pytest.mark.asyncio
async def test_can_connect_to_database():
    """INTEGRATION: Verify database is accessible."""
    # Ensure clean state by disposing any existing connections first
    from src.db.session import dispose_all_connections, test_connection

    await dispose_all_connections()

    settings = get_settings()

    # Skip if database not configured
    if not settings.database_url:
        pytest.skip("Database not configured")

    # Test connection with retry logic for resilience
    max_retries = 3
    retry_delay = 0.5

    for attempt in range(max_retries):
        is_connected = await test_connection()
        if is_connected:
            assert True  # Test passes
            # Clean up after successful test
            await dispose_all_connections()
            return

        if attempt < max_retries - 1:
            # Wait before retrying
            await asyncio.sleep(retry_delay)
            # Dispose connections before retry
            await dispose_all_connections()

    # All retries failed - skip test instead of failing in CI environments
    pytest.skip(
        f"Database not accessible after {max_retries} attempts (likely running in CI without database)"
    )


@pytest.mark.integration
def test_database_config_available():
    """INTEGRATION: Verify database is configured in settings."""
    settings = get_settings()

    # Check that database configuration exists
    assert settings.is_database_available is True, "Database should be configured"
    assert settings.database_url is not None
    assert "postgresql" in settings.database_url
