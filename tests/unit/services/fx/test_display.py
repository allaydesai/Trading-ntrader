"""Text rows the renderers share for FX runs (M5)."""

from decimal import Decimal
from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

from src.services.fx.display import (
    dashboard_panel,
    flat_report_fields,
    leverage,
    pips,
    print_fx,
    summary_rows,
    summary_text,
    symbol_cell,
    terms_line,
    trade_cells,
    units_lots,
)
from src.services.fx.returns import FINANCING_NOTE, summarise_run, terms_from_snapshot
from tests.unit.services.fx.test_returns import SNAPSHOT, TRADE_A, TRADE_B

pytestmark = pytest.mark.unit

CAPITAL = Decimal("1000000")


def _summary():
    return summarise_run(SNAPSHOT, [TRADE_A, TRADE_B], CAPITAL)


def _run(snapshot=SNAPSHOT):
    return SimpleNamespace(
        config_snapshot=snapshot, trades=[TRADE_A, TRADE_B], initial_capital=CAPITAL
    )


def test_formatters():
    assert pips(Decimal("50")) == "+50.0"
    assert pips(Decimal("-12.34")) == "-12.3"
    assert pips(None) == "N/A"
    assert leverage(Decimal("0.308")) == "0.31x"
    assert leverage(None) == "N/A"
    assert units_lots(Decimal("100000")) == "100,000 (1.00 lots)"


def test_summary_rows():
    rows = summary_rows(_summary())
    assert rows[0] == ("Net P&L (USD)", "-$180.90")
    assert ("Pips (sum of closed trades)", "+0.0") in rows
    assert ("Largest position", "200,000 (2.00 lots)") in rows
    assert ("Peak notional (USD)", "$308,000.00") in rows
    assert ("Peak notional leverage", "0.31x") in rows
    assert ("Commission (USD)", "$12.00") in rows
    assert ("Modelled spread (USD)", "$7.10") in rows
    assert ("Total costs (USD)", "$19.10") in rows
    terms = terms_from_snapshot(SNAPSHOT)["USD/JPY.IDEALPRO"]
    assert rows[-1] == ("USD/JPY.IDEALPRO", terms_line(terms))


def test_terms_line():
    line = terms_line(terms_from_snapshot(SNAPSHOT)["USD/JPY.IDEALPRO"])
    assert line == (
        "USD/JPY · pip 0.01 · margin 3.0% · spread 0.5 pips · 0.20 bp (min $2.00/order) · "
        "charged commission $4.00, spread $4.60 · as of 2026-11-22 (UNVERIFIED)"
    )


def test_symbol_cell():
    assert symbol_cell("USDJPY", SNAPSHOT) == "USDJPY (fx)"
    assert symbol_cell("AAPL", {"config": {}}) is None


def test_text_and_table_carry_the_financing_note():
    assert summary_text(_summary()).endswith(f"Note: {FINANCING_NOTE}")
    out = StringIO()
    assert print_fx(Console(file=out, width=200), _run()) is True
    assert "Peak notional leverage" in out.getvalue()
    assert print_fx(Console(file=StringIO()), _run({"config": {}})) is False


def test_flat_report_fields():
    flat = flat_report_fields(_summary())
    assert flat["asset_class"] == "fx"
    assert flat["fx_commission_usd"] == "12.00"
    assert flat["fx_USD/JPY.IDEALPRO_commission_usd"] == "4.00"
    assert flat["fx_USD/JPY.IDEALPRO_spread_usd"] == "4.60"
    assert all(not isinstance(v, (dict, list)) for v in flat.values())


def test_dashboard_panel_and_cells():
    panel = dashboard_panel(_run())
    assert panel["net_pnl"] == "-$180.90" and panel["net_pnl_class"] == "text-red-400"
    assert panel["leverage"] == "0.31x"
    assert panel["commission"] == "$12.00" and panel["spread"] == "$7.10"
    assert [label for label, _ in panel["terms"]] == ["EUR/USD.IDEALPRO", "USD/JPY.IDEALPRO"]
    assert panel["note"] == FINANCING_NOTE
    assert dashboard_panel(_run({"config": {}})) is None
    cells = trade_cells(SNAPSHOT, [TRADE_A, TRADE_B])
    assert cells[0]["units"] == "100,000" and cells[0]["lots"] == "1.00"
    assert cells[0]["notional"] == "$108,000.00" and cells[0]["pips"] == "+50.0"
    assert cells[1]["pips_class"] == "text-red-400" and cells[1]["pnl"] == "-$672.40"
    assert trade_cells({"config": {}}, [TRADE_A]) is None
