"""Unit tests for DataCatalogService bar type filtering."""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock

from src.services.data_catalog import DataCatalogService


class TestQueryBarsBarTypeFiltering:
    """Test suite for bar type filtering in query_bars."""

    @pytest.fixture
    def mock_catalog(self):
        """Create a mock ParquetDataCatalog."""
        catalog = MagicMock()
        return catalog

    @pytest.fixture
    def data_catalog_service(self, mock_catalog, tmp_path):
        """Create DataCatalogService with mocked catalog."""
        with patch(
            "src.services.data_catalog.ParquetDataCatalog", return_value=mock_catalog
        ):
            service = DataCatalogService(catalog_path=tmp_path)
            service.catalog = mock_catalog
            return service

    def test_query_bars_constructs_correct_bar_type_for_one_minute(
        self, data_catalog_service, mock_catalog
    ):
        """Query bars constructs correct BarType for 1-MINUTE-LAST."""
        # Arrange
        mock_bar = Mock()
        mock_catalog.bars.return_value = [mock_bar]

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, tzinfo=timezone.utc)

        # Act
        with patch("src.services.data_catalog.BarType") as mock_bar_type_cls:
            mock_bar_type = Mock()
            mock_bar_type_cls.from_str.return_value = mock_bar_type

            data_catalog_service.query_bars(
                instrument_id="AAPL.NASDAQ",
                start=start,
                end=end,
                bar_type_spec="1-MINUTE-LAST",
            )

        # Assert
        mock_bar_type_cls.from_str.assert_called_once_with(
            "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
        )
        mock_catalog.bars.assert_called_once()
        call_kwargs = mock_catalog.bars.call_args[1]
        assert call_kwargs["instrument_ids"] == ["AAPL.NASDAQ"]
        assert call_kwargs["bar_type"] == mock_bar_type

    def test_query_bars_constructs_correct_bar_type_for_one_day(
        self, data_catalog_service, mock_catalog
    ):
        """Query bars constructs correct BarType for 1-DAY-LAST."""
        # Arrange
        mock_bar = Mock()
        mock_catalog.bars.return_value = [mock_bar]

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 12, 31, tzinfo=timezone.utc)

        # Act
        with patch("src.services.data_catalog.BarType") as mock_bar_type_cls:
            mock_bar_type = Mock()
            mock_bar_type_cls.from_str.return_value = mock_bar_type

            data_catalog_service.query_bars(
                instrument_id="AAPL.NASDAQ",
                start=start,
                end=end,
                bar_type_spec="1-DAY-LAST",
            )

        # Assert
        mock_bar_type_cls.from_str.assert_called_once_with(
            "AAPL.NASDAQ-1-DAY-LAST-EXTERNAL"
        )
        call_kwargs = mock_catalog.bars.call_args[1]
        assert call_kwargs["bar_type"] == mock_bar_type

    def test_query_bars_constructs_correct_bar_type_for_five_minute(
        self, data_catalog_service, mock_catalog
    ):
        """Query bars constructs correct BarType for 5-MINUTE-LAST."""
        # Arrange
        mock_bar = Mock()
        mock_catalog.bars.return_value = [mock_bar]

        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, tzinfo=timezone.utc)

        # Act
        with patch("src.services.data_catalog.BarType") as mock_bar_type_cls:
            mock_bar_type = Mock()
            mock_bar_type_cls.from_str.return_value = mock_bar_type

            data_catalog_service.query_bars(
                instrument_id="TSLA.NASDAQ",
                start=start,
                end=end,
                bar_type_spec="5-MINUTE-LAST",
            )

        # Assert
        mock_bar_type_cls.from_str.assert_called_once_with(
            "TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"
        )

    def test_query_bars_passes_correct_time_range(
        self, data_catalog_service, mock_catalog
    ):
        """Query bars passes correct start and end timestamps."""
        # Arrange
        mock_bar = Mock()
        mock_catalog.bars.return_value = [mock_bar]

        start = datetime(2024, 1, 1, 9, 30, 0, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, 16, 0, 0, tzinfo=timezone.utc)

        expected_start_ns = int(start.timestamp() * 1e9)
        expected_end_ns = int(end.timestamp() * 1e9)

        # Act
        with patch("src.services.data_catalog.BarType") as mock_bar_type_cls:
            mock_bar_type_cls.from_str.return_value = Mock()
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
        with patch("src.services.data_catalog.BarType") as mock_bar_type_cls:
            mock_bar_type_cls.from_str.return_value = Mock()
            result = data_catalog_service.query_bars(
                instrument_id="AAPL.NASDAQ",
                start=start,
                end=end,
                bar_type_spec="1-MINUTE-LAST",
            )

        # Assert
        assert result == mock_bars
        assert len(result) == 3
