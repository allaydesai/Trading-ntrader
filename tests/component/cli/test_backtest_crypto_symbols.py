"""Crypto M5 (task 5.3): ``backtest run`` symbol forms, ``LAST`` bars and ``--spread-bps``.

``--symbol BTC``, ``BTCUSD``, ``BTC/USD``, ``XBTUSD`` and ``BTC/USD.KRAKEN`` against a named
catalog all become the catalog ticker ``BTC`` (so the run, its config hash and
``--units``/``--fee-tier`` are the same), with ``LAST`` bars. A crypto-shaped symbol no spec
ships exits 2 naming the shipped pairs. ``--spread-bps X`` replaces the coin's modelled spread
for the run (part of its identity only when set), like FX's ``--spread-pips``.
"""

from decimal import Decimal
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.cli.commands._crypto_options import with_crypto_terms
from src.cli.commands.backtest import run_backtest
from src.core.crypto_costs import crypto_terms
from src.services.crypto.coin_specs import get_coin_spec
from src.services.crypto.instrument_builder import build_crypto_pair
from src.services.fx.symbols import price_type_for, run_target
from src.services.provenance import compute_config_hash
from tests.component.cli.test_backtest_crypto_options import _resolve

pytestmark = pytest.mark.component

FORMS = ["BTC", "btc", "BTCUSD", "BTC/USD", "BTC-USD", "XBTUSD", "BTC/USD.KRAKEN"]


class TestSymbolForms:
    @pytest.mark.parametrize("symbol", FORMS)
    def test_every_form_is_the_btc_catalog_ticker_on_last_bars(self, symbol):
        request = _resolve(symbol=symbol)
        assert request.symbol == "BTC"
        assert request.instrument_id == "BTC.NAMED_CATALOG"
        assert request.bar_type == "1-DAY-LAST"

    def test_every_form_is_the_same_run(self):
        hashes = {compute_config_hash(_resolve(symbol=s)) for s in FORMS}
        assert len(hashes) == 1

    @pytest.mark.parametrize("symbol", ["BTC/USD", "XBTUSD", "BTC/USD.KRAKEN"])
    def test_units_and_fee_tier_take_any_form(self, symbol):
        request = _resolve(symbol=symbol, units=Decimal("0.25"), fee_tier=3)
        assert request.strategy_config["units"] == Decimal("0.25")
        assert request.crypto_fee_tier == 3

    @pytest.mark.parametrize(
        ("symbol", "why"),
        [
            ("BTC/EUR", "quoted in USD only"),
            ("BTCUSDT", "quoted in USD only"),
            ("BTC/USD.BINANCE", "KRAKEN venue"),
            ("DOGE/USD", "unknown crypto coin DOGE"),
        ],
    )
    def test_an_unshipped_crypto_pair_is_a_usage_error(self, symbol, why):
        with pytest.raises(click.UsageError, match=why) as caught:
            _resolve(symbol=symbol)
        assert "BTC/USD, ETH/USD, SOL/USD" in str(caught.value)

    def test_the_command_exits_2_for_an_unshipped_pair(self):
        result = CliRunner().invoke(
            run_backtest,
            ["--symbol", "BTC/EUR", "--start", "2024-01-02", "--end", "2024-02-01"]
            + ["--catalog", "firstrate-crypto", "--no-persist"],
        )
        assert result.exit_code == 2, result.output
        assert "quoted in USD only" in result.output

    def test_an_equity_symbol_is_unchanged(self):
        request = _resolve(symbol="AAPL")
        assert (request.symbol, request.instrument_id) == ("AAPL", "AAPL.NAMED_CATALOG")
        assert request.bar_type == "1-DAY-LAST"

    def test_the_crypto_price_type_is_last(self):
        assert price_type_for("CRYPTO") == "LAST"


class TestSpreadBps:
    def test_reaches_the_request_and_the_identity_only_when_set(self):
        plain, wide = _resolve(), _resolve(spread_bps=5.0)
        assert plain.crypto_spread_bps is None
        assert wide.crypto_spread_bps == Decimal("5.0")
        assert compute_config_hash(plain) != compute_config_hash(wide)
        assert compute_config_hash(plain) == compute_config_hash(_resolve())

    @pytest.mark.parametrize("bad", [float("inf"), float("nan")])
    def test_a_non_finite_spread_is_a_usage_error(self, bad):
        with pytest.raises(click.UsageError, match="finite"):
            _resolve(spread_bps=bad)

    def test_a_non_crypto_symbol_is_a_usage_error(self):
        with pytest.raises(click.UsageError, match="--spread-bps is for spot crypto only"):
            _resolve(symbol="AAPL", spread_bps=5.0)

    def test_the_pair_is_rebuilt_with_the_spread(self):
        pair = build_crypto_pair(get_coin_spec("BTC"), price_precision=4)
        rebuilt = with_crypto_terms(pair, None, Decimal("5"))
        assert rebuilt.info["typical_spread_bps"] == "5"
        assert crypto_terms(rebuilt).spread_bps == Decimal("5")
        assert rebuilt.price_precision == 4 and rebuilt.id == pair.id

    def test_tier_and_spread_together(self):
        pair = build_crypto_pair(get_coin_spec("BTC"), price_precision=4)
        terms = crypto_terms(with_crypto_terms(pair, 4, Decimal("3")))
        assert (terms.fee_tier, terms.spread_bps) == (4, Decimal("3"))

    def test_unchanged_without_either(self):
        pair = build_crypto_pair(get_coin_spec("BTC"))
        assert with_crypto_terms(pair, None, None) is pair
        equity = TestInstrumentProvider.equity("AAPL", "XNAS")
        assert with_crypto_terms(equity, None, None) is equity

    def test_the_command_exits_2_for_a_negative_spread(self):
        result = CliRunner().invoke(
            run_backtest,
            ["--symbol", "BTC", "--start", "2024-01-02", "--end", "2024-02-01"]
            + ["--catalog", "firstrate-crypto", "--no-persist", "--spread-bps", "-1"],
        )
        assert result.exit_code == 2, result.output


class TestWebRunTarget:
    """The dashboard's Run Backtest form maps a crypto spelling to the coin's row (M5)."""

    @pytest.mark.parametrize("symbol", ["BTC", "BTC/USD", "XBTUSD", "BTC/USD.KRAKEN"])
    def test_crypto_form_gets_the_coin_and_last_bars(self, symbol):
        form = SimpleNamespace(symbol=symbol, catalog_name="firstrate-crypto", timeframe="1-DAY")
        assert run_target(form) == ("BTC", "1-DAY-LAST")

    def test_an_unshipped_pair_is_left_for_the_loader_to_refuse(self):
        form = SimpleNamespace(symbol="BTC/EUR", catalog_name="firstrate-crypto", timeframe="1-DAY")
        assert run_target(form) == ("BTC/EUR", "1-DAY-LAST")
