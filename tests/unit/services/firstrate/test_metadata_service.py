"""Unit tests for MetadataService."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db.models.catalog_instrument import CatalogInstrument
from src.services.firstrate.metadata_service import MetadataService


def _make_instrument(**overrides) -> CatalogInstrument:
    defaults = {
        "id": 1,
        "ticker": "SPY",
        "nautilus_id": "SPY.ARCA",
        "asset_class": "ETF",
        "catalog_name": "firstrate-etf",
        "exchange": "ARCA",
        "name": "SPDR S&P 500 ETF Trust",
    }
    defaults.update(overrides)
    return CatalogInstrument(**defaults)


@pytest.mark.unit
class TestMetadataServiceAsync:
    """Tests for async MetadataService methods."""

    async def test_get_instrument_delegates_to_repo(self):
        """get_instrument calls async repository get_by_ticker."""
        mock_repo = AsyncMock()
        instrument = _make_instrument()
        mock_repo.get_by_ticker.return_value = instrument

        service = MetadataService(async_repo=mock_repo)
        result = await service.get_instrument("firstrate-etf", "SPY")

        assert result is instrument
        mock_repo.get_by_ticker.assert_awaited_once_with("firstrate-etf", "SPY")

    async def test_list_instruments_delegates_to_repo(self):
        """list_instruments calls async repository list_by_catalog."""
        mock_repo = AsyncMock()
        instruments = [_make_instrument(), _make_instrument(ticker="QQQ", id=2)]
        mock_repo.list_by_catalog.return_value = instruments

        service = MetadataService(async_repo=mock_repo)
        result = await service.list_instruments("firstrate-etf")

        assert len(result) == 2
        mock_repo.list_by_catalog.assert_awaited_once_with(
            "firstrate-etf", asset_class=None, limit=100, offset=0
        )

    async def test_upsert_instrument_delegates_to_repo(self):
        """upsert_instrument calls async repository upsert."""
        mock_repo = AsyncMock()
        instrument = _make_instrument()
        mock_repo.upsert.return_value = instrument

        service = MetadataService(async_repo=mock_repo)
        result = await service.upsert_instrument(instrument)

        assert result is instrument
        mock_repo.upsert.assert_awaited_once_with(instrument)

    async def test_search_instruments_delegates_to_repo(self):
        """search_instruments calls async repository search."""
        mock_repo = AsyncMock()
        mock_repo.search.return_value = [_make_instrument()]

        service = MetadataService(async_repo=mock_repo)
        result = await service.search_instruments("SPY")

        assert len(result) == 1
        mock_repo.search.assert_awaited_once_with("SPY", limit=50)


@pytest.mark.unit
class TestMetadataServiceSync:
    """Tests for sync MetadataService methods."""

    def test_get_instrument_sync(self):
        """get_instrument_sync calls sync repository."""
        mock_repo = MagicMock()
        instrument = _make_instrument()
        mock_repo.get_by_ticker.return_value = instrument

        service = MetadataService(sync_repo=mock_repo)
        result = service.get_instrument_sync("firstrate-etf", "SPY")

        assert result is instrument
        mock_repo.get_by_ticker.assert_called_once_with("firstrate-etf", "SPY")

    def test_list_instruments_sync(self):
        """list_instruments_sync calls sync repository."""
        mock_repo = MagicMock()
        mock_repo.list_by_catalog.return_value = [_make_instrument()]

        service = MetadataService(sync_repo=mock_repo)
        result = service.list_instruments_sync("firstrate-etf")

        assert len(result) == 1

    def test_upsert_instrument_sync(self):
        """upsert_instrument_sync calls sync repository."""
        mock_repo = MagicMock()
        instrument = _make_instrument()
        mock_repo.upsert.return_value = instrument

        service = MetadataService(sync_repo=mock_repo)
        result = service.upsert_instrument_sync(instrument)

        assert result is instrument

    def test_search_instruments_sync(self):
        """search_instruments_sync calls sync repository."""
        mock_repo = MagicMock()
        mock_repo.search.return_value = []

        service = MetadataService(sync_repo=mock_repo)
        result = service.search_instruments_sync("XYZ")

        assert result == []
        mock_repo.search.assert_called_once_with("XYZ", limit=50)


@pytest.mark.unit
class TestMetadataServiceErrors:
    """Tests for error handling."""

    def test_sync_method_without_sync_repo_raises(self):
        """Sync methods raise RuntimeError if no sync_repo provided."""
        service = MetadataService(async_repo=AsyncMock())

        with pytest.raises(RuntimeError, match="sync repository"):
            service.get_instrument_sync("catalog", "ticker")

    async def test_async_method_without_async_repo_raises(self):
        """Async methods raise RuntimeError if no async_repo provided."""
        service = MetadataService(sync_repo=MagicMock())

        with pytest.raises(RuntimeError, match="async repository"):
            await service.get_instrument("catalog", "ticker")
