"""Crypto M5 (task 5.3): every spelling of a shipped pair names its coin's catalog ticker.

``BTC/USD``, ``BTCUSD``, ``XBTUSD`` (Kraken's altname), ``XXBTZUSD`` (its REST name) and
``BTC/USD.KRAKEN`` (the Nautilus id) are all ``BTC``, the catalog ticker of
``firstrate-crypto``. A symbol shaped like a crypto pair that no spec ships (another quote,
another venue, an unknown coin) is refused naming the pairs that exist; anything else (an
equity, an FX pair, a futures root) is not crypto and is left to its own resolver.
"""

import pytest

from src.services.crypto import symbols
from src.services.crypto.symbols import CryptoSymbolError

pytestmark = pytest.mark.unit


class TestResolve:
    @pytest.mark.parametrize(
        "symbol",
        [
            "BTC",
            "btc",
            " BTC ",
            "BTC/USD",
            "btc/usd",
            "BTCUSD",
            "BTC-USD",
            "XBTUSD",
            "XBT/USD",
            "XBT",
            "XXBTZUSD",
            "BTC/USD.KRAKEN",
            "BTCUSD.KRAKEN",
            "XBTUSD.KRAKEN",
        ],
    )
    def test_every_btc_spelling_is_btc(self, symbol):
        assert symbols.crypto_coin(symbol) == "BTC"

    @pytest.mark.parametrize(("symbol", "coin"), [("ETH/USD", "ETH"), ("SOLUSD", "SOL")])
    def test_other_coins(self, symbol, coin):
        assert symbols.crypto_coin(symbol) == coin

    @pytest.mark.parametrize(
        "symbol", ["AAPL", "SPY", "EURUSD", "EUR/USD", "ES", "BRK.B", "ETHE", "ETHA", "BTCO"]
    )
    def test_non_crypto_symbols_are_not_crypto(self, symbol):
        assert symbols.crypto_coin(symbol) is None


class TestRefuse:
    @pytest.mark.parametrize(
        ("symbol", "why"),
        [
            ("BTC/EUR", "quote"),
            ("XBTEUR", "quote"),
            ("BTCUSDT", "quote"),
            ("ETH/BTC", "quote"),
            ("BTC/USD.BINANCE", "venue"),
            ("DOGE/USD", "unknown"),
            ("DOGEUSD.KRAKEN", "unknown"),
        ],
    )
    def test_crypto_shaped_but_not_shipped(self, symbol, why):
        with pytest.raises(CryptoSymbolError, match=why) as caught:
            symbols.crypto_coin(symbol)
        assert "BTC/USD, ETH/USD, SOL/USD" in str(caught.value)
        assert symbol.strip() in str(caught.value)

    def test_an_alias_two_coins_share_is_ambiguous(self, monkeypatch):
        monkeypatch.setattr(symbols, "_aliases", lambda: {"BTC": {"BTC"}, "XBT": {"BTC", "XBTC"}})
        with pytest.raises(CryptoSymbolError, match="ambiguous"):
            symbols.crypto_coin("XBT")

    def test_the_error_is_a_value_error(self):
        assert issubclass(CryptoSymbolError, ValueError)


class TestPairId:
    def test_nautilus_id_of_a_coin(self):
        assert symbols.nautilus_id("BTC") == "BTC/USD.KRAKEN"
