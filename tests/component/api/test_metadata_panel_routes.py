"""Component tests for the ETF metadata panel UI route (Story 4-4)."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_instrument_metadata_repository
from src.api.web import app
from src.db.models.instrument_metadata import InstrumentMetadata as OrmInstrumentMetadata
from src.models.instrument_metadata import NA_SENTINEL, ResolutionStatus


def _make_orm(**overrides) -> OrmInstrumentMetadata:
    defaults = {
        "ticker": "SPY",
        "metadata_provider": "FMP",
        "venue": "ARCA",
        "currency": "USD",
        "asset_type": "ETF",
        "company_name": "SPDR S&P 500 ETF Trust",
        "sector": "Financial Services",
        "industry": "Asset Management",
        "country": "US",
        "ipo_date": date(1993, 1, 29),
        "resolution_status": ResolutionStatus.RESOLVED,
    }
    defaults.update(overrides)
    row = MagicMock(spec=OrmInstrumentMetadata)
    for k, v in defaults.items():
        setattr(row, k, v)
    return row


@pytest.fixture
def mock_metadata_repo():
    repo = AsyncMock()
    repo.get_by_ticker = AsyncMock(return_value=_make_orm())
    return repo


@pytest.fixture
def client(mock_metadata_repo):
    app.dependency_overrides[get_instrument_metadata_repository] = lambda: mock_metadata_repo
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_instrument_metadata_repository, None)


_URL = "/explorer/metadata-panel?ticker=SPY"


@pytest.mark.component
class TestMetadataPanelRoute:
    """Tests for GET /explorer/metadata-panel."""

    def test_returns_200(self, client):
        assert client.get(_URL).status_code == 200

    def test_requires_ticker(self, client):
        assert client.get("/explorer/metadata-panel").status_code == 422

    def test_is_fragment_not_full_page(self, client):
        text = client.get(_URL).text
        assert "<html" not in text.lower()
        assert "<body" not in text.lower()

    def test_no_metadata_panel_id_wrapper(self, client):
        """Fragment is content only — the container owns the id."""
        assert 'id="metadata-panel"' not in client.get(_URL).text

    def test_renders_resolved_values(self, client):
        text = client.get(_URL).text
        assert "ARCA" in text
        assert "Financial Services" in text
        assert "1993-01-29" in text

    def test_descriptive_gap_renders_na(self, client, mock_metadata_repo):
        mock_metadata_repo.get_by_ticker = AsyncMock(return_value=_make_orm(sector=None))
        assert NA_SENTINEL in client.get(_URL).text

    def test_unresolved_venue_shown_distinctly(self, client, mock_metadata_repo):
        """AC3: venue state is distinct from a descriptive N/A, never the sentinel."""
        mock_metadata_repo.get_by_ticker = AsyncMock(
            return_value=_make_orm(venue=None, resolution_status=ResolutionStatus.VENUE_UNRESOLVED)
        )
        text = client.get(_URL).text
        assert "Unresolved" in text
        # Every descriptive field is resolved in this fixture, so the N/A sentinel
        # must not appear at all — the venue is rendered as "Unresolved", never "N/A".
        assert NA_SENTINEL not in text
        # And the distinct amber pill styling is used for the unresolved venue.
        assert "text-amber-400" in text

    def test_missing_row_renders_empty_state(self, client, mock_metadata_repo):
        """AC4: no row → clear empty-state, not a 404."""
        mock_metadata_repo.get_by_ticker = AsyncMock(return_value=None)
        response = client.get(_URL)
        assert response.status_code == 200
        assert "No resolved metadata for this ticker yet" in response.text
