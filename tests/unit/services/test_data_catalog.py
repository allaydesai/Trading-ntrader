"""Unit tests for DataCatalogService bar type filtering."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.services.data_catalog import DataCatalogService


class TestQueryBarsBarTypeFiltering:
    """Test suite for bar type filtering in query_bars.

    The Nautilus catalog.bars() API expects:
    - bar_types: list[str] - List of bar type strings (NOT BarType objects)
    - start: int - Start timestamp in nanoseconds
    - end: int - End timestamp in nanoseconds

    The bar_types strings follow the format:
    "{instrument_id}-{bar_type_spec}-EXTERNAL"
    e.g., "AAPL.NASDAQ-1-DAY-LAST-EXTERNAL"
    """

    @pytest.fixture
    def mock_catalog(self):
        """Create a mock ParquetDataCatalog."""
        catalog = MagicMock()
        return catalog

    @pytest.fixture
    def data_catalog_service(self, mock_catalog, tmp_path):
        """Create DataCatalogService with mocked catalog."""
        with patch("src.services.data_catalog.ParquetDataCatalog", return_value=mock_catalog):
            service = DataCatalogService(catalog_path=tmp_path)
            service.catalog = mock_catalog
            return service

    def test_query_bars_uses_bar_types_list_of_strings(self, data_catalog_service, mock_catalog):
        """Query bars passes bar_types as list of strings, not BarType objects.

        This test ensures we use the correct Nautilus API:
        - Parameter name: bar_types (plural, NOT bar_type)
        - Parameter type: list[str] (NOT BarType object)
        """
        # Arrange
        mock_bar = Mock()
        mock_catalog.bars.return_value = [mock_bar]

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, tzinfo=timezone.utc)

        # Act
        data_catalog_service.query_bars(
            instrument_id="AAPL.NASDAQ",
            start=start,
            end=end,
            bar_type_spec="1-MINUTE-LAST",
        )

        # Assert
        mock_catalog.bars.assert_called_once()
        call_kwargs = mock_catalog.bars.call_args[1]

        # Verify bar_types is a list of strings
        assert "bar_types" in call_kwargs, "Should use 'bar_types' parameter (plural)"
        assert isinstance(call_kwargs["bar_types"], list), "bar_types should be a list"
        assert len(call_kwargs["bar_types"]) == 1, "bar_types should have one element"
        assert isinstance(call_kwargs["bar_types"][0], str), "bar_types elements should be strings"

        # Verify the string format
        expected_bar_type_str = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
        assert call_kwargs["bar_types"][0] == expected_bar_type_str

        # Verify instrument_ids is NOT passed (it's not needed when using bar_types)
        assert "instrument_ids" not in call_kwargs or call_kwargs.get("instrument_ids") is None

    def test_query_bars_constructs_correct_bar_type_string_for_one_day(
        self, data_catalog_service, mock_catalog
    ):
        """Query bars constructs correct bar type string for 1-DAY-LAST."""
        # Arrange
        mock_bar = Mock()
        mock_catalog.bars.return_value = [mock_bar]

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 12, 31, tzinfo=timezone.utc)

        # Act
        data_catalog_service.query_bars(
            instrument_id="SPY.ARCA",
            start=start,
            end=end,
            bar_type_spec="1-DAY-LAST",
        )

        # Assert
        call_kwargs = mock_catalog.bars.call_args[1]
        assert call_kwargs["bar_types"] == ["SPY.ARCA-1-DAY-LAST-EXTERNAL"]

    def test_query_bars_constructs_correct_bar_type_string_for_one_hour(
        self, data_catalog_service, mock_catalog
    ):
        """Query bars constructs correct bar type string for 1-HOUR-LAST."""
        # Arrange
        mock_bar = Mock()
        mock_catalog.bars.return_value = [mock_bar]

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, tzinfo=timezone.utc)

        # Act
        data_catalog_service.query_bars(
            instrument_id="TSLA.NASDAQ",
            start=start,
            end=end,
            bar_type_spec="1-HOUR-LAST",
        )

        # Assert
        call_kwargs = mock_catalog.bars.call_args[1]
        assert call_kwargs["bar_types"] == ["TSLA.NASDAQ-1-HOUR-LAST-EXTERNAL"]

    def test_query_bars_constructs_correct_bar_type_string_for_five_minute(
        self, data_catalog_service, mock_catalog
    ):
        """Query bars constructs correct bar type string for 5-MINUTE-LAST."""
        # Arrange
        mock_bar = Mock()
        mock_catalog.bars.return_value = [mock_bar]

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, tzinfo=timezone.utc)

        # Act
        data_catalog_service.query_bars(
            instrument_id="TSLA.NASDAQ",
            start=start,
            end=end,
            bar_type_spec="5-MINUTE-LAST",
        )

        # Assert
        call_kwargs = mock_catalog.bars.call_args[1]
        assert call_kwargs["bar_types"] == ["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"]

    def test_query_bars_passes_correct_time_range_in_nanoseconds(
        self, data_catalog_service, mock_catalog
    ):
        """Query bars passes correct start and end timestamps in nanoseconds."""
        # Arrange
        mock_bar = Mock()
        mock_catalog.bars.return_value = [mock_bar]

        start = datetime(2024, 1, 1, 9, 30, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, 16, 0, 0, tzinfo=timezone.utc)

        expected_start_ns = int(start.timestamp() * 1e9)
        expected_end_ns = int(end.timestamp() * 1e9)

        # Act
        data_catalog_service.query_bars(
            instrument_id="AAPL.NASDAQ",
            start=start,
            end=end,
            bar_type_spec="1-MINUTE-LAST",
        )

        # Assert
        call_kwargs = mock_catalog.bars.call_args[1]
        assert call_kwargs["start"] == expected_start_ns
        assert call_kwargs["end"] == expected_end_ns

    def test_query_bars_returns_list_of_bars(self, data_catalog_service, mock_catalog):
        """Query bars returns list of bar objects."""
        # Arrange
        mock_bars = [Mock(), Mock(), Mock()]
        mock_catalog.bars.return_value = mock_bars

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, tzinfo=timezone.utc)

        # Act
        result = data_catalog_service.query_bars(
            instrument_id="AAPL.NASDAQ",
            start=start,
            end=end,
            bar_type_spec="1-MINUTE-LAST",
        )

        # Assert
        assert result == mock_bars
        assert len(result) == 3

    def test_query_bars_does_not_use_bar_type_parameter(self, data_catalog_service, mock_catalog):
        """Query bars should NOT use 'bar_type' (singular) parameter.

        Using bar_type (singular) with BarType object was the old incorrect API.
        This caused Nautilus to return ALL bar types instead of filtering.
        """
        # Arrange
        mock_bar = Mock()
        mock_catalog.bars.return_value = [mock_bar]

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, tzinfo=timezone.utc)

        # Act
        data_catalog_service.query_bars(
            instrument_id="AAPL.NASDAQ",
            start=start,
            end=end,
            bar_type_spec="1-DAY-LAST",
        )

        # Assert
        call_kwargs = mock_catalog.bars.call_args[1]

        # bar_type (singular) should NOT be used
        assert "bar_type" not in call_kwargs, (
            "Should NOT use 'bar_type' (singular) parameter. "
            "This was the old incorrect API that caused mixed data."
        )
