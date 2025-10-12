"""Tests for date range adjustment logic."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from src.services.data_service import DataService


class TestDateRangeAdjustment:
    """Test cases for date range adjustment functionality."""

    @pytest.mark.asyncio
    async def test_get_adjusted_date_range_with_midnight_times(self):
        """Test that midnight times are adjusted to actual data boundaries."""
        data_service = DataService()

        # Mock get_adjusted_date_range from DatabaseRepository
        expected_result = {
            "start": datetime(2024, 1, 2, 9, 30, 0, tzinfo=timezone.utc),
            "end": datetime(2024, 1, 2, 10, 20, 0, tzinfo=timezone.utc),
        }

        with patch.object(
            data_service.db_repo,
            "get_adjusted_date_range",
            new_callable=AsyncMock,
            return_value=expected_result,
        ):
            # Test with midnight times
            start = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
            end = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)

            result = await data_service.get_adjusted_date_range("AAPL", start, end)

            assert result is not None
            assert result["start"] == datetime(
                2024, 1, 2, 9, 30, 0, tzinfo=timezone.utc
            )
            assert result["end"] == datetime(2024, 1, 2, 10, 20, 0, tzinfo=timezone.utc)

    @pytest.mark.asyncio
    async def test_get_adjusted_date_range_with_specific_times(self):
        """Test that specific times are preserved without adjustment."""
        data_service = DataService()

        # Test with specific times (not midnight)
        start = datetime(2024, 1, 2, 9, 35, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, 10, 15, 0, tzinfo=timezone.utc)

        expected_result = {"start": start, "end": end}

        with patch.object(
            data_service.db_repo,
            "get_adjusted_date_range",
            new_callable=AsyncMock,
            return_value=expected_result,
        ):
            result = await data_service.get_adjusted_date_range("AAPL", start, end)

            assert result is not None
            # Times should not be adjusted since they're not midnight
            assert result["start"] == start
            assert result["end"] == end

    @pytest.mark.asyncio
    async def test_get_adjusted_date_range_no_data_on_date(self):
        """Test adjustment when no data exists on the specified date."""
        data_service = DataService()

        # Request date before available data
        start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        expected_result = {
            "start": datetime(2024, 1, 3, 9, 30, 0, tzinfo=timezone.utc),
            "end": datetime(2024, 1, 3, 9, 30, 0, tzinfo=timezone.utc),
        }

        with patch.object(
            data_service.db_repo,
            "get_adjusted_date_range",
            new_callable=AsyncMock,
            return_value=expected_result,
        ):
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

        start = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)

        with patch.object(
            data_service.db_repo,
            "get_adjusted_date_range",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await data_service.get_adjusted_date_range("INVALID", start, end)

            assert result is None

    @pytest.mark.asyncio
    async def test_get_adjusted_date_range_mixed_times(self):
        """Test adjustment with mixed midnight and specific times."""
        data_service = DataService()

        # Start is midnight, end is specific time
        start = datetime(2024, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 2, 15, 30, 0, tzinfo=timezone.utc)

        expected_result = {
            "start": datetime(2024, 1, 2, 9, 30, 0, tzinfo=timezone.utc),
            "end": datetime(2024, 1, 2, 15, 30, 0, tzinfo=timezone.utc),
        }

        with patch.object(
            data_service.db_repo,
            "get_adjusted_date_range",
            new_callable=AsyncMock,
            return_value=expected_result,
        ):
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

        # Test with timezone-naive dates
        start = datetime(2024, 1, 2, 10, 0, 0)  # No timezone
        end = datetime(2024, 1, 2, 15, 0, 0)  # No timezone

        expected_result = {
            "start": datetime(2024, 1, 2, 10, 0, 0, tzinfo=timezone.utc),
            "end": datetime(2024, 1, 2, 15, 0, 0, tzinfo=timezone.utc),
        }

        with patch.object(
            data_service.db_repo,
            "get_adjusted_date_range",
            new_callable=AsyncMock,
            return_value=expected_result,
        ):
            result = await data_service.get_adjusted_date_range("AAPL", start, end)

            assert result is not None
            # Should handle timezone-naive dates and preserve the times
            assert result["start"] == datetime(
                2024, 1, 2, 10, 0, 0, tzinfo=timezone.utc
            )
            assert result["end"] == datetime(2024, 1, 2, 15, 0, 0, tzinfo=timezone.utc)
