"""Crypto M5 (task 5.2): the words and formats every crypto renderer shares."""

from decimal import Decimal
from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

from src.services.crypto import display
from tests.unit.services.crypto.test_returns import SNAPSHOT, TRADES

pytestmark = pytest.mark.unit


def _run(snapshot=SNAPSHOT, trades=TRADES):
    return SimpleNamespace(
        config_snapshot=snapshot, trades=trades, initial_capital=Decimal("100000")
    )


def _summary():
    return display.run_summary(_run())


class TestRows:
    def test_pair_label_names_venue_and_tier(self):
        terms = _summary().pairs["BTC/USD.KRAKEN"]
        assert display.pair_label(terms) == "BTC/USD (crypto, KRAKEN, fee tier 2)"

    def test_summary_rows(self):
        rows = dict(display.summary_rows(_summary()))
        assert rows["Closed-trade P&L (USD)"] == "-$113.59"
        assert rows["Largest position"] == "0.25 BTC"
        assert rows["Peak notional (USD)"] == "$20,000.00"
        assert rows["Peak exposure (notional / equity)"] == "19.96%"
        assert rows["Commission, maker (USD)"] == "$20.00"
        assert rows["Commission, taker (USD)"] == "$100.00"
        assert rows["Modelled spread (USD)"] == "$4.00"
        assert rows["Total costs (USD)"] == "$124.00"
        assert rows["Orders dropped under minimum"] == "3"
        assert rows["Annualised on"] == "365 days a year"
        assert rows["Account currency"] == "USD"
        line = rows["BTC/USD.KRAKEN"]
        assert line.startswith("BTC/USD (crypto, KRAKEN, fee tier 2)")
        assert "maker 0.30% / taker 0.60%" in line and "spread 2 bp" in line

    def test_an_unrecorded_drop_count_says_so(self):
        snapshot = {
            **SNAPSHOT,
            "crypto_pairs": {
                k: {kk: vv for kk, vv in v.items() if kk != "orders_below_minimum"}
                for k, v in SNAPSHOT["crypto_pairs"].items()
            },
        }
        rows = dict(display.summary_rows(display.run_summary(_run(snapshot))))
        assert rows["Orders dropped under minimum"] == "not recorded"

    def test_symbol_cell(self):
        assert display.symbol_cell("BTC", SNAPSHOT) == "BTC (crypto)"
        assert display.symbol_cell("AAPL", {}) is None


class TestCli:
    def test_print_crypto_prints_the_table_and_the_note(self):
        console = Console(file=StringIO(), width=200)
        assert display.print_crypto(console, _run()) is True
        text = console.file.getvalue()
        assert "BTC/USD (crypto, KRAKEN, fee tier 2)" in text
        assert "Spot, unlevered, long-only; Kraken Pro fee tier 2" in text

    def test_print_crypto_prints_nothing_for_another_run(self):
        console = Console(file=StringIO())
        assert display.print_crypto(console, _run(snapshot={})) is False
        assert console.file.getvalue() == ""

    def test_summary_text(self):
        text = display.summary_text(_summary())
        assert text.startswith(display.TITLE)
        assert "  Largest position: 0.25 BTC" in text
        assert "Note: Spot, unlevered, long-only" in text

    def test_flat_report_fields(self):
        flat = display.flat_report_fields(_summary())
        assert flat["asset_class"] == "crypto"
        assert flat["crypto_fee_tier"] == 2 and flat["crypto_venue"] == "KRAKEN"
        assert flat["crypto_commission_maker_usd"] == "20.00"
        assert flat["crypto_orders_below_minimum"] == 3
        assert flat["crypto_BTC/USD.KRAKEN_taker_pct"] == "0.60"
        assert flat["crypto_BTC/USD.KRAKEN_commission_taker_usd"] == "100.00"
        assert all(not isinstance(v, (dict, list)) for v in flat.values())


class TestDashboard:
    def test_panel(self):
        panel = display.dashboard_panel(_run())
        assert panel["net_pnl"] == "-$113.59" and panel["net_pnl_class"] == "text-red-400"
        assert panel["maker"] == "$20.00" and panel["taker"] == "$100.00"
        assert panel["spread"] == "$4.00"
        assert panel["pairs"] == [
            ("BTC/USD.KRAKEN", display.terms_line(_summary().pairs["BTC/USD.KRAKEN"]))
        ]
        labels = [label for label, _ in panel["rows"]]
        assert "Orders dropped under minimum" in labels
        assert "Commission, maker (USD)" not in labels  # in the costs box
        assert panel["note"].startswith("Spot, unlevered, long-only")

    def test_no_panel_for_another_run(self):
        assert display.dashboard_panel(_run(snapshot={})) is None

    def test_trade_cells_keep_coin_precision(self):
        cells = display.trade_cells(SNAPSHOT, TRADES)
        assert cells[0]["quantity"] == "0.12820512"
        assert cells[1]["quantity"] == "0.25"
        assert cells[0]["notional"] == "$10,000.00"
        assert cells[0]["underlying"] == "+2.56%"
        assert cells[1]["pnl"] == "-$310.00" and cells[1]["pnl_class"] == "text-red-400"
        assert cells[0]["entry"] == "78,000.0000"  # the pair's price precision

    def test_no_cells_for_another_run(self):
        assert display.trade_cells({}, TRADES) is None
