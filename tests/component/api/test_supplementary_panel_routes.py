"""Component tests for the supplementary panel UI route (Story 4-2)."""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import (
    get_dividend_repository,
    get_metadata_service,
    get_stock_split_repository,
)
from src.api.web import app
from src.db.models.catalog_dividend import CatalogDividend
from src.db.models.catalog_instrument import CatalogInstrument
from src.db.models.catalog_stock_split import CatalogStockSplit


def _make_instrument(**overrides) -> CatalogInstrument:
    defaults = {
        "id": 1,
        "ticker": "AAPL",
        "nautilus_id": "AAPL.NASDAQ",
        "asset_class": "STOCK",
        "catalog_name": "e2e-test",
        "exchange": "NASDAQ",
        "name": "Apple Inc.",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "ipo_date": date(1980, 12, 12),
        "country": "US",
        "state": "CA",
        "date_range_start": datetime(2020, 1, 2, tzinfo=timezone.utc),
        "date_range_end": datetime(2025, 12, 31, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    inst = MagicMock(spec=CatalogInstrument)
    for k, v in defaults.items():
        setattr(inst, k, v)
    return inst


def _div(ex_date: date, amount: str) -> CatalogDividend:
    return CatalogDividend(ticker="AAPL", ex_date=ex_date, amount=Decimal(amount))


def _split(effective_date: date, ratio: str) -> CatalogStockSplit:
    return CatalogStockSplit(ticker="AAPL", effective_date=effective_date, ratio=Decimal(ratio))


@pytest.fixture
def mock_metadata_service():
    service = AsyncMock()
    service.get_instrument = AsyncMock(return_value=_make_instrument())
    return service


@pytest.fixture
def mock_dividend_repo():
    repo = AsyncMock()
    repo.list_by_ticker = AsyncMock(
        return_value=[_div(date(2026, 2, 9), "0.26"), _div(date(2025, 11, 8), "0.25")]
    )
    return repo


@pytest.fixture
def mock_split_repo():
    repo = AsyncMock()
    repo.list_by_ticker = AsyncMock(
        return_value=[_split(date(2020, 8, 31), "4"), _split(date(2005, 2, 28), "2")]
    )
    return repo


@pytest.fixture
def client(mock_metadata_service, mock_dividend_repo, mock_split_repo):
    app.dependency_overrides[get_metadata_service] = lambda: mock_metadata_service
    app.dependency_overrides[get_dividend_repository] = lambda: mock_dividend_repo
    app.dependency_overrides[get_stock_split_repository] = lambda: mock_split_repo
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_metadata_service, None)
        app.dependency_overrides.pop(get_dividend_repository, None)
        app.dependency_overrides.pop(get_stock_split_repository, None)


_URL = "/explorer/supplementary?catalog=e2e-test&ticker=AAPL"


@pytest.mark.component
class TestSupplementaryPanelRoute:
    """Tests for GET /explorer/supplementary."""

    def test_returns_200(self, client):
        assert client.get(_URL).status_code == 200

    def test_requires_catalog_and_ticker(self, client):
        assert client.get("/explorer/supplementary").status_code == 422

    def test_is_fragment_not_full_page(self, client):
        text = client.get(_URL).text
        assert "<html" not in text.lower()
        assert "<body" not in text.lower()

    def test_no_supplementary_panel_id_wrapper(self, client):
        """Fragment is content only — the container owns the id."""
        assert 'id="supplementary-panel"' not in client.get(_URL).text

    def test_contains_three_section_headings(self, client):
        text = client.get(_URL).text
        assert "Company Profile" in text
        assert "Dividend History" in text
        assert "Stock Split History" in text

    def test_sections_collapsed_by_default(self, client):
        """All <details> render without the `open` attribute (AC #1)."""
        text = client.get(_URL).text
        assert "<details" in text
        assert "<details open" not in text
        assert "open>" not in text

    def test_company_profile_includes_country_and_state(self, client):
        text = client.get(_URL).text
        assert "Country" in text and "US" in text
        assert "State" in text and "CA" in text

    def test_renders_dividend_rows(self, client):
        text = client.get(_URL).text
        assert "2026-02-09" in text
        assert "0.26" in text

    def test_renders_split_display_strings(self, client):
        text = client.get(_URL).text
        assert "4:1" in text
        assert "2:1" in text

    def test_dividend_empty_state(self, client, mock_dividend_repo):
        mock_dividend_repo.list_by_ticker = AsyncMock(return_value=[])
        text = client.get(_URL).text
        assert "No dividend history available" in text

    def test_split_empty_state(self, client, mock_split_repo):
        mock_split_repo.list_by_ticker = AsyncMock(return_value=[])
        text = client.get(_URL).text
        assert "No split history available" in text

    def test_company_profile_shows_when_divs_and_splits_empty(
        self, client, mock_dividend_repo, mock_split_repo
    ):
        """AC #5: Company Profile still displays even when div/split empty."""
        mock_dividend_repo.list_by_ticker = AsyncMock(return_value=[])
        mock_split_repo.list_by_ticker = AsyncMock(return_value=[])
        text = client.get(_URL).text
        assert "Apple Inc." in text
        assert "No dividend history available" in text
        assert "No split history available" in text

    def test_missing_profile_renders_gracefully(self, client, mock_metadata_service):
        """No 404 when the instrument is missing — additive panel (AC #5)."""
        mock_metadata_service.get_instrument = AsyncMock(return_value=None)
        response = client.get(_URL)
        assert response.status_code == 200
        assert "No company profile available" in response.text
