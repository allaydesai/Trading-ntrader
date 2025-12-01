"""Unit tests for DataCatalogService."""

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.models.catalog_metadata import CatalogAvailability
from src.services.data_catalog import DataCatalogService
from src.services.exceptions import (
    CatalogError,
)


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


class TestDataCatalogServiceInit:
    """Test suite for DataCatalogService initialization."""

    @patch("src.services.data_catalog.ParquetDataCatalog")
    @patch.object(DataCatalogService, "_rebuild_availability_cache")
    def test_init_uses_default_catalog_path(self, mock_rebuild, mock_catalog_class):
        """Init uses default catalog path when none provided."""
        # Arrange
        mock_catalog = MagicMock()
        mock_catalog_class.return_value = mock_catalog

        # Act
        with patch.dict(os.environ, {}, clear=True):
            service = DataCatalogService()

        # Assert
        # Path("./data/catalog") normalizes to "data/catalog" when converted to string
        assert service.catalog_path == Path("./data/catalog")
        assert mock_catalog_class.call_count == 1
        call_args = mock_catalog_class.call_args[0][0]
        assert call_args in ("./data/catalog", "data/catalog")  # Accept both normalized forms
        mock_rebuild.assert_called_once()

    @patch("src.services.data_catalog.ParquetDataCatalog")
    @patch.object(DataCatalogService, "_rebuild_availability_cache")
    def test_init_uses_env_variable_catalog_path(self, mock_rebuild, mock_catalog_class):
        """Init uses NAUTILUS_PATH environment variable when set."""
        # Arrange
        mock_catalog = MagicMock()
        mock_catalog_class.return_value = mock_catalog
        env_path = "/custom/catalog/path"

        # Act
        with patch.dict(os.environ, {"NAUTILUS_PATH": env_path}):
            service = DataCatalogService()

        # Assert
        assert service.catalog_path == Path(env_path)
        mock_catalog_class.assert_called_once_with(env_path)

    @patch("src.services.data_catalog.ParquetDataCatalog")
    @patch.object(DataCatalogService, "_rebuild_availability_cache")
    def test_init_uses_explicit_catalog_path(self, mock_rebuild, mock_catalog_class):
        """Init uses explicit catalog path parameter."""
        # Arrange
        mock_catalog = MagicMock()
        mock_catalog_class.return_value = mock_catalog
        explicit_path = "/explicit/path"

        # Act
        service = DataCatalogService(catalog_path=explicit_path)

        # Assert
        assert service.catalog_path == Path(explicit_path)
        mock_catalog_class.assert_called_once_with(explicit_path)

    @patch("src.services.data_catalog.ParquetDataCatalog")
    @patch.object(DataCatalogService, "_rebuild_availability_cache")
    def test_init_stores_provided_ibkr_client(self, mock_rebuild, mock_catalog_class):
        """Init stores provided IBKR client."""
        # Arrange
        mock_catalog = MagicMock()
        mock_catalog_class.return_value = mock_catalog
        mock_ibkr_client = MagicMock()

        # Act
        service = DataCatalogService(ibkr_client=mock_ibkr_client)

        # Assert
        assert service._ibkr_client is mock_ibkr_client
        assert service._ibkr_client_initialized is True


class TestIBKRClientProperty:
    """Test suite for ibkr_client lazy initialization property."""

    @patch("src.services.data_catalog.ParquetDataCatalog")
    @patch("src.services.data_catalog.IBKRHistoricalClient")
    @patch.object(DataCatalogService, "_rebuild_availability_cache")
    def test_ibkr_client_lazy_initializes_with_env_vars(
        self, mock_rebuild, mock_ibkr_class, mock_catalog_class
    ):
        """IBKR client lazy initializes using environment variables."""
        # Arrange
        mock_catalog = MagicMock()
        mock_catalog_class.return_value = mock_catalog
        mock_ibkr_client = MagicMock()
        mock_ibkr_class.return_value = mock_ibkr_client

        env_vars = {
            "IBKR_HOST": "192.168.1.100",
            "IBKR_PORT": "4002",
            "IBKR_CLIENT_ID": "42",
        }

        # Act
        with patch.dict(os.environ, env_vars, clear=True):
            service = DataCatalogService()
            client = service.ibkr_client

        # Assert
        assert client is mock_ibkr_client
        mock_ibkr_class.assert_called_once_with(host="192.168.1.100", port=4002, client_id=42)

    @patch("src.services.data_catalog.ParquetDataCatalog")
    @patch("src.services.data_catalog.IBKRHistoricalClient")
    @patch.object(DataCatalogService, "_rebuild_availability_cache")
    def test_ibkr_client_uses_defaults_when_no_env_vars(
        self, mock_rebuild, mock_ibkr_class, mock_catalog_class
    ):
        """IBKR client uses default values when env vars not set."""
        # Arrange
        mock_catalog = MagicMock()
        mock_catalog_class.return_value = mock_catalog
        mock_ibkr_client = MagicMock()
        mock_ibkr_class.return_value = mock_ibkr_client

        # Act
        with patch.dict(os.environ, {}, clear=True):
            DataCatalogService()

        # Assert
        mock_ibkr_class.assert_called_once_with(host="127.0.0.1", port=7497, client_id=10)

    @patch("src.services.data_catalog.ParquetDataCatalog")
    @patch.object(DataCatalogService, "_rebuild_availability_cache")
    def test_ibkr_client_returns_provided_client_without_lazy_init(
        self, mock_rebuild, mock_catalog_class
    ):
        """IBKR client returns provided client without lazy initialization."""
        # Arrange
        mock_catalog = MagicMock()
        mock_catalog_class.return_value = mock_catalog
        mock_ibkr_client = MagicMock()

        # Act
        service = DataCatalogService(ibkr_client=mock_ibkr_client)
        client = service.ibkr_client

        # Assert
        assert client is mock_ibkr_client


class TestGetAvailability:
    """Test suite for get_availability method."""

    @pytest.fixture
    def mock_catalog(self):
        """Create mock ParquetDataCatalog."""
        return MagicMock()

    @pytest.fixture
    def data_catalog_service(self, mock_catalog, tmp_path):
        """Create DataCatalogService with mocked catalog."""
        with patch("src.services.data_catalog.ParquetDataCatalog", return_value=mock_catalog):
            service = DataCatalogService(catalog_path=tmp_path)
            service.catalog = mock_catalog
            return service

    def test_get_availability_returns_cached_availability(self, data_catalog_service):
        """Get availability returns cached availability when available."""
        # Arrange
        availability = CatalogAvailability(
            instrument_id="AAPL.NASDAQ",
            bar_type_spec="1-MINUTE-LAST",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            file_count=100,
            total_rows=10000,
            last_updated=datetime.now(),
        )
        data_catalog_service.availability_cache["AAPL.NASDAQ_1-MINUTE-LAST"] = availability

        # Act
        result = data_catalog_service.get_availability("AAPL.NASDAQ", "1-MINUTE-LAST")

        # Assert
        assert result is availability

    def test_get_availability_returns_none_when_not_cached(self, data_catalog_service):
        """Get availability returns None when data not in cache."""
        # Act
        result = data_catalog_service.get_availability("TSLA.NASDAQ", "1-HOUR-LAST")

        # Assert
        assert result is None


class TestLoadInstrument:
    """Test suite for load_instrument method."""

    @pytest.fixture
    def mock_catalog(self):
        """Create mock ParquetDataCatalog."""
        return MagicMock()

    @pytest.fixture
    def data_catalog_service(self, mock_catalog, tmp_path):
        """Create DataCatalogService with mocked catalog."""
        with patch("src.services.data_catalog.ParquetDataCatalog", return_value=mock_catalog):
            service = DataCatalogService(catalog_path=tmp_path)
            service.catalog = mock_catalog
            return service

    def test_load_instrument_returns_instrument_when_found(
        self, data_catalog_service, mock_catalog
    ):
        """Load instrument returns instrument when found in catalog."""
        # Arrange
        mock_instrument = Mock()
        mock_instrument.id = "AAPL.NASDAQ"
        mock_catalog.instruments.return_value = [mock_instrument]

        # Act
        result = data_catalog_service.load_instrument("AAPL.NASDAQ")

        # Assert
        assert result is mock_instrument

    def test_load_instrument_returns_none_when_not_found(self, data_catalog_service, mock_catalog):
        """Load instrument returns None when not found in catalog."""
        # Arrange
        mock_instrument = Mock()
        mock_instrument.id = "TSLA.NASDAQ"
        mock_catalog.instruments.return_value = [mock_instrument]

        # Act
        result = data_catalog_service.load_instrument("AAPL.NASDAQ")

        # Assert
        assert result is None

    def test_load_instrument_returns_none_on_exception(self, data_catalog_service, mock_catalog):
        """Load instrument returns None when exception occurs."""
        # Arrange
        mock_catalog.instruments.side_effect = Exception("Catalog error")

        # Act
        result = data_catalog_service.load_instrument("AAPL.NASDAQ")

        # Assert
        assert result is None


class TestWriteBars:
    """Test suite for write_bars method."""

    @pytest.fixture
    def mock_catalog(self):
        """Create mock ParquetDataCatalog."""
        return MagicMock()

    @pytest.fixture
    def data_catalog_service(self, mock_catalog, tmp_path):
        """Create DataCatalogService with mocked catalog."""
        with patch("src.services.data_catalog.ParquetDataCatalog", return_value=mock_catalog):
            service = DataCatalogService(catalog_path=tmp_path)
            service.catalog = mock_catalog
            return service

    def test_write_bars_writes_to_catalog_and_rebuilds_cache(
        self, data_catalog_service, mock_catalog
    ):
        """Write bars writes to catalog and rebuilds cache."""
        # Arrange
        mock_bar = Mock()
        mock_bar_type = Mock()
        mock_bar_type.instrument_id = "AAPL.NASDAQ"
        mock_bar.bar_type = mock_bar_type

        bars = [mock_bar]

        with patch.object(data_catalog_service, "_rebuild_availability_cache") as mock_rebuild:
            # Act
            data_catalog_service.write_bars(bars, correlation_id="test-123")

            # Assert
            mock_catalog.write_data.assert_called_once_with(bars, skip_disjoint_check=True)
            mock_rebuild.assert_called_once()

    def test_write_bars_does_nothing_with_empty_list(self, data_catalog_service, mock_catalog):
        """Write bars does nothing when called with empty list."""
        # Act
        data_catalog_service.write_bars([], correlation_id="test-123")

        # Assert
        mock_catalog.write_data.assert_not_called()

    def test_write_bars_raises_catalog_error_on_failure(self, data_catalog_service, mock_catalog):
        """Write bars raises CatalogError when write fails."""
        # Arrange
        mock_bar = Mock()
        mock_bar_type = Mock()
        mock_bar_type.instrument_id = "AAPL.NASDAQ"
        mock_bar.bar_type = mock_bar_type

        mock_catalog.write_data.side_effect = Exception("Write failed")

        # Act & Assert
        with pytest.raises(CatalogError) as exc_info:
            data_catalog_service.write_bars([mock_bar])

        assert "Write failed" in str(exc_info.value)


class TestIsIBKRAvailable:
    """Test suite for _is_ibkr_available method."""

    @pytest.fixture
    def mock_catalog(self):
        """Create mock ParquetDataCatalog."""
        return MagicMock()

    @pytest.fixture
    def data_catalog_service(self, mock_catalog, tmp_path):
        """Create DataCatalogService with mocked catalog."""
        with patch("src.services.data_catalog.ParquetDataCatalog", return_value=mock_catalog):
            service = DataCatalogService(catalog_path=tmp_path)
            service.catalog = mock_catalog
            return service

    @pytest.mark.asyncio
    async def test_is_ibkr_available_returns_true_when_connected(
        self, data_catalog_service, mock_catalog
    ):
        """Is IBKR available returns True when client is connected."""
        # Arrange
        mock_ibkr_client = MagicMock()
        mock_ibkr_client.is_connected = True
        data_catalog_service._ibkr_client = mock_ibkr_client
        data_catalog_service._ibkr_client_initialized = True

        # Act
        result = await data_catalog_service._is_ibkr_available()

        # Assert
        assert result is True

    @pytest.mark.asyncio
    async def test_is_ibkr_available_attempts_connection_when_not_connected(
        self, data_catalog_service, mock_catalog
    ):
        """Is IBKR available attempts connection when not connected."""
        # Arrange
        mock_ibkr_client = AsyncMock()
        mock_ibkr_client.is_connected = False
        mock_ibkr_client.connect = AsyncMock()
        data_catalog_service._ibkr_client = mock_ibkr_client
        data_catalog_service._ibkr_client_initialized = True

        # Act - First check: not connected, then connected after connect()
        # Simulate connection success
        async def mock_connect(timeout):
            mock_ibkr_client.is_connected = True

        mock_ibkr_client.connect.side_effect = mock_connect

        result = await data_catalog_service._is_ibkr_available()

        # Assert
        mock_ibkr_client.connect.assert_called_once_with(timeout=10)
        assert result is True

    @pytest.mark.asyncio
    async def test_is_ibkr_available_returns_false_on_connection_failure(
        self, data_catalog_service, mock_catalog
    ):
        """Is IBKR available returns False when connection fails."""
        # Arrange
        mock_ibkr_client = AsyncMock()
        mock_ibkr_client.is_connected = False
        mock_ibkr_client.connect = AsyncMock(side_effect=Exception("Connection failed"))
        data_catalog_service._ibkr_client = mock_ibkr_client
        data_catalog_service._ibkr_client_initialized = True

        # Act
        result = await data_catalog_service._is_ibkr_available()

        # Assert
        assert result is False


class TestScanCatalog:
    """Test suite for scan_catalog method."""

    @pytest.fixture
    def mock_catalog(self):
        """Create mock ParquetDataCatalog."""
        return MagicMock()

    @pytest.fixture
    def data_catalog_service(self, mock_catalog, tmp_path):
        """Create DataCatalogService with mocked catalog."""
        with patch("src.services.data_catalog.ParquetDataCatalog", return_value=mock_catalog):
            service = DataCatalogService(catalog_path=tmp_path)
            service.catalog = mock_catalog
            return service

    def test_scan_catalog_groups_by_instrument_id(self, data_catalog_service):
        """Scan catalog groups availabilities by instrument_id."""
        # Arrange
        avail1 = CatalogAvailability(
            instrument_id="AAPL.NASDAQ",
            bar_type_spec="1-MINUTE-LAST",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            file_count=100,
            total_rows=10000,
            last_updated=datetime.now(),
        )
        avail2 = CatalogAvailability(
            instrument_id="AAPL.NASDAQ",
            bar_type_spec="1-HOUR-LAST",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            file_count=50,
            total_rows=5000,
            last_updated=datetime.now(),
        )
        avail3 = CatalogAvailability(
            instrument_id="TSLA.NASDAQ",
            bar_type_spec="1-MINUTE-LAST",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 12, 31, tzinfo=timezone.utc),
            file_count=75,
            total_rows=7500,
            last_updated=datetime.now(),
        )

        data_catalog_service.availability_cache = {
            "AAPL.NASDAQ_1-MINUTE-LAST": avail1,
            "AAPL.NASDAQ_1-HOUR-LAST": avail2,
            "TSLA.NASDAQ_1-MINUTE-LAST": avail3,
        }

        # Act
        result = data_catalog_service.scan_catalog()

        # Assert
        assert len(result) == 2
        assert "AAPL.NASDAQ" in result
        assert "TSLA.NASDAQ" in result
        assert len(result["AAPL.NASDAQ"]) == 2
        assert len(result["TSLA.NASDAQ"]) == 1

    def test_scan_catalog_returns_empty_dict_when_cache_empty(self, data_catalog_service):
        """Scan catalog returns empty dict when cache is empty."""
        # Arrange
        data_catalog_service.availability_cache = {}

        # Act
        result = data_catalog_service.scan_catalog()

        # Assert
        assert result == {}


class TestDetectGaps:
    """Test suite for detect_gaps method."""

    @pytest.fixture
    def mock_catalog(self):
        """Create mock ParquetDataCatalog."""
        return MagicMock()

    @pytest.fixture
    def data_catalog_service(self, mock_catalog, tmp_path):
        """Create DataCatalogService with mocked catalog."""
        with patch("src.services.data_catalog.ParquetDataCatalog", return_value=mock_catalog):
            service = DataCatalogService(catalog_path=tmp_path)
            service.catalog = mock_catalog
            return service

    def test_detect_gaps_returns_full_range_when_no_data(self, data_catalog_service):
        """Detect gaps returns full range when no data available."""
        # Arrange
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 12, 31, tzinfo=timezone.utc)

        # Act
        gaps = data_catalog_service.detect_gaps("AAPL.NASDAQ", "1-MINUTE-LAST", start, end)

        # Assert
        assert len(gaps) == 1
        assert gaps[0]["start"] == start
        assert gaps[0]["end"] == end

    def test_detect_gaps_returns_gap_at_start(self, data_catalog_service):
        """Detect gaps returns gap at start when data starts later."""
        # Arrange
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 12, 31, tzinfo=timezone.utc)
        data_start = datetime(2024, 6, 1, tzinfo=timezone.utc)
        data_end = datetime(2024, 12, 31, tzinfo=timezone.utc)

        availability = CatalogAvailability(
            instrument_id="AAPL.NASDAQ",
            bar_type_spec="1-MINUTE-LAST",
            start_date=data_start,
            end_date=data_end,
            file_count=100,
            total_rows=10000,
            last_updated=datetime.now(),
        )
        data_catalog_service.availability_cache["AAPL.NASDAQ_1-MINUTE-LAST"] = availability

        # Act
        gaps = data_catalog_service.detect_gaps("AAPL.NASDAQ", "1-MINUTE-LAST", start, end)

        # Assert
        assert len(gaps) == 1
        assert gaps[0]["start"] == start
        assert gaps[0]["end"] == data_start

    def test_detect_gaps_returns_gap_at_end(self, data_catalog_service):
        """Detect gaps returns gap at end when data ends earlier."""
        # Arrange
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 12, 31, tzinfo=timezone.utc)
        data_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        data_end = datetime(2024, 6, 30, tzinfo=timezone.utc)

        availability = CatalogAvailability(
            instrument_id="AAPL.NASDAQ",
            bar_type_spec="1-MINUTE-LAST",
            start_date=data_start,
            end_date=data_end,
            file_count=100,
            total_rows=10000,
            last_updated=datetime.now(),
        )
        data_catalog_service.availability_cache["AAPL.NASDAQ_1-MINUTE-LAST"] = availability

        # Act
        gaps = data_catalog_service.detect_gaps("AAPL.NASDAQ", "1-MINUTE-LAST", start, end)

        # Assert
        assert len(gaps) == 1
        assert gaps[0]["start"] == data_end
        assert gaps[0]["end"] == end

    def test_detect_gaps_returns_no_gaps_when_data_fully_covers_range(self, data_catalog_service):
        """Detect gaps returns no gaps when data fully covers range."""
        # Arrange
        start = datetime(2024, 6, 1, tzinfo=timezone.utc)
        end = datetime(2024, 6, 30, tzinfo=timezone.utc)
        data_start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        data_end = datetime(2024, 12, 31, tzinfo=timezone.utc)

        availability = CatalogAvailability(
            instrument_id="AAPL.NASDAQ",
            bar_type_spec="1-MINUTE-LAST",
            start_date=data_start,
            end_date=data_end,
            file_count=100,
            total_rows=10000,
            last_updated=datetime.now(),
        )
        data_catalog_service.availability_cache["AAPL.NASDAQ_1-MINUTE-LAST"] = availability

        # Act
        gaps = data_catalog_service.detect_gaps("AAPL.NASDAQ", "1-MINUTE-LAST", start, end)

        # Assert
        assert len(gaps) == 0
