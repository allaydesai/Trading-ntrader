"""Tests for date range adjustment logic."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from src.services.data_service import DataService


class TestDateRangeAdjustment:
    """Test cases for date range adjustment functionality."""

    @pytest.mark.asyncio
    async def test_get_adjusted_date_range_with_midnight_times(self):
        """Test that midnight times are adjusted to actual data boundaries."""
        data_service = DataService()

        # Mock get_data_range
        data_service.get_data_range = AsyncMock(
            return_value={
                "start": datetime(2024, 1, 2, 9, 30, 0, tzinfo=timezone.utc),
                "end": datetime(2024, 1, 2, 10, 20, 0, tzinfo=timezone.utc),
            }
        )

        # Mock get_session for database queries
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_row = MagicMock()

        # Mock first timestamp query (for start date)
        mock_row.first_timestamp = datetime(2024, 1, 2, 9, 30, 0, tzinfo=timezone.utc)
        mock_result.fetchone.return_value = mock_row

        # We need to handle two database calls
        execute_call_count = 0

        async def mock_execute(query, params):
            nonlocal execute_call_count
            execute_call_count += 1
            if execute_call_count == 1:
                # First call is for start date
                mock_row.first_timestamp = datetime(
                    2024, 1, 2, 9, 30, 0, tzinfo=timezone.utc
                )
            else:
                # Second call is for end date
                mock_row.last_timestamp = datetime(
                    2024, 1, 2, 10, 20, 0, tzinfo=timezone.utc
                )
            return mock_result

        mock_session.execute = mock_execute

        # Mock get_session context manager
        from src.db import session

        original_get_session = session.get_session

        def mock_get_session():
            class MockContext:
                async def __aenter__(self):
                    return mock_session

                async def __aexit__(self, *args):
                    pass

            return MockContext()

        session.get_session = mock_get_session

        try:
            # Test with midnight times
            start = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
            end = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)

            result = await data_service.get_adjusted_date_range("AAPL", start, end)

            assert result is not None
            assert result["start"] == datetime(
                2024, 1, 2, 9, 30, 0, tzinfo=timezone.utc
            )
            assert result["end"] == datetime(2024, 1, 2, 10, 20, 0, tzinfo=timezone.utc)
        finally:
            session.get_session = original_get_session

    @pytest.mark.asyncio
    async def test_get_adjusted_date_range_with_specific_times(self):
        """Test that specific times are preserved without adjustment."""
        data_service = DataService()

        # Mock get_data_range
        data_service.get_data_range = AsyncMock(
            return_value={
                "start": datetime(2024, 1, 2, 9, 30, 0, tzinfo=timezone.utc),
                "end": datetime(2024, 1, 2, 10, 20, 0, tzinfo=timezone.utc),
            }
        )

        # Test with specific times (not midnight)
        start = datetime(2024, 1, 2, 9, 35, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, 10, 15, 0, tzinfo=timezone.utc)

        result = await data_service.get_adjusted_date_range("AAPL", start, end)

        assert result is not None
        # Times should not be adjusted since they're not midnight
        assert result["start"] == start
        assert result["end"] == end

    @pytest.mark.asyncio
    async def test_get_adjusted_date_range_no_data_on_date(self):
        """Test adjustment when no data exists on the specified date."""
        data_service = DataService()

        # Mock get_data_range with different dates
        data_service.get_data_range = AsyncMock(
            return_value={
                "start": datetime(2024, 1, 3, 9, 30, 0, tzinfo=timezone.utc),
                "end": datetime(2024, 1, 5, 16, 0, 0, tzinfo=timezone.utc),
            }
        )

        # Mock get_session for database queries
        mock_session = AsyncMock()
        mock_result = MagicMock()

        execute_call_count = 0

        async def mock_execute(query, params):
            nonlocal execute_call_count
            execute_call_count += 1

            # Mock no data found on requested date for both queries
            mock_row = MagicMock()
            if execute_call_count == 1:
                mock_row.first_timestamp = None
            else:
                mock_row.last_timestamp = None
            mock_result.fetchone.return_value = mock_row
            return mock_result

        mock_session.execute = mock_execute

        # Mock get_session context manager
        from unittest.mock import patch

        with patch("src.services.data_service.get_session") as mock_get_session_func:

            class MockContext:
                async def __aenter__(self):
                    return mock_session

                async def __aexit__(self, *args):
                    pass

            mock_get_session_func.return_value = MockContext()

            # Request date before available data
            start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
            end = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

            result = await data_service.get_adjusted_date_range("AAPL", start, end)

            assert result is not None
            # Should adjust to actual data range since no data on requested date
            assert result["start"] == datetime(
                2024, 1, 3, 9, 30, 0, tzinfo=timezone.utc
            )
            # End date would be the overall end since requested date is before data
            assert result["end"] == datetime(2024, 1, 3, 9, 30, 0, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_get_adjusted_date_range_no_data_at_all(self):
        """Test adjustment when no data exists for the symbol."""
        data_service = DataService()

        # Mock get_data_range returning None (no data)
        data_service.get_data_range = AsyncMock(return_value=None)

        start = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)

        result = await data_service.get_adjusted_date_range("INVALID", start, end)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_adjusted_date_range_mixed_times(self):
        """Test adjustment with mixed midnight and specific times."""
        data_service = DataService()

        # Mock get_data_range
        data_service.get_data_range = AsyncMock(
            return_value={
                "start": datetime(2024, 1, 2, 9, 30, 0, tzinfo=timezone.utc),
                "end": datetime(2024, 1, 2, 16, 0, 0, tzinfo=timezone.utc),
            }
        )

        # Mock get_session for database queries
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_row = MagicMock()

        # Mock first timestamp query (for start date)
        mock_row.first_timestamp = datetime(2024, 1, 2, 9, 30, 0, tzinfo=timezone.utc)
        mock_result.fetchone.return_value = mock_row

        async def mock_execute(query, params):
            return mock_result

        mock_session.execute = mock_execute

        # Mock get_session context manager
        from unittest.mock import patch

        with patch("src.services.data_service.get_session") as mock_get_session_func:

            class MockContext:
                async def __aenter__(self):
                    return mock_session

                async def __aexit__(self, *args):
                    pass

            mock_get_session_func.return_value = MockContext()

            # Start is midnight, end is specific time
            start = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
            end = datetime(2024, 1, 2, 15, 30, 0, tzinfo=timezone.utc)

            result = await data_service.get_adjusted_date_range("AAPL", start, end)

            assert result is not None
            # Start should be adjusted, end should remain as is
            assert result["start"] == datetime(
                2024, 1, 2, 9, 30, 0, tzinfo=timezone.utc
            )
            assert result["end"] == datetime(2024, 1, 2, 15, 30, 0, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_get_adjusted_date_range_timezone_handling(self):
        """Test that timezone-naive dates are properly handled."""
        data_service = DataService()

        # Mock get_data_range
        data_service.get_data_range = AsyncMock(
            return_value={
                "start": datetime(2024, 1, 2, 9, 30, 0, tzinfo=timezone.utc),
                "end": datetime(2024, 1, 2, 16, 0, 0, tzinfo=timezone.utc),
            }
        )

        # Test with timezone-naive dates
        start = datetime(2024, 1, 2, 10, 0, 0)  # No timezone
        end = datetime(2024, 1, 2, 15, 0, 0)  # No timezone

        result = await data_service.get_adjusted_date_range("AAPL", start, end)

        assert result is not None
        # Should handle timezone-naive dates and preserve the times
        assert result["start"] == datetime(2024, 1, 2, 10, 0, 0, tzinfo=timezone.utc)
        assert result["end"] == datetime(2024, 1, 2, 15, 0, 0, tzinfo=timezone.utc)
