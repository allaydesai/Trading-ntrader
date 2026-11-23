"""``backtest run`` FX options (M5): symbol normalisation, ``MID`` bars, ``--units``,
``--spread-pips``.

The catalog row decides FX (the price type comes from it); the lookup is stubbed so no DB is
touched. Equity and futures requests resolve exactly as before.
"""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner
from rich.console import Console

from src.cli.commands import _fx_options
from src.cli.commands._backtest_helpers import resolve_backtest_request
from src.cli.commands._fx_options import check_units_instrument
from src.cli.commands.backtest import run_backtest
from src.services.fx import symbols
from src.services.fx.instrument_builder import build_fx_pair
from src.services.fx.pair_specs import get_pair_spec

pytestmark = pytest.mark.component

START = datetime(2024, 1, 2, tzinfo=timezone.utc)
END = datetime(2024, 2, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fx_catalog(monkeypatch):
    """The ``firstrate-fx`` rows for EURUSD and USDJPY; nothing else is in any catalog."""
    rows = {
        ("firstrate-fx", "EURUSD"): SimpleNamespace(asset_class="FX"),
        ("firstrate-fx", "USDJPY"): SimpleNamespace(asset_class="FX"),
        ("equities", "EURUSD"): SimpleNamespace(asset_class="STOCK"),
    }
    monkeypatch.setattr(
        symbols, "_catalog_row", lambda catalog, ticker: rows.get((catalog, ticker))
    )


def _resolve(symbol="EURUSD", catalog="firstrate-fx", strategy="sma_crossover", **extra):
    return resolve_backtest_request(
        config_file=None,
        symbol=symbol,
        strategy=strategy,
        start=START,
        end=END,
        data_source=None,
        starting_balance=None,
        persist=False,
        console=Console(),
        timeframe="1-HOUR",
        catalog_name=catalog,
        **extra,
    )[0]


class TestSymbolAndBarType:
    @pytest.mark.parametrize("symbol", ["EURUSD", "EUR/USD", "eur/usd", "EUR.USD"])
    def test_symbol_forms_resolve_to_the_catalog_ticker(self, symbol):
        request = _resolve(symbol=symbol)
        assert request.symbol == "EURUSD"
        assert request.instrument_id == "EURUSD.NAMED_CATALOG"

    def test_fx_row_loads_mid_bars(self):
        assert _resolve().bar_type == "1-HOUR-MID"

    def test_non_fx_row_keeps_last(self):
        assert _resolve(catalog="equities").bar_type == "1-HOUR-LAST"

    def test_equity_symbol_is_untouched(self):
        request = _resolve(symbol="AAPL", catalog="equities")
        assert (request.symbol, request.bar_type) == ("AAPL", "1-HOUR-LAST")

    def test_price_type_for(self):
        assert symbols.price_type_for("FX") == "MID"
        assert symbols.price_type_for("STOCK") == "LAST"
        assert symbols.price_type_for(None) == "LAST"

    def test_fx_ticker(self):
        assert symbols.fx_ticker("usd/jpy") == "USDJPY"
        assert symbols.fx_ticker("AAPL") is None
        assert symbols.fx_ticker("ABC/DEF") is None


class TestUnits:
    def test_units_reach_the_strategy_config(self):
        assert _resolve(units=50_000).strategy_config["units"] == 50_000

    def test_unset_leaves_the_config_alone(self):
        assert "units" not in _resolve().strategy_config

    def test_equity_with_units_is_a_usage_error(self):
        with pytest.raises(click.UsageError, match="--units is for FX only"):
            _resolve(symbol="AAPL", catalog="equities", units=1000)

    def test_strategy_without_units_is_a_usage_error(self):
        with pytest.raises(click.UsageError, match="sma_crossover"):
            _resolve(strategy="sma_momentum", units=1000)

    def test_instrument_check(self):
        pair = build_fx_pair(get_pair_spec("EURUSD"), price_precision=5)
        check_units_instrument({"units": 1000}, pair)
        check_units_instrument({}, SimpleNamespace(id="AAPL.NASDAQ", info={}))
        with pytest.raises(ValueError, match="FX only"):
            check_units_instrument({"units": 1000}, SimpleNamespace(id="AAPL.NASDAQ", info={}))
        with pytest.raises(ValueError, match="positive whole number"):
            check_units_instrument({"units": 0}, pair)


class TestSpreadPips:
    def test_spread_override_reaches_the_request(self):
        assert _resolve(spread_pips=1.2).fx_spread_pips == Decimal("1.2")

    def test_equity_spread_is_a_usage_error(self):
        with pytest.raises(click.UsageError, match="--spread-pips is for FX only"):
            _resolve(symbol="AAPL", catalog="equities", spread_pips=1.0)

    def test_override_rebuilds_the_pair_with_the_new_spread(self):
        pair = build_fx_pair(get_pair_spec("EURUSD"), price_precision=5)
        wider = _fx_options.with_spread_pips(pair, Decimal("1.2"))
        assert wider.info["typical_spread_pips"] == "1.2"
        assert wider.id == pair.id and wider.margin_init == pair.margin_init
        assert _fx_options.with_spread_pips(pair, None) is pair


class TestClickOptions:
    def test_help_names_the_fx_options(self):
        result = CliRunner().invoke(run_backtest, ["--help"])
        assert "--units" in result.output and "--spread-pips" in result.output

    @pytest.mark.parametrize("args", [["--units", "0"], ["--spread-pips", "-1"]])
    def test_out_of_range_exits_2(self, args):
        base = ["--symbol", "EURUSD", "--start", "2024-01-02", "--end", "2024-02-01"]
        assert CliRunner().invoke(run_backtest, base + args).exit_code == 2

    def test_equity_units_exits_2_naming_fx(self):
        args = ["--symbol", "AAPL", "--start", "2024-01-02", "--end", "2024-02-01"]
        result = CliRunner().invoke(run_backtest, args + ["--units", "1000"])
        assert result.exit_code == 2
        assert "--units is for FX only" in result.output
