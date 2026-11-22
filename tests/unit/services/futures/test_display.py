"""Text rows the CLI renderers share for futures runs (M4)."""

from decimal import Decimal

import pytest

from src.services.futures.display import (
    history_return_cell,
    signed_pct,
    summary_rows,
    symbol_cell,
    terms_line,
)
from src.services.futures.returns import RATIO_ADJUSTED_NOTE, summarise_run, terms_from_snapshot
from tests.unit.services.futures.test_returns import ES_SNAPSHOT, TRADE_A, TRADE_B

pytestmark = pytest.mark.unit


def test_signed_pct():
    assert signed_pct(Decimal("2.70272")) == "+2.70%"
    assert signed_pct(Decimal("-1.8345")) == "-1.83%"
    assert signed_pct(Decimal("0")) == "0.00%"
    assert signed_pct(None) == "N/A"


def test_summary_rows_lead_with_return_on_margin():
    rows = summary_rows(summarise_run(ES_SNAPSHOT, [TRADE_A, TRADE_B]))
    assert rows[0] == ("Return on margin (headline)", "+2.70%")
    assert ("Return on underlying", "+0.20%") in rows
    assert ("Peak margin used", "$55,000.00") in rows
    assert ("Max contracts", "2") in rows
    assert ("Net P&L (ratio-adjusted)", "$1,486.50") in rows
    assert ("Points per contract (adjusted)", "+10.00") in rows
    assert ("ES.CME", terms_line(terms_from_snapshot(ES_SNAPSHOT)["ES.CME"])) in rows


def test_terms_line():
    line = terms_line(terms_from_snapshot(ES_SNAPSHOT)["ES.CME"])
    assert line == (
        "x50 · initial margin $27,500.00 · maintenance $25,000.00 · "
        "$2.25/contract/side · as of 2026-11-21"
    )


def test_history_cells():
    summary = summarise_run(ES_SNAPSHOT, [TRADE_A, TRADE_B])
    assert history_return_cell(summary) == "RoM +2.70%"
    assert symbol_cell("ES", ES_SNAPSHOT) == "ES (fut)"
    assert symbol_cell("AAPL", {"config": {}}) == "AAPL"


def test_note_mentions_ratio_adjustment():
    assert "ratio-adjusted" in RATIO_ADJUSTED_NOTE


class TestDashboard:
    def _run(self, snapshot=ES_SNAPSHOT, trades=(TRADE_A, TRADE_B)):
        from types import SimpleNamespace

        return SimpleNamespace(config_snapshot=snapshot, trades=list(trades))

    def test_panel(self):
        from src.services.futures.display import dashboard_panel

        panel = dashboard_panel(self._run())
        assert panel["return_on_margin"] == "+2.70%"
        assert panel["return_on_margin_class"] == "text-green-400"
        assert panel["return_on_underlying"] == "+0.20%"
        assert ("Largest notional", "$500,000.00") in panel["rows"]
        assert [iid for iid, _ in panel["terms"]] == ["ES.CME"]
        assert dashboard_panel(self._run(snapshot={"config": {}})) is None

    def test_trade_cells_from_api_json(self):
        from src.services.futures.display import trade_cells

        row = {k: (str(v) if v is not None else None) for k, v in vars(TRADE_B).items()}
        (cell,) = trade_cells(ES_SNAPSHOT, [row])
        assert cell["contracts"] == "1"
        assert cell["points"] == "-10.00"
        assert cell["underlying"] == "-0.20%"
        assert cell["margin_return"] == "-1.83%"
        assert cell["margin_return_class"] == "text-red-400"
        assert trade_cells({"config": {}}, [row]) is None
        assert trade_cells(ES_SNAPSHOT, [{"instrument_id": "AAPL.NASDAQ"}]) == [{}]

    def test_list_return(self):
        from src.services.futures.display import list_return

        asset, rom = list_return(self._run())
        assert asset == "futures" and f"{rom:.2f}" == "2.70"
        assert list_return(self._run(snapshot={"config": {}})) == ("equity", None)
