"""Unit tests for Kraken client: pair mapper, rate limiter, client lifecycle.

The spot fetch itself (crypto M7: spot OHLC, the 720-candle refusal, the spec-built pair) is
covered in ``tests/component/services/crypto/test_kraken_spot.py``; the Futures Charts
(perpetual) path and its converters were removed.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.services.exceptions import KrakenConnectionError
from src.services.kraken_client import (
    KrakenHistoricalClient,
    KrakenPairMapper,
    KrakenRateLimiter,
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
        # The coin spec's AssetPairs key (crypto M7); Kraken rejects SOLZUSD
        assert KrakenPairMapper.to_kraken_rest("SOL/USD") == "SOLUSD"

    def test_doge_usd(self):
        # No coin spec: the altname form, which Kraken resolves (XXDGZUSD is rejected)
        assert KrakenPairMapper.to_kraken_rest("DOGE/USD") == "XDGUSD"

    def test_ltc_eur(self):
        # Kraken resolves the altname LTCEUR to its key XLTCZEUR
        assert KrakenPairMapper.to_kraken_rest("LTC/EUR") == "LTCEUR"


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

    def test_doge_maps_to_xdg_in_rest(self):
        """DOGE maps to XDG (Kraken's asset code) in the REST pair name."""
        assert "XDG" in KrakenPairMapper.to_kraken_rest("DOGE/USD")

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


class TestKrakenHistoricalClientConnect:
    """Test client lifecycle: connect/disconnect/is_connected."""

    def test_not_connected_initially(self):
        client = KrakenHistoricalClient()
        assert client.is_connected is False

    @pytest.mark.asyncio
    async def test_connect_initializes_the_spot_market(self):
        with patch("src.services.kraken_client.SpotMarket") as mock_sm:
            client = KrakenHistoricalClient(api_key="test-key", api_secret="test-secret")
            result = await client.connect()
            assert client.is_connected is True
            assert "connected" in result
            mock_sm.assert_called_once_with(key="test-key", secret="test-secret")

    @pytest.mark.asyncio
    async def test_connect_without_credentials_uses_the_public_endpoints(self):
        """Spot OHLC and AssetPairs are public (crypto M7): no key is needed."""
        with patch("src.services.kraken_client.SpotMarket") as mock_sm:
            client = KrakenHistoricalClient()
            await client.connect()
            assert client.is_connected is True
            mock_sm.assert_called_once_with(key="", secret="")

    @pytest.mark.asyncio
    async def test_fetch_before_connect_is_a_connection_error(self):
        client = KrakenHistoricalClient()
        with pytest.raises(KrakenConnectionError, match="not connected"):
            await client.fetch_asset_pairs()

    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self):
        with patch("src.services.kraken_client.SpotMarket"):
            client = KrakenHistoricalClient(api_key="test-key", api_secret="test-secret")
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
