"""CLI renderers for FX runs (M5): ``show``, ``report``, ``history`` and ``compare``.

An FX run gets its block (pips, lots, USD notional, leverage, costs split, financing note)
next to the account return, which stays the headline; equity and futures runs render as before
(``tests/unit/cli/test_futures_reporting_cli.py`` pins those).
"""

import csv
import json

import pytest

from src.cli.commands import report, show
from src.services.fx.returns import FINANCING_NOTE
from tests.unit.cli import test_futures_reporting_cli as futures_cli
from tests.unit.cli.test_futures_reporting_cli import EQUITY_SNAPSHOT, _captured
from tests.unit.cli.test_futures_reporting_cli import _run as _base_run
from tests.unit.services.fx.test_returns import SNAPSHOT, TRADE_A, TRADE_B

pytestmark = pytest.mark.unit


def _run(snapshot=SNAPSHOT, symbol="USDJPY"):
    run = _base_run(snapshot, symbol=symbol, trades=(TRADE_A, TRADE_B))
    run.initial_capital = run.initial_capital * 10  # USD 1,000,000, as test_returns
    return run


class TestShow:
    def test_fx_run_shows_its_block_before_the_config(self):
        with _captured(show) as out:
            show._display_backtest(_run())
        text = out.getvalue()
        assert "FX (account return on USD equity is the headline)" in text
        assert "Peak notional leverage" in text and "0.31x" in text
        assert "Modelled spread (USD)" in text and "$7.10" in text
        assert "Financing" in text
        assert text.index("Peak notional leverage") < text.index("Configuration Snapshot")

    def test_equity_run_has_no_fx_block(self):
        with _captured(show) as out:
            show._display_backtest(_run(EQUITY_SNAPSHOT, symbol="AAPL"))
        assert "FX (" not in out.getvalue()
        assert "Financing" not in out.getvalue()


class TestReport:
    def test_summary_prints_the_fx_table(self):
        with _captured(report) as out:
            report._display_fx(_run())
        assert "Peak notional leverage" in out.getvalue()

    def test_equity_summary_prints_nothing(self):
        with _captured(report) as out:
            report._display_fx(_run(EQUITY_SNAPSHOT, symbol="AAPL"))
        assert out.getvalue() == ""

    def test_text_report_has_fx_section_and_note(self, tmp_path):
        path = tmp_path / "r.txt"
        with _captured(report):
            report._generate_text_report(_run(), str(path))
        text = path.read_text()
        assert "Total costs (USD): $19.10" in text
        assert f"Note: {FINANCING_NOTE}" in text

    def test_csv_report_has_fx_rows(self, tmp_path):
        path = tmp_path / "r.csv"
        with _captured(report):
            report._generate_csv_report(_run(), str(path))
        with path.open() as handle:
            rows = {row["metric"]: row["value"] for row in csv.DictReader(handle)}
        assert rows["asset_class"] == "fx"
        assert rows["fx_peak_leverage"] == "0.31"
        assert rows["fx_USD/JPY.IDEALPRO_spread_usd"] == "4.60"

    def test_json_report_has_fx_summary_and_trades(self, tmp_path):
        path = tmp_path / "r.json"
        with _captured(report):
            report._generate_json_report(_run(), str(path))
        data = json.loads(path.read_text())
        assert "futures" not in data
        assert data["fx"]["costs_usd"] == "19.10"
        assert data["fx"]["note"] == FINANCING_NOTE
        assert [t["pips"] for t in data["fx"]["trades"]] == ["50.0", "-50.0"]
        assert data["fx"]["trades"][1]["lots"] == "2.00"


class TestHistory:
    def test_fx_row_marks_the_symbol_and_keeps_the_account_return(self):
        text = futures_cli.TestHistory()._history([_run()])
        assert "USDJPY (fx)" in text
        assert "1.49%" in text
        assert "RoM" not in text


class TestCompare:
    def _compare(self, runs):
        from src.cli.commands import compare

        with _captured(compare) as out:
            compare._display_comparison_table(runs)
        return out.getvalue()

    def test_fx_comparison_adds_asset_class_pips_and_leverage(self):
        text = self._compare([_run(EQUITY_SNAPSHOT, symbol="AAPL"), _run()])
        assert "Asset Class" in text and "fx" in text and "equity" in text
        assert "Peak Leverage" in text and "0.31x" in text
        assert "Total Costs" in text and "$19.10" in text

    def test_equity_only_comparison_has_no_fx_rows(self):
        text = self._compare([_run(EQUITY_SNAPSHOT, symbol="AAPL")] * 2)
        assert "Asset Class" not in text and "Peak Leverage" not in text
