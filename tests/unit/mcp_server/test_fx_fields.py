"""FX fields in the research MCP's run views and export (M5)."""

import csv
from decimal import Decimal

import pytest

from src.mcp_server.export import export_target, fx_trade_fields, write_export
from src.mcp_server.runs import FX_NOTE, compare_view, run_view
from src.services.fx.returns import FINANCING_NOTE
from tests.unit.mcp_server.test_export import _settings
from tests.unit.mcp_server.test_futures_fields import _futures_run
from tests.unit.mcp_server.test_runs import _run
from tests.unit.services.fx.test_returns import SNAPSHOT, TRADE_A, TRADE_B

pytestmark = pytest.mark.unit


def _fx_run():
    run = _run()
    run.config_snapshot = {**run.config_snapshot, **SNAPSHOT}
    run.instrument_symbol = "USDJPY"
    run.initial_capital = Decimal("1000000")
    run.trades = [TRADE_A, TRADE_B]
    return run


def _trade_dict(trade):
    return {k: getattr(trade, k) for k in vars(trade)}


class TestRunView:
    def test_fx_run_has_its_figures_and_the_financing_warning(self):
        view = run_view(_fx_run())
        assert view["asset_class"] == "fx"
        assert "futures" not in view
        assert view["fx"]["peak_leverage"] == "0.31"
        assert view["fx"]["commission_usd"] == "12.00"
        assert view["fx"]["spread_usd"] == "7.10"
        assert view["fx"]["note"] == FINANCING_NOTE
        assert view["fx"]["pairs"]["USD/JPY.IDEALPRO"]["pip_size"] == "0.01"
        assert FX_NOTE in view["warnings"]

    def test_equity_run_is_unchanged(self):
        view = run_view(_run())
        assert view["asset_class"] == "equity" and "fx" not in view

    def test_fx_and_equity_compare_without_the_futures_warning(self):
        equity, fx = _run(), _fx_run()
        view = compare_view([equity, fx], requested=[str(equity.run_id), str(fx.run_id)])
        assert [r["asset_class"] for r in view["rows"]] == ["equity", "fx"]
        assert not any("asset classes" in w for w in view["warnings"])

    def test_fx_and_futures_still_warn(self):
        fx, futures = _fx_run(), _futures_run()
        view = compare_view([fx, futures], requested=[str(fx.run_id), str(futures.run_id)])
        assert any("asset classes" in w for w in view["warnings"])


class TestExportTrades:
    def test_fx_trades_gain_columns_and_run_summary(self):
        row = {"initial_capital": Decimal("1000000")}
        trades = fx_trade_fields(row, SNAPSHOT, [_trade_dict(TRADE_A), _trade_dict(TRADE_B)])
        assert trades[0]["pips"] == "50.0" and trades[0]["lots"] == "1.00"
        assert trades[1]["notional_usd"] == "200000.00"
        assert row["fx"]["peak_leverage"] == "0.31"

    def test_non_fx_trades_are_untouched(self):
        row, trades = {}, [{"profit_loss": 1}]
        assert fx_trade_fields(row, {"config": {}}, trades) is trades
        assert row == {}

    def test_mixed_export_writes_blank_fx_cells(self, tmp_path):
        vault = tmp_path / "vault"
        (vault / "Lab" / "results").mkdir(parents=True)
        target = export_target(_settings(vault), "Lab/results", "mix", overwrite=False)
        fx = fx_trade_fields({}, SNAPSHOT, [_trade_dict(TRADE_B)])
        trades = [{"profit_loss": 5, "run_id": "e"}, {**fx[0], "run_id": "f"}]
        write_export(target, slug="mix", runs=[], trades=trades, missing=[], git={})
        rows = list(csv.DictReader(target.csv_path.open()))
        assert rows[0]["pips"] == ""
        assert rows[1]["pips"] == "-50.0"
