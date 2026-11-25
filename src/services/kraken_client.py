"""Kraken client for historical **spot** crypto candles (``--data-source kraken``).

Crypto M7 (``openspec/changes/add-crypto-backtesting/design.md`` section 11): the client reads
Kraken's public spot REST ``OHLC`` endpoint, never the Futures Charts API (its symbols are
perpetuals and futures; its ``spot`` tick type is an index). That endpoint serves only the
latest 720 candles of an interval, so an older range is refused before any request
(:mod:`src.services.crypto.kraken_spot`); the instrument is the coin spec's
``BTC/USD.KRAKEN``. Both endpoints used (``OHLC``, ``AssetPairs``) are public: an API key is
optional.
"""

import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone

import structlog
from kraken.spot import Market as SpotMarket
from nautilus_trader.model.data import Bar
from nautilus_trader.model.instruments import CurrencyPair

from src.services.crypto import kraken_spot
from src.services.crypto.coin_specs import coin_names, get_coin_spec
from src.services.exceptions import DataNotFoundError, KrakenConnectionError

logger = structlog.get_logger(__name__)

# REST API: BTC→XBT, DOGE→XDG
SYMBOL_MAP = {"BTC": "XBT", "DOGE": "XDG"}
SYMBOL_MAP_REVERSE = {v: k for k, v in SYMBOL_MAP.items()}


# ---------------------------------------------------------------------------
# KrakenPairMapper
# ---------------------------------------------------------------------------


class KrakenPairMapper:
    """Maps between user pairs, Nautilus IDs, and Kraken spot REST names."""

    VENUE = "KRAKEN"

    @staticmethod
    def to_nautilus_id(user_pair: str) -> str:
        """Convert user pair to Nautilus ID. 'BTC/USD' → 'BTC/USD.KRAKEN'."""
        if "/" not in user_pair:
            raise ValueError(f"Invalid pair format: {user_pair!r}. Expected 'BASE/QUOTE'.")
        return f"{user_pair}.{KrakenPairMapper.VENUE}"

    @staticmethod
    def to_kraken_rest(user_pair: str) -> str:
        """The spot REST pair name: the coin spec's AssetPairs key ('BTC/USD' → 'XXBTZUSD',
        'SOL/USD' → 'SOLUSD'), else the altname form Kraken resolves ('DOGE/USD' → 'XDGUSD').
        """
        if "/" not in user_pair:
            raise ValueError(f"Invalid pair format: {user_pair!r}. Expected 'BASE/QUOTE'.")
        base, quote = user_pair.split("/")
        if base in coin_names() and get_coin_spec(base).kraken.pair == user_pair:
            return get_coin_spec(base).kraken.rest
        return f"{SYMBOL_MAP.get(base, base)}{quote}"

    @staticmethod
    def from_nautilus_id(nautilus_id: str) -> tuple[str, str]:
        """Parse Nautilus ID. 'BTC/USD.KRAKEN' → ('BTC/USD', 'KRAKEN')."""
        if "." not in nautilus_id:
            raise ValueError(
                f"Invalid Nautilus instrument ID: {nautilus_id!r}. Expected 'PAIR.VENUE'."
            )
        last_dot = nautilus_id.rfind(".")
        pair = nautilus_id[:last_dot]
        venue = nautilus_id[last_dot + 1 :]
        return pair, venue


# ---------------------------------------------------------------------------
# KrakenRateLimiter
# ---------------------------------------------------------------------------


class KrakenRateLimiter:
    """Sliding window rate limiter for Kraken API calls."""

    def __init__(self, requests_per_second: int = 10):
        self.requests_per_second = requests_per_second
        self.window = timedelta(seconds=1)
        self.requests: deque[datetime] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> datetime:
        """Wait until a request slot is available."""
        while True:
            sleep_time = 0.0
            async with self._lock:
                now = datetime.now(timezone.utc)
                while self.requests and self.requests[0] < now - self.window:
                    self.requests.popleft()
                if len(self.requests) < self.requests_per_second:
                    self.requests.append(now)
                    return now
                sleep_time = (self.requests[0] + self.window - now).total_seconds()
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)


# ---------------------------------------------------------------------------
# KrakenHistoricalClient
# ---------------------------------------------------------------------------


class KrakenHistoricalClient:
    """Client for fetching historical spot OHLCV candles from Kraken (public endpoints)."""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        rate_limit: int = 10,
    ):
        # Fees are not a client concern: runs charge the coin_specs.yaml tier (M3, --fee-tier).
        self._api_key = api_key
        self._api_secret = api_secret
        self._spot_market: SpotMarket | None = None
        self._connected = False
        self.rate_limiter = KrakenRateLimiter(requests_per_second=rate_limit)
        self._pair_info_cache: dict[str, dict] = {}

    def __repr__(self) -> str:
        return (
            f"KrakenHistoricalClient(connected={self._connected}, "
            f"rate_limit={self.rate_limiter.requests_per_second})"
        )

    def _validate_credentials(self) -> None:
        """A key without a secret (or the reverse) is a misconfiguration; none is fine.

        Raises:
            KrakenConnectionError: When only one of key and secret is set.
        """
        if self._api_key and not self._api_secret:
            raise KrakenConnectionError(
                "Kraken API secret not configured. Set the KRAKEN_API_SECRET environment variable."
            )
        if self._api_secret and not self._api_key:
            raise KrakenConnectionError(
                "Kraken API key not configured. Set the KRAKEN_API_KEY environment variable."
            )

    async def connect(self, timeout: int = 30) -> dict:
        """Initialize the Kraken spot market client (public data needs no key)."""
        self._validate_credentials()
        try:
            self._spot_market = SpotMarket(key=self._api_key, secret=self._api_secret)
            self._connected = True
            logger.info("kraken_client_connected")
            return {
                "connected": True,
                "connection_time": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            raise KrakenConnectionError(
                "Failed to connect to Kraken. Verify your credentials are correct."
            ) from e

    async def disconnect(self) -> None:
        """Clean up the Kraken SDK client."""
        self._spot_market = None
        self._connected = False
        logger.info("kraken_client_disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _market(self) -> SpotMarket:
        if self._spot_market is None:
            raise KrakenConnectionError("Kraken client not connected. Call connect() first.")
        return self._spot_market

    async def _fetch_pair_info(self, user_pair: str) -> dict | None:
        """Fetch and cache ``AssetPairs`` metadata (with its result ``key``), or ``None``."""
        if user_pair in self._pair_info_cache:
            return self._pair_info_cache[user_pair]
        rest_pair = KrakenPairMapper.to_kraken_rest(user_pair)
        try:
            await self.rate_limiter.acquire()
            all_pairs = await asyncio.to_thread(self._market().get_asset_pairs, pair=rest_pair)
        except KrakenConnectionError:
            raise
        except Exception as e:
            logger.warning("failed_to_fetch_pair_info", pair=user_pair, error=str(e))
            return None
        if not all_pairs:
            return None
        key, info = next(iter(all_pairs.items()))
        self._pair_info_cache[user_pair] = {**info, "key": key}
        return self._pair_info_cache[user_pair]

    async def _fetch_spot_ohlc(
        self, user_pair: str, interval: int, start: datetime
    ) -> tuple[list[list], int]:
        """One spot ``OHLC`` request: ``(rows, last)``; ``last`` is the newest committed time."""
        await self.rate_limiter.acquire()
        try:
            response = await asyncio.to_thread(
                self._market().get_ohlc,
                pair=KrakenPairMapper.to_kraken_rest(user_pair),
                interval=interval,
                since=int(start.timestamp()) - 1,
            )
        except KrakenConnectionError:
            raise
        except Exception as e:
            raise KrakenConnectionError(f"Failed to fetch OHLC from Kraken: {e}") from e
        rows: list[list] = next((v for k, v in response.items() if k != "last"), [])
        return rows, int(response.get("last", 0))

    async def fetch_bars(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
        bar_type_spec: str = "1-MINUTE-LAST",
        now: datetime | None = None,
    ) -> tuple[list[Bar], CurrencyPair]:
        """Fetch spot bars and the spot pair for ``BTC/USD.KRAKEN`` over ``[start, end]``.

        Raises:
            KrakenSpotRequestError: Before any request: ``start`` is older than the endpoint's
                720-candle window, or the pair, quote, interval or price type is not served.
            KrakenConnectionError: On API failure.
            DataNotFoundError: No committed candle in the range, or an unknown pair.
        """
        now = now or datetime.now(timezone.utc)
        user_pair, interval = kraken_spot.spot_request(
            instrument_id, bar_type_spec, start, end, now
        )
        known = user_pair.split("/")[0] in coin_names()
        pair_info = None if known else await self._fetch_pair_info(user_pair)
        instrument = kraken_spot.spot_instrument(user_pair, pair_info)
        rows, last = await self._fetch_spot_ohlc(user_pair, interval, start)
        bars = kraken_spot.spot_bars(
            rows,
            last=last,
            instrument_id=str(instrument.id),
            bar_type_spec=bar_type_spec,
            start=start,
            end=end,
            price_precision=instrument.price_precision,
            size_precision=instrument.size_precision,
        )
        if not bars:
            raise DataNotFoundError(instrument_id, start, end)
        logger.info("kraken_spot_bars_fetched", instrument_id=instrument_id, bar_count=len(bars))
        return bars, instrument

    async def fetch_asset_pairs(self) -> dict:
        """Fetch all asset pairs from Kraken Spot API."""
        return self._market().get_asset_pairs()


async def fetch_kraken_spot(
    instrument_id: str, start: datetime, end: datetime, bar_type_spec: str
) -> tuple[list[Bar], CurrencyPair]:
    """One ``--data-source kraken`` fetch with a ``KrakenSettings`` client (``fetch_bars``)."""
    from src.config import KrakenSettings

    settings = KrakenSettings()
    client = KrakenHistoricalClient(
        api_key=settings.kraken_api_key,
        api_secret=settings.kraken_api_secret,
        rate_limit=settings.kraken_rate_limit,
    )
    await client.connect()
    try:
        return await client.fetch_bars(instrument_id, start, end, bar_type_spec)
    finally:
        await client.disconnect()
