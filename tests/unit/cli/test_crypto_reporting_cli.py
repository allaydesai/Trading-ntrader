"""CLI renderers for spot crypto runs (crypto M5): ``show``, ``report``, ``history``, ``compare``.

A crypto run gets its block (``BTC/USD (crypto, KRAKEN, fee tier 2)``, coin quantities at full
precision, USD notional, maker/taker commission and modelled spread, the orders dropped under
the minimum, 365-day annualisation, "spot, unlevered, long-only") next to the account return,
which stays the headline; equity, futures and FX runs render as before
(``test_futures_reporting_cli.py`` and ``test_fx_reporting_cli.py`` pin those).
"""

import csv
import json
from decimal import Decimal

import pytest

from src.cli.commands import report, show
from tests.unit.cli import test_futures_reporting_cli as futures_cli
from tests.unit.cli.test_futures_reporting_cli import EQUITY_SNAPSHOT, _captured
from tests.unit.cli.test_futures_reporting_cli import _run as _base_run
from tests.unit.services.crypto.test_returns import SNAPSHOT, _trade

pytestmark = pytest.mark.unit

TRADES = (
    _trade("0.12820512", "78000", "80000", "196.41", 0, 5),
    _trade("0.25", "80000", "79000", "-310.00", 6, 9),
)
for n, trade in enumerate(TRADES, 1):
    trade["trade_id"] = f"T-{n}"


class _Trade(dict):
    """A dict that reads like an ORM trade (report's JSON path uses attributes)."""

    __getattr__ = dict.get


def _run(snapshot=SNAPSHOT, symbol="BTC"):
    run = _base_run(snapshot, symbol=symbol, trades=[_Trade(t) for t in TRADES])
    run.initial_capital = Decimal("100000")
    return run


class TestShow:
    def test_crypto_run_shows_its_block_before_the_config(self):
        with _captured(show) as out:
            show._display_backtest(_run())
        text = out.getvalue()
        assert "Crypto (account return on USD equity is the headline)" in text
        assert "BTC/USD (crypto, KRAKEN, fee tier 2)" in text
        assert "0.25 BTC" in text
        assert "Commission, maker (USD)" in text and "$20.00" in text
        assert "Commission, taker (USD)" in text and "$100.00" in text
        assert "Modelled spread (USD)" in text and "$4.00" in text
        assert "Orders dropped under minimum" in text
        assert "365 days a year" in text
        assert "Spot, unlevered, long-only" in text
        assert text.index("Peak exposure") < text.index("Configuration Snapshot")

    def test_equity_run_has_no_crypto_block(self):
        with _captured(show) as out:
            show._display_backtest(_run(EQUITY_SNAPSHOT, symbol="AAPL"))
        assert "Crypto (" not in out.getvalue() and "KRAKEN" not in out.getvalue()


class TestReport:
    def test_summary_prints_the_crypto_table(self):
        with _captured(report) as out:
            report._display_crypto(_run())
        assert "BTC/USD (crypto, KRAKEN, fee tier 2)" in out.getvalue()

    def test_equity_summary_prints_nothing(self):
        with _captured(report) as out:
            report._display_crypto(_run(EQUITY_SNAPSHOT, symbol="AAPL"))
        assert out.getvalue() == ""

    def test_text_report_has_the_crypto_section(self, tmp_path):
        path = tmp_path / "r.txt"
        with _captured(report):
            report._generate_text_report(_run(), str(path))
        text = path.read_text()
        assert "Total costs (USD): $124.00" in text
        assert "Largest position: 0.25 BTC" in text
        assert "Note: Spot, unlevered, long-only; Kraken Pro fee tier 2" in text

    def test_csv_report_has_crypto_rows(self, tmp_path):
        path = tmp_path / "r.csv"
        with _captured(report):
            report._generate_csv_report(_run(), str(path))
        with path.open() as handle:
            rows = {row["metric"]: row["value"] for row in csv.DictReader(handle)}
        assert rows["asset_class"] == "crypto"
        assert rows["crypto_fee_tier"] == "2" and rows["crypto_periods_per_year"] == "365"
        assert rows["crypto_commission_maker_usd"] == "20.00"
        assert rows["crypto_orders_below_minimum"] == "3"

    def test_json_report_has_crypto_summary_and_trades(self, tmp_path):
        path = tmp_path / "r.json"
        with _captured(report):
            report._generate_json_report(_run(), str(path))
        data = json.loads(path.read_text())
        assert "fx" not in data and "futures" not in data
        assert data["crypto"]["venue"] == "KRAKEN" and data["crypto"]["fee_tier"] == 2
        assert data["crypto"]["orders_below_minimum"] == 3
        quantities = [t["coin_quantity"] for t in data["crypto"]["trades"]]
        assert quantities == ["0.12820512", "0.25"]
        assert data["crypto"]["trades"][0]["trade_id"] == "T-1"


class TestHistory:
    def test_crypto_row_marks_the_symbol_and_keeps_the_account_return(self):
        text = futures_cli.TestHistory()._history([_run()])
        assert "BTC (crypto)" in text
        assert "1.49%" in text


class TestCompare:
    def _compare(self, runs):
        from src.cli.commands import compare

        with _captured(compare) as out:
            compare._display_comparison_table(runs)
        return out.getvalue()

    def test_crypto_comparison_adds_asset_class_tier_and_costs(self):
        text = self._compare([_run(EQUITY_SNAPSHOT, symbol="AAPL"), _run()])
        assert "Asset Class" in text and "crypto" in text and "equity" in text
        assert "Fee Tier" in text and "Kraken 2" in text
        assert "Peak Exposure" in text and "19.96%" in text
        assert "Total Costs" in text and "$124.00" in text

    def test_equity_only_comparison_has_no_crypto_rows(self):
        text = self._compare([_run(EQUITY_SNAPSHOT, symbol="AAPL")] * 2)
        assert "Asset Class" not in text and "Fee Tier" not in text

    def test_crypto_comparison_shows_the_peak_exposure_basis(self):
        from tests.unit.services.crypto import test_exposure as exact

        exact_run = _base_run(exact._snapshot(True), symbol="BTC", trades=exact.TRADES)
        exact_run.initial_capital = Decimal("100000")
        text = self._compare([_run(), exact_run])
        assert "Peak Exposure Basis" in text
        assert "upper_bound" in text and "exact" in text
        lines = text.splitlines()
        peak = next(i for i, line in enumerate(lines) if "Peak Exposure " in line)
        assert "Peak Exposure Basis" in lines[peak + 1] or "Peak Exposure Basis" in lines[peak + 2]
