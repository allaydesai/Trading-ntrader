"""``--data-source kraken`` fetches Kraken *spot* candles or refuses (crypto M7, 7.2 and 7.4).

Spike (2026-11-24, public endpoints, no key): the Kraken Futures Charts API that this path used
lists only futures symbols (``PF_``/``PI_``/``FF_``/``FI_``/``OF_``) under every tick type; its
``spot`` tick type is the futures' reference index (volume 0, from 2022-03-22), not Kraken spot
trades. Kraken's spot REST ``OHLC`` endpoint returns the latest 720 candles of an interval plus
the still-open one (``last`` is its timestamp). So the path reads spot ``OHLC`` and refuses a
range that starts before that window, pointing at the ``kraken-crypto`` catalog.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.core.position_sizing import is_crypto
from src.services.crypto.kraken_spot import (
    fallback_coin_spec,
    rest_window_start,
    spot_bars,
    spot_instrument,
    spot_interval,
)
from src.services.exceptions import DataNotFoundError, KrakenSpotRangeError
from src.services.kraken_client import KrakenHistoricalClient, KrakenPairMapper

pytestmark = pytest.mark.component

NOW = datetime(2026, 11, 25, 3, 26, 30, tzinfo=timezone.utc)
H = 3600


def _rows(first: int, count: int, step: int = H) -> list[list]:
    return [
        [first + i * step, "59000.1", "59100.0", "58900.5", f"59050.{i % 10}", "0", "1.5", 9]
        for i in range(count)
    ]


DOGE_INFO = {
    "altname": "XDGUSD",
    "wsname": "XDG/USD",
    "pair_decimals": 7,
    "lot_decimals": 8,
    "ordermin": "50",
    "costmin": "0.5",
    "tick_size": "0.0000001",
}


class TestMapper:
    @pytest.mark.parametrize(
        ("pair", "rest"),
        [
            ("BTC/USD", "XXBTZUSD"),  # the coin spec's AssetPairs key
            ("ETH/USD", "XETHZUSD"),
            ("SOL/USD", "SOLUSD"),  # not SOLZUSD: Kraken answers "Unknown asset pair"
            ("DOGE/USD", "XDGUSD"),  # no spec: the altname form Kraken resolves
        ],
    )
    def test_rest_names_are_ones_kraken_accepts(self, pair, rest):
        assert KrakenPairMapper.to_kraken_rest(pair) == rest

    def test_the_mapper_has_no_perpetual_symbol(self):
        assert not hasattr(KrakenPairMapper, "to_kraken_charts")


class TestInterval:
    @pytest.mark.parametrize(
        ("spec", "minutes"),
        [("1-MINUTE-LAST", 1), ("5-MINUTE", 5), ("1-HOUR-LAST", 60), ("1-DAY-LAST", 1440)],
    )
    def test_spot_intervals(self, spec, minutes):
        assert spot_interval(spec) == minutes

    @pytest.mark.parametrize("spec", ["12-HOUR-LAST", "1-HOUR-MID", "1-HOUR-BID"])
    def test_an_interval_or_price_type_kraken_does_not_publish_is_refused(self, spec):
        with pytest.raises(ValueError, match="Kraken spot OHLC"):
            spot_interval(spec)


class TestWindow:
    def test_the_window_is_the_latest_720_candles_before_the_open_one(self):
        assert rest_window_start(60, NOW) == datetime(2026, 10, 26, 3, tzinfo=timezone.utc)
        assert rest_window_start(1440, NOW) == datetime(2024, 12, 5, tzinfo=timezone.utc)

    async def test_an_older_range_is_refused_before_any_request(self):
        client = KrakenHistoricalClient()
        client._spot_market = MagicMock()
        with pytest.raises(KrakenSpotRangeError) as caught:
            await client.fetch_bars(
                "BTC/USD.KRAKEN",
                datetime(2024, 1, 1, tzinfo=timezone.utc),
                NOW,
                "1-HOUR-LAST",
                now=NOW,
            )
        message = str(caught.value)
        assert "720" in message and "2026-10-26 03:00" in message
        assert "--format kraken" in message and "kraken-crypto" in message
        assert isinstance(caught.value, DataNotFoundError)  # web UI and CLI already show it
        client._spot_market.get_ohlc.assert_not_called()


class TestBars:
    def test_committed_candles_in_range_at_the_spec_precision(self):
        first = int(datetime(2026, 11, 24, tzinfo=timezone.utc).timestamp())
        rows = _rows(first, 6)
        bars = spot_bars(
            rows,
            last=rows[-1][0],  # the open candle is dropped
            instrument_id="BTC/USD.KRAKEN",
            bar_type_spec="1-HOUR-LAST",
            start=datetime(2026, 11, 24, 1, tzinfo=timezone.utc),
            end=datetime(2026, 11, 24, 23, tzinfo=timezone.utc),
            price_precision=4,
        )
        assert [b.ts_event // 10**9 for b in bars] == [
            first + H,
            first + 2 * H,
            first + 3 * H,
            first + 4 * H,
        ]
        assert str(bars[0].bar_type) == "BTC/USD.KRAKEN-1-HOUR-LAST-EXTERNAL"
        assert str(bars[0].close) == "59050.1000" and str(bars[0].volume) == "1.50000000"


class TestInstrument:
    def test_a_spec_coin_gets_the_spec_built_pair(self):
        pair = spot_instrument("BTC/USD", pair_info=None)
        assert str(pair.id) == "BTC/USD.KRAKEN" and is_crypto(pair)
        assert pair.price_precision == 4 and pair.size_precision == 8
        assert pair.info["kraken"]["min_order_base"] == "0.00006"
        assert pair.maker_fee == 0 and pair.taker_fee == 0  # the venue model charges

    def test_a_coin_without_a_spec_gets_a_fallback_on_the_same_schedule(self):
        pair = spot_instrument("DOGE/USD", pair_info=DOGE_INFO)
        assert str(pair.id) == "DOGE/USD.KRAKEN" and is_crypto(pair)
        assert pair.price_precision == 7
        assert pair.info["kraken"]["min_order_base"] == "50"
        assert pair.info["fee_schedule"]["default_tier"] == 2
        assert "typical_spread_bps" in pair.info["unverified"]

    def test_the_fallback_spec_is_flagged_unverified(self):
        spec = fallback_coin_spec("DOGE", DOGE_INFO)
        assert spec.data == "none" and spec.kraken.altname == "XDGUSD"
        assert spec.typical_spread_bps == Decimal("5")
        assert set(spec.unverified_fields()) >= {"kraken_min_order_base", "typical_spread_bps"}

    def test_a_coin_without_a_spec_or_pair_metadata_is_refused(self):
        with pytest.raises(DataNotFoundError, match="DOGE/USD"):
            spot_instrument("DOGE/USD", pair_info=None)

    def test_a_non_usd_quote_is_refused(self):
        with pytest.raises(ValueError, match="USD"):
            spot_instrument("BTC/EUR", pair_info=None)


class TestFetch:
    async def test_fetch_reads_spot_ohlc_without_credentials(self):
        client = KrakenHistoricalClient()  # public endpoints: no key needed
        await client.connect()
        market = MagicMock()
        first = int(datetime(2026, 11, 24, tzinfo=timezone.utc).timestamp())
        rows = _rows(first, 4)
        market.get_ohlc.return_value = {"XXBTZUSD": rows, "last": rows[-1][0]}
        client._spot_market = market
        bars, pair = await client.fetch_bars(
            "BTC/USD.KRAKEN",
            datetime(2026, 11, 24, tzinfo=timezone.utc),
            NOW,
            "1-HOUR-LAST",
            now=NOW,
        )
        assert len(bars) == 3 and str(pair.id) == "BTC/USD.KRAKEN" and is_crypto(pair)
        kwargs = market.get_ohlc.call_args.kwargs
        assert kwargs["pair"] == "XXBTZUSD" and kwargs["interval"] == 60
        assert kwargs["since"] == first - 1
        market.get_asset_pairs.assert_not_called()  # a spec coin needs no metadata call

    async def test_an_empty_range_is_data_not_found(self):
        client = KrakenHistoricalClient()
        await client.connect()
        client._spot_market = MagicMock()
        client._spot_market.get_ohlc.return_value = {"XXBTZUSD": [], "last": 0}
        start = NOW - timedelta(hours=3)
        with pytest.raises(DataNotFoundError):
            await client.fetch_bars("BTC/USD.KRAKEN", start, NOW, "1-HOUR-LAST", now=NOW)
