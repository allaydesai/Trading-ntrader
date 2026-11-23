"""FX dashboard (M5, D12 ruling Allay 2026-11-22): detail panel, trades table, export, list.

- Equity pages stay byte for byte the goldens in ``tests/fixtures/equity_dashboard_golden/``
  (``test_futures_dashboard.py`` pins them). Futures pages stay byte for byte the goldens in
  ``tests/fixtures/futures_dashboard_golden/``, captured from the pre-M5 tree
  (``python -m tests.component.api.test_fx_dashboard --capture-golden``, run once before
  M5 touched a view). Don't recapture to make a failure pass: a futures diff is the bug.
- FX pages show units/lots, USD notional, pips, underlying %, USD P&L, peak leverage, the
  commission vs modelled spread split and the "financing not modelled" note; the account
  return stays the headline (D10).
"""

from __future__ import annotations

import csv
import io
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from tests.component.api import test_futures_dashboard as dash
from tests.unit.services.fx.test_returns import SNAPSHOT as FX_PAIRS

pytestmark = pytest.mark.component

FUTURES_GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "futures_dashboard_golden"

FX_TRADES = [
    dash._trade(
        1, "EUR/USD.IDEALPRO", "BUY", "100000", "1.08000", "1.08500", "491.50", "0.4630", 0, 5
    ),
    dash._trade(
        2, "USD/JPY.IDEALPRO", "SELL", "200000", "150.000", "150.500", "-672.40", "0.3333", 3, 8
    ),
]


def fx_run():
    snapshot = {
        "strategy_type": "sma_crossover",
        "config": {"portfolio_value": "1000000"},
        "bar_type": "1-HOUR-MID",
        **FX_PAIRS,
    }
    run = dash._run("EURUSD", snapshot, FX_TRADES)
    run.initial_capital = Decimal("1000000")
    run.data_source = "catalog:firstrate-fx"
    return run


class TestFuturesPagesAreUnchanged:
    @pytest.mark.parametrize("name", sorted(dash.PAGES))
    def test_futures_page_is_byte_for_byte_the_pre_m5_render(self, name):
        expected = (FUTURES_GOLDEN / name).read_bytes().decode("utf-8")
        assert dash.render(dash.futures_run())[name] == expected


class TestFxDetail:
    def test_panel_shows_the_fx_figures_after_the_account_return(self):
        text = dash._text(dash.render(fx_run())["detail.html"])
        assert "Spot FX" in text
        assert "Net P&L (USD) -$180.90" in text
        assert "Peak notional leverage 0.31x" in text
        assert "Commission $12.00" in text and "Modelled spread $7.10" in text
        assert "Largest position 200,000 (2.00 lots)" in text
        assert "USD/JPY · pip 0.01" in text
        assert "Financing (overnight rollover/swap) is not modelled" in text
        assert "Return on margin (headline)" not in text
        assert text.index("Spot FX") > text.index("Total Return")


class TestFxTradesTable:
    def test_units_lots_pips_and_usd(self):
        text = dash._text(dash.render(fx_run())["trades_table.html"])
        assert "Units" in text and "Lots" in text and "Quantity" not in text
        assert "100,000" in text and "1.00" in text and "2.00" in text
        assert "Notional $" in text and "$108,000.00" in text and "$200,000.00" in text
        assert "Pips" in text and "+50.0" in text and "-50.0" in text
        assert "+0.46%" in text and "-0.33%" in text
        assert "1.08000" in text and "150.500" in text  # pair precision, not 2 dp
        assert "-$672.40" in text
        assert "not modelled" in text


class TestFxExport:
    def test_csv_adds_fx_columns(self):
        rows = list(csv.DictReader(io.StringIO(dash.render(fx_run())["export.csv"])))
        assert rows[0]["units"] == "100000" and rows[0]["lots"] == "1.00"
        assert rows[0]["notional_usd"] == "108000.00"
        assert rows[1]["pips"] == "-50.0"
        assert rows[1]["pnl_usd"] == "-672.40"

    def test_json_adds_fx_fields(self):
        rows = json.loads(dash.render(fx_run())["export.json"])
        assert rows[0]["pips"] == "50.0"
        assert rows[1]["return_on_underlying_pct"] == "-0.3333"

    def test_an_fx_run_without_trades_keeps_the_fx_header(self):
        run = fx_run()
        run.trades = []
        header = dash.render(run)["export.csv"].splitlines()[0]
        assert header.endswith("pips,return_on_underlying_pct,pnl_usd")


class TestFxList:
    def test_list_row_shows_the_fx_badge_and_the_account_return(self):
        text = dash._text(dash.render(fx_run())["list_fragment.html"])
        assert "EURUSD FX" in text
        assert "RoM" not in text
        assert "+1.49%" in text


def _capture_futures_golden() -> None:
    """Record the futures pages as the pre-M5 tree serves them (run once, before M5's edits)."""
    FUTURES_GOLDEN.mkdir(parents=True, exist_ok=True)
    for name, html in dash.render(dash.futures_run()).items():
        (FUTURES_GOLDEN / name).write_bytes(html.encode("utf-8"))
        print(f"wrote {FUTURES_GOLDEN / name} ({len(html)} chars)")


if __name__ == "__main__":
    if "--capture-golden" in sys.argv:
        _capture_futures_golden()
