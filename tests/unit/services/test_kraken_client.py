"""Unit tests for Kraken client: pair mapper, converter, rate limiter, client."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from nautilus_trader.model.data import Bar
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity

from src.services.exceptions import DataNotFoundError, KrakenConnectionError
from src.services.kraken_client import (
    KrakenHistoricalClient,
    KrakenPairMapper,
    KrakenRateLimiter,
    build_currency_pair,
    convert_ohlcv_to_bars,
)

# ---------------------------------------------------------------------------
# T007: Pair Mapping Tests
# ---------------------------------------------------------------------------


class TestKrakenPairMapperToNautilusId:
    """Test to_nautilus_id: user pair → Nautilus instrument ID string."""

    def test_btc_usd(self):
        assert KrakenPairMapper.to_nautilus_id("BTC/USD") == "BTC/USD.KRAKEN"

    def test_eth_usd(self):
        assert KrakenPairMapper.to_nautilus_id("ETH/USD") == "ETH/USD.KRAKEN"

    def test_sol_usd(self):
        assert KrakenPairMapper.to_nautilus_id("SOL/USD") == "SOL/USD.KRAKEN"

    def test_doge_usd(self):
        assert KrakenPairMapper.to_nautilus_id("DOGE/USD") == "DOGE/USD.KRAKEN"


class TestKrakenPairMapperToKrakenRest:
    """Test to_kraken_rest: user pair → Kraken REST pair string."""

    def test_btc_usd(self):
        assert KrakenPairMapper.to_kraken_rest("BTC/USD") == "XXBTZUSD"

    def test_eth_usd(self):
        assert KrakenPairMapper.to_kraken_rest("ETH/USD") == "XETHZUSD"

    def test_sol_usd(self):
        # SOL is not legacy-crypto-prefixed, USD gets Z prefix
        assert KrakenPairMapper.to_kraken_rest("SOL/USD") == "SOLZUSD"

    def test_doge_usd(self):
        # DOGE maps to XDG in REST, XDG is legacy-prefixed → XXDG
        assert KrakenPairMapper.to_kraken_rest("DOGE/USD") == "XXDGZUSD"

    def test_ltc_eur(self):
        assert KrakenPairMapper.to_kraken_rest("LTC/EUR") == "XLTCZEUR"


class TestKrakenPairMapperToKrakenCharts:
    """Test to_kraken_charts: user pair → Charts API symbol."""

    def test_btc_usd(self):
        assert KrakenPairMapper.to_kraken_charts("BTC/USD") == "PF_XBTUSD"

    def test_eth_usd(self):
        assert KrakenPairMapper.to_kraken_charts("ETH/USD") == "PF_ETHUSD"

    def test_sol_usd(self):
        assert KrakenPairMapper.to_kraken_charts("SOL/USD") == "PF_SOLUSD"

    def test_doge_usd(self):
        # DOGE stays DOGE in Charts API (not XDG)
        assert KrakenPairMapper.to_kraken_charts("DOGE/USD") == "PF_DOGEUSD"


class TestKrakenPairMapperFromNautilusId:
    """Test from_nautilus_id: Nautilus ID → (user_pair, venue)."""

    def test_btc_usd_kraken(self):
        pair, venue = KrakenPairMapper.from_nautilus_id("BTC/USD.KRAKEN")
        assert pair == "BTC/USD"
        assert venue == "KRAKEN"

    def test_eth_usd_kraken(self):
        pair, venue = KrakenPairMapper.from_nautilus_id("ETH/USD.KRAKEN")
        assert pair == "ETH/USD"
        assert venue == "KRAKEN"

    def test_sol_usd_kraken(self):
        pair, venue = KrakenPairMapper.from_nautilus_id("SOL/USD.KRAKEN")
        assert pair == "SOL/USD"
        assert venue == "KRAKEN"


class TestKrakenPairMapperSpecialMappings:
    """Test special symbol mappings and roundtrips."""

    def test_btc_xbt_roundtrip_rest(self):
        """BTC → XBT in REST, but user always uses BTC."""
        rest = KrakenPairMapper.to_kraken_rest("BTC/USD")
        assert "XBT" in rest

    def test_btc_xbt_roundtrip_charts(self):
        """BTC → XBT in Charts API."""
        charts = KrakenPairMapper.to_kraken_charts("BTC/USD")
        assert "XBT" in charts

    def test_doge_rest_vs_charts_differ(self):
        """DOGE maps to XDG in REST but stays DOGE in Charts."""
        rest = KrakenPairMapper.to_kraken_rest("DOGE/USD")
        charts = KrakenPairMapper.to_kraken_charts("DOGE/USD")
        assert "XDG" in rest
        assert "DOGE" in charts

    def test_nautilus_id_roundtrip(self):
        """to_nautilus_id → from_nautilus_id roundtrip."""
        nid = KrakenPairMapper.to_nautilus_id("BTC/USD")
        pair, venue = KrakenPairMapper.from_nautilus_id(nid)
        assert pair == "BTC/USD"
        assert venue == "KRAKEN"


class TestKrakenPairMapperErrors:
    """Test error handling for invalid inputs."""

    def test_invalid_pair_format_no_slash(self):
        with pytest.raises(ValueError, match="Invalid pair format"):
            KrakenPairMapper.to_nautilus_id("BTCUSD")

    def test_invalid_nautilus_id_no_dot(self):
        with pytest.raises(ValueError, match="Invalid Nautilus instrument ID"):
            KrakenPairMapper.from_nautilus_id("BTCUSD")

    def test_empty_pair(self):
        with pytest.raises(ValueError):
            KrakenPairMapper.to_nautilus_id("")


# ---------------------------------------------------------------------------
# T008: OHLCV-to-Bar Conversion Tests
# ---------------------------------------------------------------------------


class TestConvertOhlcvToBars:
    """Test convert_ohlcv_to_bars: Kraken candles → Nautilus Bars."""

    def test_single_candle_conversion(self):
        candles = [
            {
                "time": 1678888800000,
                "open": "24885.4",
                "high": "25039.43",
                "low": "24529.18",
                "close": "24793.87",
                "volume": "0",
            }
        ]
        instrument_id = InstrumentId(Symbol("BTC/USD"), Venue("KRAKEN"))
        bars = convert_ohlcv_to_bars(candles, instrument_id, "1-HOUR-LAST", price_precision=1)
        assert len(bars) == 1
        bar = bars[0]
        assert isinstance(bar, Bar)

    def test_bar_type_format(self):
        candles = [
            {
                "time": 1678888800000,
                "open": "100.0",
                "high": "110.0",
                "low": "90.0",
                "close": "105.0",
                "volume": "0",
            }
        ]
        instrument_id = InstrumentId(Symbol("BTC/USD"), Venue("KRAKEN"))
        bars = convert_ohlcv_to_bars(candles, instrument_id, "1-HOUR-LAST", price_precision=1)
        bar = bars[0]
        expected_bar_type = "BTC/USD.KRAKEN-1-HOUR-LAST-EXTERNAL"
        assert str(bar.bar_type) == expected_bar_type

    def test_timestamps_ms_to_ns(self):
        """Timestamps convert from milliseconds to nanoseconds."""
        candles = [
            {
                "time": 1678888800000,
                "open": "100.0",
                "high": "110.0",
                "low": "90.0",
                "close": "105.0",
                "volume": "0",
            }
        ]
        instrument_id = InstrumentId(Symbol("BTC/USD"), Venue("KRAKEN"))
        bars = convert_ohlcv_to_bars(candles, instrument_id, "1-HOUR-LAST")
        bar = bars[0]
        expected_ns = 1678888800000 * 1_000_000
        assert bar.ts_event == expected_ns
        assert bar.ts_init == expected_ns

    def test_prices_from_str(self):
        candles = [
            {
                "time": 1678888800000,
                "open": "24885.4",
                "high": "25039.43",
                "low": "24529.18",
                "close": "24793.87",
                "volume": "0",
            }
        ]
        instrument_id = InstrumentId(Symbol("BTC/USD"), Venue("KRAKEN"))
        bars = convert_ohlcv_to_bars(candles, instrument_id, "1-HOUR-LAST", price_precision=2)
        bar = bars[0]
        assert bar.open == Price.from_str("24885.4")
        assert bar.high == Price.from_str("25039.43")
        assert bar.low == Price.from_str("24529.18")
        assert bar.close == Price.from_str("24793.87")

    def test_volume_from_str(self):
        candles = [
            {
                "time": 1678888800000,
                "open": "100.0",
                "high": "110.0",
                "low": "90.0",
                "close": "105.0",
                "volume": "123.456",
            }
        ]
        instrument_id = InstrumentId(Symbol("BTC/USD"), Venue("KRAKEN"))
        bars = convert_ohlcv_to_bars(candles, instrument_id, "1-HOUR-LAST")
        bar = bars[0]
        assert bar.volume == Quantity.from_str("123.456")

    def test_empty_candles_returns_empty(self):
        instrument_id = InstrumentId(Symbol("BTC/USD"), Venue("KRAKEN"))
        bars = convert_ohlcv_to_bars([], instrument_id, "1-HOUR-LAST")
        assert bars == []

    def test_multiple_candles_ordered(self):
        candles = [
            {
                "time": 1678888800000,
                "open": "100.0",
                "high": "110.0",
                "low": "90.0",
                "close": "105.0",
                "volume": "0",
            },
            {
                "time": 1678892400000,
                "open": "105.0",
                "high": "115.0",
                "low": "95.0",
                "close": "110.0",
                "volume": "0",
            },
        ]
        instrument_id = InstrumentId(Symbol("BTC/USD"), Venue("KRAKEN"))
        bars = convert_ohlcv_to_bars(candles, instrument_id, "1-HOUR-LAST")
        assert len(bars) == 2
        assert bars[0].ts_event < bars[1].ts_event


class TestBuildCurrencyPair:
    """Test build_currency_pair: Kraken pair info → Nautilus CurrencyPair."""

    def test_creates_currency_pair(self):
        pair_info = {"pair_decimals": 1, "lot_decimals": 8}
        instrument_id = InstrumentId(Symbol("BTC/USD"), Venue("KRAKEN"))
        cp = build_currency_pair(
            pair_info,
            instrument_id,
            "BTC/USD",
            Decimal("0.0016"),
            Decimal("0.0026"),
        )
        assert isinstance(cp, CurrencyPair)
        assert str(cp.id) == "BTC/USD.KRAKEN"

    def test_precision_from_pair_info(self):
        pair_info = {"pair_decimals": 2, "lot_decimals": 6}
        instrument_id = InstrumentId(Symbol("ETH/USD"), Venue("KRAKEN"))
        cp = build_currency_pair(
            pair_info,
            instrument_id,
            "ETH/USD",
            Decimal("0.0016"),
            Decimal("0.0026"),
        )
        assert cp.price_precision == 2
        assert cp.size_precision == 6

    def test_fees_applied(self):
        pair_info = {"pair_decimals": 1, "lot_decimals": 8}
        instrument_id = InstrumentId(Symbol("BTC/USD"), Venue("KRAKEN"))
        cp = build_currency_pair(
            pair_info,
            instrument_id,
            "BTC/USD",
            Decimal("0.0020"),
            Decimal("0.0030"),
        )
        assert cp.maker_fee == Decimal("0.0020")
        assert cp.taker_fee == Decimal("0.0030")

    def test_currencies_from_pair(self):
        pair_info = {"pair_decimals": 1, "lot_decimals": 8}
        instrument_id = InstrumentId(Symbol("SOL/USD"), Venue("KRAKEN"))
        cp = build_currency_pair(
            pair_info,
            instrument_id,
            "SOL/USD",
            Decimal("0.0016"),
            Decimal("0.0026"),
        )
        assert str(cp.base_currency) == "SOL"
        assert str(cp.quote_currency) == "USD"


# ---------------------------------------------------------------------------
# T009: fetch_bars() Tests
# ---------------------------------------------------------------------------


class TestKrakenHistoricalClientFetchBars:
    """Test KrakenHistoricalClient.fetch_bars()."""

    @pytest.fixture
    def mock_futures_market(self):
        return MagicMock()

    @pytest.fixture
    def mock_spot_market(self):
        return MagicMock()

    @pytest.fixture
    def client(self, mock_futures_market, mock_spot_market):
        c = KrakenHistoricalClient()
        c._futures_market = mock_futures_market
        c._spot_market = mock_spot_market
        c._connected = True
        return c

    @pytest.mark.asyncio
    async def test_returns_bars_and_currency_pair(
        self, client, mock_futures_market, mock_spot_market
    ):
        mock_futures_market.get_ohlc.return_value = {
            "candles": [
                {
                    "time": 1678888800000,
                    "open": "24885.4",
                    "high": "25039.43",
                    "low": "24529.18",
                    "close": "24793.87",
                    "volume": "0",
                }
            ],
            "more_candles": False,
        }
        mock_spot_market.get_asset_pairs.return_value = {
            "XXBTZUSD": {"pair_decimals": 1, "lot_decimals": 8}
        }
        start = datetime(2023, 3, 15, tzinfo=timezone.utc)
        end = datetime(2023, 3, 16, tzinfo=timezone.utc)
        bars, cp = await client.fetch_bars("BTC/USD.KRAKEN", start, end, "1-HOUR-LAST")
        assert isinstance(bars, list)
        assert len(bars) == 1
        assert isinstance(bars[0], Bar)
        assert isinstance(cp, CurrencyPair)

    @pytest.mark.asyncio
    async def test_pagination_with_more_candles(
        self, client, mock_futures_market, mock_spot_market
    ):
        """When more_candles is True, client makes additional API calls."""
        mock_futures_market.get_ohlc.side_effect = [
            {
                "candles": [
                    {
                        "time": 1678888800000,
                        "open": "100.0",
                        "high": "110.0",
                        "low": "90.0",
                        "close": "105.0",
                        "volume": "0",
                    }
                ],
                "more_candles": True,
            },
            {
                "candles": [
                    {
                        "time": 1678892400000,
                        "open": "105.0",
                        "high": "115.0",
                        "low": "95.0",
                        "close": "110.0",
                        "volume": "0",
                    }
                ],
                "more_candles": False,
            },
        ]
        mock_spot_market.get_asset_pairs.return_value = {
            "XXBTZUSD": {"pair_decimals": 1, "lot_decimals": 8}
        }
        start = datetime(2023, 3, 15, tzinfo=timezone.utc)
        end = datetime(2023, 3, 16, tzinfo=timezone.utc)
        bars, cp = await client.fetch_bars("BTC/USD.KRAKEN", start, end, "1-HOUR-LAST")
        assert len(bars) == 2
        assert mock_futures_market.get_ohlc.call_count == 2

    @pytest.mark.asyncio
    async def test_connection_error_on_failure(self, client, mock_futures_market, mock_spot_market):
        mock_futures_market.get_ohlc.side_effect = Exception("API down")
        mock_spot_market.get_asset_pairs.return_value = {
            "XXBTZUSD": {"pair_decimals": 1, "lot_decimals": 8}
        }
        start = datetime(2023, 3, 15, tzinfo=timezone.utc)
        end = datetime(2023, 3, 16, tzinfo=timezone.utc)
        with pytest.raises(KrakenConnectionError):
            await client.fetch_bars("BTC/USD.KRAKEN", start, end, "1-HOUR-LAST")

    @pytest.mark.asyncio
    async def test_data_not_found_for_unknown_pair(
        self, client, mock_futures_market, mock_spot_market
    ):
        mock_futures_market.get_ohlc.return_value = {
            "candles": [],
            "more_candles": False,
        }
        mock_spot_market.get_asset_pairs.return_value = {}
        start = datetime(2023, 3, 15, tzinfo=timezone.utc)
        end = datetime(2023, 3, 16, tzinfo=timezone.utc)
        with pytest.raises(DataNotFoundError):
            await client.fetch_bars("UNKNOWN/USD.KRAKEN", start, end, "1-HOUR-LAST")


class TestKrakenHistoricalClientBarTypeMapping:
    """Test bar_type_spec → Kraken resolution mapping."""

    @pytest.fixture
    def client(self):
        c = KrakenHistoricalClient()
        c._futures_market = MagicMock()
        c._spot_market = MagicMock()
        c._connected = True
        return c

    def test_one_minute(self, client):
        assert client._map_resolution("1-MINUTE-LAST") == "1m"

    def test_five_minute(self, client):
        assert client._map_resolution("5-MINUTE-LAST") == "5m"

    def test_fifteen_minute(self, client):
        assert client._map_resolution("15-MINUTE-LAST") == "15m"

    def test_thirty_minute(self, client):
        assert client._map_resolution("30-MINUTE-LAST") == "30m"

    def test_one_hour(self, client):
        assert client._map_resolution("1-HOUR-LAST") == "1h"

    def test_four_hour(self, client):
        assert client._map_resolution("4-HOUR-LAST") == "4h"

    def test_one_day(self, client):
        assert client._map_resolution("1-DAY-LAST") == "1d"

    def test_unsupported_raises(self, client):
        with pytest.raises(ValueError, match="Unsupported bar type"):
            client._map_resolution("1-WEEK-LAST")


class TestKrakenHistoricalClientConnect:
    """Test client lifecycle: connect/disconnect/is_connected."""

    def test_not_connected_initially(self):
        client = KrakenHistoricalClient()
        assert client.is_connected is False

    @pytest.mark.asyncio
    async def test_connect_initializes_markets(self):
        with (
            patch("src.services.kraken_client.FuturesMarket") as mock_fm,
            patch("src.services.kraken_client.SpotMarket") as mock_sm,
        ):
            client = KrakenHistoricalClient()
            result = await client.connect()
            assert client.is_connected is True
            assert "connected" in result
            mock_fm.assert_called_once()
            mock_sm.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self):
        with (
            patch("src.services.kraken_client.FuturesMarket"),
            patch("src.services.kraken_client.SpotMarket"),
        ):
            client = KrakenHistoricalClient()
            await client.connect()
            assert client.is_connected is True
            await client.disconnect()
            assert client.is_connected is False


# ---------------------------------------------------------------------------
# T010: Rate Limiter Tests
# ---------------------------------------------------------------------------


class TestKrakenRateLimiter:
    """Test KrakenRateLimiter sliding window implementation."""

    def test_default_rate(self):
        limiter = KrakenRateLimiter()
        assert limiter.requests_per_second == 10

    def test_custom_rate(self):
        limiter = KrakenRateLimiter(requests_per_second=5)
        assert limiter.requests_per_second == 5

    @pytest.mark.asyncio
    async def test_within_limit_proceeds_immediately(self):
        limiter = KrakenRateLimiter(requests_per_second=10)
        start = datetime.now(timezone.utc)
        # Make 5 requests (within limit of 10)
        for _ in range(5):
            await limiter.acquire()
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        # Should complete nearly instantly
        assert elapsed < 0.5

    @pytest.mark.asyncio
    async def test_exceeding_limit_is_delayed(self):
        limiter = KrakenRateLimiter(requests_per_second=2)
        start = datetime.now(timezone.utc)
        # Make 3 requests (exceeds limit of 2)
        for _ in range(3):
            await limiter.acquire()
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        # Third request should wait ~1 second for window to expire
        assert elapsed >= 0.8

    @pytest.mark.asyncio
    async def test_window_resets_after_decay(self):
        limiter = KrakenRateLimiter(requests_per_second=2)
        # Fill the window
        await limiter.acquire()
        await limiter.acquire()
        # Wait for window to expire
        await asyncio.sleep(1.1)
        # Should proceed immediately now
        start = datetime.now(timezone.utc)
        await limiter.acquire()
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        assert elapsed < 0.5
