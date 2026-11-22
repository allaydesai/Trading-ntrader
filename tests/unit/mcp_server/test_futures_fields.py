"""Futures fields in the research MCP's run views and export (M4)."""

import csv
from datetime import datetime, timezone

import pytest

from src.mcp_server.export import export_target, futures_trade_fields, write_export
from src.mcp_server.runs import compare_view, run_view
from tests.unit.mcp_server.test_export import _settings
from tests.unit.mcp_server.test_runs import _run
from tests.unit.services.futures.test_returns import ES_SNAPSHOT, TRADE_A, TRADE_B

pytestmark = pytest.mark.unit

UTC = timezone.utc


def _futures_run():
    run = _run()
    run.config_snapshot = {**run.config_snapshot, **ES_SNAPSHOT}
    run.instrument_symbol = "ES"
    run.trades = [TRADE_A, TRADE_B]
    return run


class TestRunView:
    def test_equity_run_only_gains_asset_class(self):
        view = run_view(_run())
        assert view["asset_class"] == "equity"
        assert "futures" not in view
        assert view["warnings"] == []

    def test_futures_run_has_return_on_margin_and_a_warning(self):
        view = run_view(_futures_run())
        assert view["asset_class"] == "futures"
        assert view["futures"]["return_on_margin_pct"] == "2.7027"
        assert view["futures"]["contracts"]["ES.CME"]["margin_initial_usd"] == "27500"
        assert any("return on margin" in w for w in view["warnings"])

    def test_compare_warns_when_asset_classes_mix(self):
        equity, futures = _run(), _futures_run()
        view = compare_view([equity, futures], requested=[str(equity.run_id), str(futures.run_id)])
        assert [r["asset_class"] for r in view["rows"]] == ["equity", "futures"]
        assert any("asset classes" in w for w in view["warnings"])


def _trade_dict(trade):
    return {k: getattr(trade, k) for k in vars(trade)}


class TestExportTrades:
    def test_futures_trades_gain_columns_and_run_summary(self):
        row = {}
        trades = futures_trade_fields(row, ES_SNAPSHOT, [_trade_dict(TRADE_A)])
        assert trades[0]["contracts"] == "2"
        assert trades[0]["return_on_margin_pct"] == "3.6200"
        assert row["futures"]["peak_margin_usd"] == "55000.00"

    def test_equity_trades_are_untouched(self):
        row, trades = {}, [{"profit_loss": 1}]
        assert futures_trade_fields(row, {"config": {}}, trades) is trades
        assert row == {}

    def test_mixed_export_writes_one_header_with_blank_futures_cells(self, tmp_path):
        vault = tmp_path / "vault"
        (vault / "Lab" / "results").mkdir(parents=True)
        target = export_target(_settings(vault), "Lab/results", "mix", overwrite=False)
        futures = futures_trade_fields({}, ES_SNAPSHOT, [_trade_dict(TRADE_B)])
        trades = [
            {"profit_loss": 5, "exit_timestamp": datetime(2020, 1, 2, tzinfo=UTC), "run_id": "e"},
            {**futures[0], "run_id": "f"},
        ]
        write_export(target, slug="mix", runs=[], trades=trades, missing=[], git={})
        rows = list(csv.DictReader(target.csv_path.open()))
        assert rows[0]["contracts"] == ""
        assert rows[1]["contracts"] == "1"
        assert rows[1]["points_adjusted"] == "-10.00"

    def test_equity_only_export_has_no_futures_columns(self, tmp_path):
        vault = tmp_path / "vault"
        (vault / "Lab" / "results").mkdir(parents=True)
        target = export_target(_settings(vault), "Lab/results", "eq", overwrite=False)
        trades = [{"profit_loss": 5, "run_id": "e"}]
        write_export(target, slug="eq", runs=[], trades=trades, missing=[], git={})
        header = target.csv_path.read_text().splitlines()[0]
        assert header == "profit_loss,run_id"
