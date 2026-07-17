"""Unit tests for the explorer accuracy-verification primitives (Story 4.6).

Pure functions over stub bars / metadata panels — no routes, DB, or Nautilus
engine. They pin the two accuracy contracts the explorer must honor:

- ``verify_candle_fidelity`` — the chart transform (``bar.<f>.as_double()`` →
  ``Candle``, ``ts_event / 1e9`` → ``time``) is an *exact* identity (AC1).
- ``verify_metadata_na_contract`` — the three-state N/A rule holds: no blank
  ``display_*``, venue never the descriptive sentinel (AC2).
"""

from unittest.mock import MagicMock

import pytest

from src.api.explorer_verification import (
    MAX_FIDELITY_ISSUES,
    verify_candle_fidelity,
    verify_metadata_na_contract,
)
from src.api.models.explorer import Candle
from src.api.models.metadata_panel import VENUE_UNRESOLVED_LABEL, EtfMetadataPanel
from src.models.instrument_metadata import NA_SENTINEL


def _make_bar(ts_ns: int, o: float, h: float, lo: float, c: float, vol: float):
    """A mock Nautilus Bar whose Price/Quantity fields expose ``.as_double()``."""
    bar = MagicMock()
    bar.ts_event = ts_ns
    bar.open.as_double.return_value = o
    bar.high.as_double.return_value = h
    bar.low.as_double.return_value = lo
    bar.close.as_double.return_value = c
    bar.volume.as_double.return_value = vol
    return bar


def _candle_from_bar(bar) -> Candle:
    """Build the *faithful* Candle the chart route would emit for ``bar``."""
    return Candle(
        time=int(bar.ts_event / 1e9),
        open=bar.open.as_double(),
        high=bar.high.as_double(),
        low=bar.low.as_double(),
        close=bar.close.as_double(),
        volume=int(bar.volume.as_double()),
    )


# --- verify_candle_fidelity ------------------------------------------------


class TestVerifyCandleFidelity:
    @pytest.mark.unit
    def test_faithful_render_has_no_issues(self):
        bars = [
            _make_bar(1704067200_000_000_000, 473.257, 475.101, 472.809, 474.503, 45_000_001),
            _make_bar(1704153600_000_000_000, 474.503, 476.000, 473.000, 475.809, 42_000_000),
        ]
        candles = [_candle_from_bar(b) for b in bars]
        assert verify_candle_fidelity(bars, candles) == []

    @pytest.mark.unit
    def test_faithful_render_accepts_dict_candles(self):
        """The component test parses ``bars_json`` into dicts — accept those too."""
        bars = [_make_bar(1704067200_000_000_000, 1.0, 2.0, 0.5, 1.5, 10)]
        candles = [_candle_from_bar(bars[0]).model_dump()]
        assert verify_candle_fidelity(bars, candles) == []

    @pytest.mark.unit
    def test_length_mismatch_is_flagged_first(self):
        bars = [_make_bar(1704067200_000_000_000, 1.0, 2.0, 0.5, 1.5, 10)]
        issues = verify_candle_fidelity(bars, [])
        assert len(issues) == 1
        assert "count" in issues[0].lower()

    @pytest.mark.unit
    def test_rounded_close_is_flagged(self):
        bar = _make_bar(1704067200_000_000_000, 473.257, 475.101, 472.809, 474.503, 45_000_001)
        candle = _candle_from_bar(bar)
        candle = candle.model_copy(update={"close": round(candle.close, 1)})  # 474.5 drift
        issues = verify_candle_fidelity([bar], [candle])
        assert len(issues) == 1
        assert "close" in issues[0].lower()

    @pytest.mark.unit
    def test_wrong_volume_is_flagged(self):
        bar = _make_bar(1704067200_000_000_000, 1.0, 2.0, 0.5, 1.5, 45_000_001)
        candle = _candle_from_bar(bar).model_copy(update={"volume": 45_000_000})
        issues = verify_candle_fidelity([bar], [candle])
        assert any("volume" in i.lower() for i in issues)

    @pytest.mark.unit
    def test_off_scale_time_is_flagged(self):
        """A ns-vs-s timestamp-scaling bug must be caught."""
        bar = _make_bar(1704067200_000_000_000, 1.0, 2.0, 0.5, 1.5, 10)
        candle = _candle_from_bar(bar).model_copy(update={"time": bar.ts_event})  # forgot /1e9
        issues = verify_candle_fidelity([bar], [candle])
        assert any("time" in i.lower() for i in issues)

    @pytest.mark.unit
    def test_issue_list_is_capped(self):
        bars = [_make_bar(i, 1.0, 2.0, 0.5, 1.5, 10) for i in range(MAX_FIDELITY_ISSUES + 10)]
        # Every candle drifts on close.
        candles = [_candle_from_bar(b).model_copy(update={"close": 9.9}) for b in bars]
        issues = verify_candle_fidelity(bars, candles)
        assert len(issues) <= MAX_FIDELITY_ISSUES + 1  # +1 truncation note allowed


# --- verify_metadata_na_contract -------------------------------------------


def _panel(
    *,
    ticker: str = "QQQ",
    venue: str | None = "XNAS",
    currency: str | None = "USD",
    asset_type: str | None = "ETF",
    company_name: str | None = "Invesco QQQ Trust",
    sector: str | None = "Financial Services",
    industry: str | None = "Asset Management",
    country: str | None = "US",
) -> EtfMetadataPanel:
    return EtfMetadataPanel(
        ticker=ticker,
        venue=venue,
        currency=currency,
        asset_type=asset_type,
        company_name=company_name,
        sector=sector,
        industry=industry,
        country=country,
        ipo_date=None,
    )


class TestVerifyMetadataNaContract:
    @pytest.mark.unit
    def test_full_metadata_is_clean(self):
        assert verify_metadata_na_contract(_panel()) == []

    @pytest.mark.unit
    def test_sparse_metadata_is_clean_via_na_sentinel(self):
        """Missing descriptive fields collapse to a clean N/A — still valid."""
        panel = _panel(sector=None, industry=None, country=None, company_name="")
        issues = verify_metadata_na_contract(panel)
        assert issues == []
        # Sanity: the sparse fields really did render as the sentinel, not blank.
        assert panel.display_sector == NA_SENTINEL
        assert panel.display_company_name == NA_SENTINEL

    @pytest.mark.unit
    def test_unresolved_venue_is_clean_and_distinct(self):
        panel = _panel(venue=None)
        assert verify_metadata_na_contract(panel) == []
        assert panel.display_venue == VENUE_UNRESOLVED_LABEL
        assert panel.display_venue != NA_SENTINEL

    @pytest.mark.unit
    def test_blank_display_is_flagged(self):
        """A display_* that returns '' would be a blank-vs-N/A ambiguity."""
        # A stub whose display_company_name regressed to a blank string.
        bad = MagicMock(spec=EtfMetadataPanel)
        bad.display_company_name = ""
        bad.display_currency = "USD"
        bad.display_asset_type = "ETF"
        bad.display_sector = "X"
        bad.display_industry = "X"
        bad.display_country = "US"
        bad.display_ipo_date = NA_SENTINEL
        bad.display_venue = "XNAS"
        bad.venue_resolved = True
        issues = verify_metadata_na_contract(bad)
        assert any("blank" in i.lower() or "company" in i.lower() for i in issues)

    @pytest.mark.unit
    def test_venue_labeled_as_na_sentinel_is_flagged(self):
        bad = MagicMock(spec=EtfMetadataPanel)
        for name in (
            "display_company_name",
            "display_currency",
            "display_asset_type",
            "display_sector",
            "display_industry",
            "display_country",
            "display_ipo_date",
        ):
            setattr(bad, name, "X")
        bad.display_venue = NA_SENTINEL  # venue must NEVER be the descriptive sentinel
        bad.venue_resolved = True
        issues = verify_metadata_na_contract(bad)
        assert any("venue" in i.lower() for i in issues)

    @pytest.mark.unit
    def test_venue_resolved_disagreement_is_flagged(self):
        bad = MagicMock(spec=EtfMetadataPanel)
        for name in (
            "display_company_name",
            "display_currency",
            "display_asset_type",
            "display_sector",
            "display_industry",
            "display_country",
            "display_ipo_date",
        ):
            setattr(bad, name, "X")
        # Claims resolved but label is the unresolved sentinel — contradiction.
        bad.display_venue = VENUE_UNRESOLVED_LABEL
        bad.venue_resolved = True
        issues = verify_metadata_na_contract(bad)
        assert any("venue" in i.lower() for i in issues)
