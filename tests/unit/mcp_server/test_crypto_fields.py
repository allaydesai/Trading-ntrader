"""Crypto fields in the research MCP's run views and export (crypto M5, task 5.5).

Additive: a crypto run gains ``asset_class: "crypto"``, a ``crypto`` object and a warning;
its exported trades gain the crypto columns. Every other run's payload is unchanged.
"""

import csv
from decimal import Decimal

import pytest

from src.mcp_server.export import crypto_trade_fields, export_target, write_export
from src.mcp_server.runs import CRYPTO_NOTE, compare_view, run_view
from tests.unit.mcp_server.test_export import _settings
from tests.unit.mcp_server.test_futures_fields import _futures_run
from tests.unit.mcp_server.test_fx_fields import _fx_run
from tests.unit.mcp_server.test_runs import _run
from tests.unit.services.crypto.test_returns import SNAPSHOT, TRADES

pytestmark = pytest.mark.unit


def _crypto_run():
    run = _run()
    run.config_snapshot = {**run.config_snapshot, **SNAPSHOT}
    run.instrument_symbol = "BTC"
    run.initial_capital = Decimal("100000")
    run.trades = list(TRADES)
    return run


class TestRunView:
    def test_crypto_run_has_its_object_and_warning(self):
        view = run_view(_crypto_run())
        assert view["asset_class"] == "crypto"
        assert "fx" not in view and "futures" not in view
        crypto = view["crypto"]
        assert crypto["venue"] == "KRAKEN" and crypto["fee_tier"] == 2
        assert crypto["periods_per_year"] == 365 and crypto["account_currency"] == "USD"
        assert crypto["commission_maker_usd"] == "20.00"
        assert crypto["commission_taker_usd"] == "100.00"
        assert crypto["spread_usd"] == "4.00"
        assert crypto["max_quantity"] == "0.25"
        assert crypto["orders_below_minimum"] == 3
        assert crypto["pairs"]["BTC/USD.KRAKEN"]["taker_pct"] == "0.60"
        assert CRYPTO_NOTE in view["warnings"]
        assert "365" in CRYPTO_NOTE

    @pytest.mark.parametrize("make", [_run, _fx_run, _futures_run])
    def test_other_runs_have_no_crypto_object(self, make):
        view = run_view(make())
        assert "crypto" not in view and view["asset_class"] != "crypto"
        assert CRYPTO_NOTE not in view["warnings"]

    def test_crypto_and_equity_compare_without_the_futures_warning(self):
        equity, crypto = _run(), _crypto_run()
        ids = [str(equity.run_id), str(crypto.run_id)]
        view = compare_view([equity, crypto], requested=ids)
        assert [r["asset_class"] for r in view["rows"]] == ["equity", "crypto"]
        assert view["rows"][1]["crypto"]["fee_tier"] == 2
        assert not any("asset classes" in w for w in view["warnings"])


class TestExportTrades:
    def test_crypto_trades_gain_columns_and_run_summary(self):
        row = {"initial_capital": Decimal("100000")}
        trades = crypto_trade_fields(row, SNAPSHOT, [dict(t) for t in TRADES])
        assert trades[0]["coin_quantity"] == "0.12820512"
        assert trades[1]["coin_quantity"] == "0.25"
        assert trades[0]["notional_usd"] == "10000.00"
        assert row["crypto"]["fee_tier"] == 2

    def test_non_crypto_trades_are_untouched(self):
        row, trades = {}, [{"profit_loss": 1}]
        assert crypto_trade_fields(row, {"config": {}}, trades) is trades
        assert row == {}

    def test_mixed_export_writes_blank_crypto_cells(self, tmp_path):
        vault = tmp_path / "vault"
        (vault / "Lab" / "results").mkdir(parents=True)
        target = export_target(_settings(vault), "Lab/results", "mix", overwrite=False)
        crypto = crypto_trade_fields({}, SNAPSHOT, [dict(TRADES[0])])
        trades = [{"profit_loss": 5, "run_id": "e"}, {**crypto[0], "run_id": "c"}]
        write_export(target, slug="mix", runs=[], trades=trades, missing=[], git={})
        rows = list(csv.DictReader(target.csv_path.open()))
        assert rows[0]["coin_quantity"] == ""
        assert rows[1]["coin_quantity"] == "0.12820512"
