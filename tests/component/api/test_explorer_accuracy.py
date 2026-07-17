"""Component accuracy verification for the explorer (Story 4.6).

Drives the *real* ``/explorer/chart-panel`` and ``/explorer/metadata-panel``
routes with dependency-override mocks and runs the Story-4.6 verification
primitives over their output — so the accuracy ACs are checked end-to-end
through the actual render path, not a reimplementation:

- **AC1** render fidelity: the chart-panel's embedded ``barsJson`` reproduces
  the source catalog bars exactly (awkward decimals, no rounding drift,
  ``time == ts_event / 1e9``).
- **AC3** 30min: ``tf=30m`` renders the 30m button as active alongside the
  other four timeframes, and its bars pass fidelity.
- **AC2** metadata: full rows show real name/venue/sector, sparse rows show a
  muted ``N/A`` (never blank), and an unresolved venue is a distinct amber
  ``Unresolved`` pill (never the descriptive sentinel); a DB fault degrades to
  the empty-state, never a 500.
"""

import json
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import (
    get_catalog_list,
    get_data_catalog_service,
    get_default_catalog,
    get_dividend_repository,
    get_instrument_metadata_repository,
    get_metadata_service,
    get_stock_split_repository,
)
from src.api.explorer_verification import (
    verify_candle_fidelity,
    verify_metadata_na_contract,
)
from src.api.models.metadata_panel import EtfMetadataPanel
from src.api.web import app
from src.db.models.catalog_instrument import CatalogInstrument
from src.db.models.instrument_metadata import InstrumentMetadata as OrmInstrumentMetadata
from src.models.instrument_metadata import NA_SENTINEL, ResolutionStatus

# Awkward decimals / a large odd volume so a naive round() or ns-vs-s bug fails.
_SOURCE_ROWS = [
    (1704067200_000_000_000, 473.257, 475.101, 472.809, 474.503, 45_000_001),
    (1704153600_000_000_000, 474.503, 476.019, 473.001, 475.809, 42_000_000),
]


def _make_bar(ts_ns: int, o: float, h: float, lo: float, c: float, vol: float):
    bar = MagicMock()
    bar.ts_event = ts_ns
    bar.open.as_double.return_value = o
    bar.high.as_double.return_value = h
    bar.low.as_double.return_value = lo
    bar.close.as_double.return_value = c
    bar.volume.as_double.return_value = vol
    return bar


def _source_bars():
    return [_make_bar(*row) for row in _SOURCE_ROWS]


def _make_instrument(**overrides) -> CatalogInstrument:
    defaults = {
        "id": 1,
        "ticker": "QQQ",
        "nautilus_id": "QQQ.XNAS",
        "asset_class": "ETF",
        "catalog_name": "us_etfs",
        "exchange": "XNAS",
        "name": "Invesco QQQ Trust",
        "bar_count_daily": 1250,
        "bar_count_hourly": 8750,
        "bar_count_30min": 15625,
        "bar_count_5min": 93750,
        "bar_count_minute": 468750,
        "date_range_start": datetime(2020, 1, 2, tzinfo=timezone.utc),
        "date_range_end": datetime(2025, 12, 31, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    inst = MagicMock(spec=CatalogInstrument)
    for k, v in defaults.items():
        setattr(inst, k, v)
    return inst


def _make_orm_metadata(**overrides) -> OrmInstrumentMetadata:
    defaults = {
        "ticker": "QQQ",
        "metadata_provider": "FMP",
        "venue": "XNAS",
        "currency": "USD",
        "asset_type": "ETF",
        "company_name": "Invesco QQQ Trust",
        "sector": "Financial Services",
        "industry": "Asset Management",
        "country": "US",
        "ipo_date": date(1999, 3, 10),
        "resolution_status": ResolutionStatus.RESOLVED,
    }
    defaults.update(overrides)
    row = MagicMock(spec=OrmInstrumentMetadata)
    for k, v in defaults.items():
        setattr(row, k, v)
    return row


def _parse_bars_json(html: str) -> list:
    """Extract the ``var barsJson = [...];`` array from the chart panel HTML."""
    assert "var barsJson = " in html, "barsJson not embedded in chart panel"
    payload = html.split("var barsJson = ", 1)[1].split(";", 1)[0]
    return json.loads(payload)


def _toolbar_button_tag(html: str, label: str) -> str:
    """Opening ``<button ...>`` tag for the toolbar button labelled ``label``."""
    marker = f'aria-label="{label} timeframe"'
    idx = html.find(marker)
    assert idx != -1, f"toolbar button for {label!r} not found"
    tag_open = html.rfind("<button", 0, idx)
    tag_close = html.find(">", idx)
    assert tag_open != -1 and tag_close != -1
    return html[tag_open : tag_close + 1]


@pytest.fixture
def mock_metadata_service():
    service = AsyncMock()
    service.get_instrument = AsyncMock(return_value=_make_instrument())
    service.list_instruments_with_search = AsyncMock(return_value=([], 0))
    service.count_asset_classes = AsyncMock(return_value={})
    return service


@pytest.fixture
def mock_catalog_service():
    service = MagicMock()
    service.query_bars.return_value = _source_bars()
    return service


@pytest.fixture
def mock_dividend_repo():
    repo = AsyncMock()
    repo.list_by_ticker = AsyncMock(return_value=[])
    repo.has_for_ticker = AsyncMock(return_value=False)
    return repo


@pytest.fixture
def mock_split_repo():
    repo = AsyncMock()
    repo.list_by_ticker = AsyncMock(return_value=[])
    repo.has_for_ticker = AsyncMock(return_value=False)
    return repo


@pytest.fixture
def mock_instrument_metadata_repo():
    repo = AsyncMock()
    repo.get_by_ticker = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def client(
    mock_metadata_service,
    mock_catalog_service,
    mock_dividend_repo,
    mock_split_repo,
    mock_instrument_metadata_repo,
):
    app.dependency_overrides[get_metadata_service] = lambda: mock_metadata_service
    app.dependency_overrides[get_data_catalog_service] = lambda: mock_catalog_service
    app.dependency_overrides[get_catalog_list] = lambda: ["us_etfs"]
    app.dependency_overrides[get_default_catalog] = lambda: "us_etfs"
    app.dependency_overrides[get_dividend_repository] = lambda: mock_dividend_repo
    app.dependency_overrides[get_stock_split_repository] = lambda: mock_split_repo
    app.dependency_overrides[get_instrument_metadata_repository] = (
        lambda: mock_instrument_metadata_repo
    )
    try:
        yield TestClient(app)
    finally:
        for dep in (
            get_metadata_service,
            get_data_catalog_service,
            get_catalog_list,
            get_default_catalog,
            get_dividend_repository,
            get_stock_split_repository,
            get_instrument_metadata_repository,
        ):
            app.dependency_overrides.pop(dep, None)


# --- AC1: render fidelity (catalog ↔ explorer) -----------------------------


@pytest.mark.component
class TestChartRenderFidelity:
    def test_daily_bars_render_faithfully(self, client):
        """AC1: chart-panel bars reproduce the source catalog bars exactly."""
        resp = client.get("/explorer/chart-panel?catalog=us_etfs&ticker=QQQ&tf=D")
        assert resp.status_code == 200
        candles = _parse_bars_json(resp.text)
        assert len(candles) == len(_SOURCE_ROWS)
        assert verify_candle_fidelity(_source_bars(), candles) == []

    def test_awkward_decimals_are_not_rounded(self, client):
        """The rendered close keeps full source precision (no 474.5 rounding)."""
        resp = client.get("/explorer/chart-panel?catalog=us_etfs&ticker=QQQ&tf=D")
        candles = _parse_bars_json(resp.text)
        assert candles[0]["close"] == 474.503
        assert candles[0]["volume"] == 45_000_001

    def test_timestamps_are_epoch_seconds(self, client):
        """time == int(ts_event / 1e9), not raw nanoseconds."""
        resp = client.get("/explorer/chart-panel?catalog=us_etfs&ticker=QQQ&tf=D")
        candles = _parse_bars_json(resp.text)
        assert candles[0]["time"] == 1704067200


# --- AC3: 30min renders alongside the other four ---------------------------


@pytest.mark.component
class TestThirtyMinuteRendering:
    def test_all_five_timeframe_buttons_present(self, client):
        resp = client.get("/explorer/chart-panel?catalog=us_etfs&ticker=QQQ&tf=30m")
        assert resp.status_code == 200
        for label in ["D", "1H", "30m", "5m", "1m"]:
            _toolbar_button_tag(resp.text, label)  # raises if missing

    def test_30m_button_is_active_and_enabled(self, client):
        resp = client.get("/explorer/chart-panel?catalog=us_etfs&ticker=QQQ&tf=30m")
        tag = _toolbar_button_tag(resp.text, "30m")
        assert 'aria-pressed="true"' in tag
        # bar_count_30min > 0 in the fixture → the button is not disabled.
        assert "disabled" not in tag

    def test_30m_bars_render_faithfully(self, client, mock_catalog_service):
        resp = client.get("/explorer/chart-panel?catalog=us_etfs&ticker=QQQ&tf=30m")
        # The route queried the 30-MINUTE bar type for this ticker.
        assert mock_catalog_service.query_bars.call_args.kwargs["bar_type_spec"] == "30-MINUTE-LAST"
        candles = _parse_bars_json(resp.text)
        assert verify_candle_fidelity(_source_bars(), candles) == []


# --- AC2: metadata N/A rendering (full / sparse / unresolved venue) ---------


@pytest.mark.component
class TestMetadataAccuracy:
    _URL = "/explorer/metadata-panel?ticker=QQQ"

    def test_full_metadata_shows_real_values_and_holds_contract(
        self, client, mock_instrument_metadata_repo
    ):
        row = _make_orm_metadata()
        mock_instrument_metadata_repo.get_by_ticker = AsyncMock(return_value=row)
        text = client.get(self._URL).text
        assert "Invesco QQQ Trust" in text
        assert "XNAS" in text
        assert "Financial Services" in text
        assert NA_SENTINEL not in text  # nothing missing → no sentinel at all
        assert verify_metadata_na_contract(EtfMetadataPanel.from_orm_row(row)) == []

    def test_sparse_metadata_renders_muted_na_not_blank(
        self, client, mock_instrument_metadata_repo
    ):
        row = _make_orm_metadata(sector=None, industry=None, country=None)
        mock_instrument_metadata_repo.get_by_ticker = AsyncMock(return_value=row)
        text = client.get(self._URL).text
        assert NA_SENTINEL in text
        assert "text-slate-500" in text  # muted styling for the honest gap
        assert "<dd></dd>" not in text  # never a blank cell
        # Contract still holds: sparse fields collapse to a clean N/A.
        assert verify_metadata_na_contract(EtfMetadataPanel.from_orm_row(row)) == []

    def test_unresolved_venue_is_distinct_pill_never_sentinel(
        self, client, mock_instrument_metadata_repo
    ):
        row = _make_orm_metadata(venue=None, resolution_status=ResolutionStatus.VENUE_UNRESOLVED)
        mock_instrument_metadata_repo.get_by_ticker = AsyncMock(return_value=row)
        text = client.get(self._URL).text
        assert "Unresolved" in text
        assert "text-amber-400" in text  # distinct amber pill
        assert NA_SENTINEL not in text  # descriptive fields all present → no N/A
        panel = EtfMetadataPanel.from_orm_row(row)
        assert verify_metadata_na_contract(panel) == []
        assert panel.display_venue != NA_SENTINEL

    def test_db_fault_degrades_to_empty_state_not_500(self, client, mock_instrument_metadata_repo):
        from sqlalchemy.exc import SQLAlchemyError

        mock_instrument_metadata_repo.get_by_ticker = AsyncMock(
            side_effect=SQLAlchemyError("table missing")
        )
        resp = client.get(self._URL)
        assert resp.status_code == 200
        assert "No resolved metadata for this ticker yet" in resp.text
