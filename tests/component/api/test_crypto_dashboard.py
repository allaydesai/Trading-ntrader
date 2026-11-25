"""Crypto dashboard (crypto M5, protected-file sign-off Allay 2026-11-24): panel, trades, list.

- Equity pages stay byte for byte ``tests/fixtures/equity_dashboard_golden/``
  (``test_futures_dashboard.py``), futures pages ``futures_dashboard_golden/``
  (``test_fx_dashboard.py``) and FX pages ``fx_dashboard_golden/``, captured here from the
  pre-M5 tree (``python -m tests.component.api.test_crypto_dashboard --capture-golden``, run
  once before crypto M5 touched a view). Don't recapture to make a failure pass: an FX diff is
  the bug.
- Crypto pages show the venue, fee tier, maker/taker commission split, modelled spread,
  fractional coin quantities at full precision, USD notional, the underlying return, USD P&L,
  the 365-day annualisation and the orders dropped under the minimum; the account return on
  USD equity stays the headline (decision C19).
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
from tests.component.api.test_fx_dashboard import fx_run
from tests.unit.services.crypto.test_returns import SNAPSHOT as CRYPTO_PAIRS

pytestmark = pytest.mark.component

FX_GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "fx_dashboard_golden"


class TestFxPagesAreUnchanged:
    @pytest.mark.parametrize("name", sorted(dash.PAGES))
    def test_fx_page_is_byte_for_byte_the_pre_crypto_m5_render(self, name):
        expected = (FX_GOLDEN / name).read_bytes().decode("utf-8")
        assert dash.render(fx_run())[name] == expected


CRYPTO_TRADES = [
    dash._trade(
        1,
        "BTC/USD.KRAKEN",
        "BUY",
        "0.12820512",
        "78000.0000",
        "80000.0000",
        "196.41",
        "2.5641",
        0,
        5,
    ),
    dash._trade(
        2, "BTC/USD.KRAKEN", "BUY", "0.25", "80000.0000", "79000.0000", "-310.00", "-1.2500", 6, 9
    ),
]


def crypto_run():
    snapshot = {
        "strategy_type": "sma_crossover",
        "config": {"portfolio_value": "100000"},
        "bar_type": "1-DAY-LAST",
        **CRYPTO_PAIRS,
    }
    run = dash._run("BTC", snapshot, CRYPTO_TRADES)
    run.initial_capital = Decimal("100000")
    run.data_source = "catalog:firstrate-crypto"
    return run


class TestCryptoDetail:
    def test_panel_shows_the_crypto_figures_after_the_account_return(self):
        text = dash._text(dash.render(crypto_run())["detail.html"])
        assert "Spot crypto" in text
        assert "BTC/USD (crypto, KRAKEN, fee tier 2)" in text
        assert "Closed-trade P&L (USD) -$113.59" in text
        assert "Maker $20.00" in text and "Taker $100.00" in text
        assert "Modelled spread $4.00" in text
        assert "Largest position 0.25 BTC" in text
        assert "Peak exposure (notional / equity) 19.96%" in text
        assert "Orders dropped under minimum 3" in text
        assert "Annualised on 365 days a year" in text
        assert "Spot, unlevered, long-only; Kraken Pro fee tier 2" in text
        assert text.index("Spot crypto") > text.index("Total Return")
        assert "Spot FX" not in text


class TestExactPeakExposure:
    """Follow-up to M5: a run with its fill series reports the exact peak, an older run the
    upper bound; the series itself is summarised, not dumped into the parameters list."""

    @staticmethod
    def _with_series():
        run = crypto_run()
        snapshot = json.loads(json.dumps(run.config_snapshot))
        snapshot["crypto_pairs"]["BTC/USD.KRAKEN"]["position_series"] = [
            [0, 1, "P1", "0.12820512", "78000.0000", "60.00"],
            [1, 2, "P1", "0.00000000", "80000.0000", "61.54"],
            [2, 3, "P2", "0.25000000", "80000.0000", "120.00"],
            [3, 4, "P2", "0.00000000", "79000.0000", "118.50"],
        ]
        run.config_snapshot = snapshot
        return run

    def test_a_run_with_the_series_shows_the_exact_note_and_a_fill_count(self):
        text = dash._text(dash.render(self._with_series())["detail.html"])
        assert "Peak notional and exposure are exact" in text
        assert "an upper bound" not in text
        assert "4 fills (exact peak exposure)" in text
        assert "78000.0000" not in text.split("Strategy-Specific Parameters")[1]

    def test_a_run_saved_before_the_series_keeps_the_upper_bound_note(self):
        text = dash._text(dash.render(crypto_run())["detail.html"])
        assert "an upper bound when a position scales in or round-trips" in text
        assert "exact peak exposure" not in text


class TestCryptoTradesTable:
    def test_coin_quantities_at_full_precision_and_usd(self):
        text = dash._text(dash.render(crypto_run())["trades_table.html"])
        assert "Quantity (coin)" in text and "Lots" not in text
        assert "0.12820512" in text and "0.25" in text
        assert "0.13" not in text  # never rounded to 2 decimals
        assert "Notional $" in text and "$10,000.00" in text and "$20,000.00" in text
        assert "+2.56%" in text and "-1.25%" in text
        assert "78,000.0000" in text  # the pair's price precision
        assert "-$310.00" in text
        assert "Kraken Pro" in text


class TestCryptoExport:
    def test_csv_adds_crypto_columns(self):
        rows = list(csv.DictReader(io.StringIO(dash.render(crypto_run())["export.csv"])))
        assert rows[0]["coin_quantity"] == "0.12820512"
        assert rows[0]["notional_usd"] == "10000.00"
        assert rows[1]["return_on_underlying_pct"] == "-1.2500"
        assert rows[1]["pnl_usd"] == "-310.00"

    def test_json_adds_crypto_fields(self):
        rows = json.loads(dash.render(crypto_run())["export.json"])
        assert rows[1]["coin_quantity"] == "0.25"

    def test_a_crypto_run_without_trades_keeps_the_crypto_header(self):
        run = crypto_run()
        run.trades = []
        header = dash.render(run)["export.csv"].splitlines()[0]
        assert header.endswith("coin_quantity,notional_usd,return_on_underlying_pct,pnl_usd")


class TestCryptoList:
    def test_list_row_shows_the_crypto_badge_and_the_account_return(self):
        text = dash._text(dash.render(crypto_run())["list_fragment.html"])
        assert "BTC CRYPTO" in text
        assert "RoM" not in text
        assert "+1.49%" in text


def _capture_fx_golden() -> None:
    """Record the FX pages as the pre-crypto-M5 tree serves them (run once, before the edits)."""
    FX_GOLDEN.mkdir(parents=True, exist_ok=True)
    for name, html in dash.render(fx_run()).items():
        (FX_GOLDEN / name).write_bytes(html.encode("utf-8"))
        print(f"wrote {FX_GOLDEN / name} ({len(html)} chars)")


if __name__ == "__main__":
    if "--capture-golden" in sys.argv:
        _capture_fx_golden()
