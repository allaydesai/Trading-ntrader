"""Kraken client for historical crypto data fetching."""

import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import structlog
from kraken.futures import Market as FuturesMarket
from kraken.spot import Market as SpotMarket
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Currency, Price, Quantity

from src.services.exceptions import DataNotFoundError, KrakenConnectionError

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Symbol mapping constants
# ---------------------------------------------------------------------------

# REST API: BTC→XBT, DOGE→XDG
SYMBOL_MAP = {"BTC": "XBT", "DOGE": "XDG"}
SYMBOL_MAP_REVERSE = {v: k for k, v in SYMBOL_MAP.items()}

# Charts API: only BTC→XBT (DOGE stays DOGE)
CHARTS_SYMBOL_MAP = {"BTC": "XBT"}

# Legacy prefixed assets on Kraken REST: X prefix for crypto
LEGACY_CRYPTO_PREFIXED = {
    "XBT",
    "ETH",
    "LTC",
    "XRP",
    "XLM",
    "XDG",
    "ETC",
    "REP",
    "ZEC",
    "XMR",
    "MLN",
    "DASH",
}

# Legacy prefixed fiat on Kraken REST: Z prefix
LEGACY_FIAT_PREFIXED = {"USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF"}

# Bar type spec → Kraken Charts resolution
BAR_TYPE_MAP = {
    "1-MINUTE": "1m",
    "5-MINUTE": "5m",
    "15-MINUTE": "15m",
    "30-MINUTE": "30m",
    "1-HOUR": "1h",
    "4-HOUR": "4h",
    "12-HOUR": "12h",
    "1-DAY": "1d",
}

# Known Nautilus price type suffixes appended to bar type specs
PRICE_TYPE_SUFFIXES = {"LAST", "MID", "BID", "ASK"}


# ---------------------------------------------------------------------------
# KrakenPairMapper
# ---------------------------------------------------------------------------


class KrakenPairMapper:
    """Maps between user pairs, Nautilus IDs, and Kraken API formats."""

    VENUE = "KRAKEN"

    @staticmethod
    def to_nautilus_id(user_pair: str) -> str:
        """Convert user pair to Nautilus ID. 'BTC/USD' → 'BTC/USD.KRAKEN'."""
        if "/" not in user_pair:
            raise ValueError(f"Invalid pair format: {user_pair!r}. Expected 'BASE/QUOTE'.")
        return f"{user_pair}.{KrakenPairMapper.VENUE}"

    @staticmethod
    def to_kraken_rest(user_pair: str) -> str:
        """Convert user pair to Kraken REST format. 'BTC/USD' → 'XXBTZUSD'."""
        if "/" not in user_pair:
            raise ValueError(f"Invalid pair format: {user_pair!r}. Expected 'BASE/QUOTE'.")
        base, quote = user_pair.split("/")
        mapped_base = SYMBOL_MAP.get(base, base)
        rest_base = f"X{mapped_base}" if mapped_base in LEGACY_CRYPTO_PREFIXED else mapped_base
        rest_quote = f"Z{quote}" if quote in LEGACY_FIAT_PREFIXED else quote
        return f"{rest_base}{rest_quote}"

    @staticmethod
    def to_kraken_charts(user_pair: str) -> str:
        """Convert user pair to Charts API symbol. 'BTC/USD' → 'PF_XBTUSD'."""
        if "/" not in user_pair:
            raise ValueError(f"Invalid pair format: {user_pair!r}. Expected 'BASE/QUOTE'.")
        base, quote = user_pair.split("/")
        charts_base = CHARTS_SYMBOL_MAP.get(base, base)
        return f"PF_{charts_base}{quote}"

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
# Converter & instrument factory
# ---------------------------------------------------------------------------


def _detect_price_precision(candles: list[dict]) -> int:
    """Detect the maximum decimal precision from OHLCV price fields."""
    max_prec = 0
    for c in candles:
        for field in ("open", "high", "low", "close"):
            s = str(c[field])
            if "." in s:
                prec = len(s.rstrip("0").split(".")[1])
                if prec > max_prec:
                    max_prec = prec
    return max_prec


def convert_ohlcv_to_bars(
    candles: list[dict],
    instrument_id: InstrumentId,
    bar_type_spec: str,
    price_precision: int = 1,
    size_precision: int = 8,
) -> list[Bar]:
    """Convert Kraken OHLCV candles to Nautilus Bar objects."""
    if not candles:
        return []

    bar_type = BarType.from_str(f"{instrument_id}-{bar_type_spec}-EXTERNAL")
    price_fmt = f"{{:.{price_precision}f}}"
    size_fmt = f"{{:.{size_precision}f}}"
    bars = []
    for c in candles:
        ts_ns = c["time"] * 1_000_000  # ms → ns
        bar = Bar(
            bar_type=bar_type,
            open=Price.from_str(price_fmt.format(float(c["open"]))),
            high=Price.from_str(price_fmt.format(float(c["high"]))),
            low=Price.from_str(price_fmt.format(float(c["low"]))),
            close=Price.from_str(price_fmt.format(float(c["close"]))),
            volume=Quantity.from_str(size_fmt.format(float(c["volume"]))),
            ts_event=ts_ns,
            ts_init=ts_ns,
        )
        bars.append(bar)
    return bars


def build_currency_pair(
    pair_info: dict,
    instrument_id: InstrumentId,
    user_pair: str,
    maker_fee: Decimal,
    taker_fee: Decimal,
) -> CurrencyPair:
    """Build a Nautilus CurrencyPair from Kraken asset pair metadata."""
    base_str, quote_str = user_pair.split("/")
    price_prec = pair_info.get("pair_decimals", 1)
    size_prec = pair_info.get("lot_decimals", 8)
    price_inc = f"0.{'0' * (price_prec - 1)}1" if price_prec > 0 else "1"
    size_inc = f"0.{'0' * (size_prec - 1)}1" if size_prec > 0 else "1"

    return CurrencyPair(
        instrument_id=instrument_id,
        raw_symbol=Symbol(user_pair.replace("/", "")),
        base_currency=Currency.from_str(base_str),
        quote_currency=Currency.from_str(quote_str),
        price_precision=price_prec,
        size_precision=size_prec,
        price_increment=Price.from_str(price_inc),
        size_increment=Quantity.from_str(size_inc),
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        ts_event=0,
        ts_init=0,
    )


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
    """Client for fetching historical OHLCV data from Kraken."""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        rate_limit: int = 10,
        default_maker_fee: Decimal = Decimal("0.0016"),
        default_taker_fee: Decimal = Decimal("0.0026"),
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._default_maker_fee = default_maker_fee
        self._default_taker_fee = default_taker_fee
        self._futures_market: FuturesMarket | None = None
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
        """Validate API credentials before connecting.

        Raises:
            KrakenConnectionError: When credentials are missing or incomplete.
        """
        has_key = bool(self._api_key)
        has_secret = bool(self._api_secret)
        if not has_key and not has_secret:
            raise KrakenConnectionError(
                "Kraken API credentials not configured. "
                "Set KRAKEN_API_KEY and KRAKEN_API_SECRET environment variables."
            )
        if not has_key:
            raise KrakenConnectionError(
                "Kraken API key not configured. Set the KRAKEN_API_KEY environment variable."
            )
        if not has_secret:
            raise KrakenConnectionError(
                "Kraken API secret not configured. Set the KRAKEN_API_SECRET environment variable."
            )

    async def connect(self, timeout: int = 30) -> dict:
        """Initialize Kraken SDK market clients."""
        self._validate_credentials()
        try:
            self._futures_market = FuturesMarket(key=self._api_key, secret=self._api_secret)
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
        """Clean up Kraken SDK clients."""
        self._futures_market = None
        self._spot_market = None
        self._connected = False
        logger.info("kraken_client_disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _map_resolution(self, bar_type_spec: str) -> str:
        """Map Nautilus bar_type_spec to Kraken resolution string."""
        # Strip known price type suffix (e.g., "1-HOUR-LAST" → "1-HOUR")
        parts = bar_type_spec.rsplit("-", 1)
        if len(parts) > 1 and parts[1] in PRICE_TYPE_SUFFIXES:
            key = parts[0]
        else:
            key = bar_type_spec
        resolution = BAR_TYPE_MAP.get(key)
        if resolution is None:
            raise ValueError(
                f"Unsupported bar type: {bar_type_spec!r}. Supported: {list(BAR_TYPE_MAP.keys())}"
            )
        return resolution

    async def _fetch_pair_info(self, user_pair: str) -> dict | None:
        """Fetch and cache asset pair metadata from Kraken Spot API."""
        if user_pair in self._pair_info_cache:
            return self._pair_info_cache[user_pair]

        if self._spot_market is None:
            return None

        rest_pair = KrakenPairMapper.to_kraken_rest(user_pair)
        try:
            all_pairs = self._spot_market.get_asset_pairs(pair=rest_pair)
            if all_pairs:
                # Take first matching pair info
                pair_info = next(iter(all_pairs.values()))
                self._pair_info_cache[user_pair] = pair_info
                return pair_info
        except Exception as e:
            logger.warning("failed_to_fetch_pair_info", pair=user_pair, error=str(e))
        return None

    async def _paginated_fetch(
        self,
        charts_sym: str,
        resolution: str,
        from_ts: int,
        to_ts: int,
    ) -> list[dict]:
        """Fetch OHLCV candles with automatic pagination.

        Returns:
            List of raw candle dicts from the Kraken Charts API.

        Raises:
            KrakenConnectionError: On API failure or client not connected.
        """
        if self._futures_market is None:
            raise KrakenConnectionError("Kraken client not connected. Call connect() first.")

        all_candles: list[dict] = []
        try:
            while True:
                await self.rate_limiter.acquire()
                response = self._futures_market.get_ohlc(
                    tick_type="spot",
                    symbol=charts_sym,
                    resolution=resolution,
                    from_=from_ts,
                    to=to_ts,
                )
                candles = response.get("candles", [])
                all_candles.extend(candles)

                if not response.get("more_candles", False) or not candles:
                    break

                # Kraken candle "time" is in milliseconds; Charts API
                # from_/to parameters accept seconds. Convert and advance
                # 1 second past the last candle to avoid duplicates.
                last_time_ms = candles[-1]["time"]
                from_ts = (last_time_ms // 1000) + 1

                logger.debug(
                    "kraken_pagination",
                    fetched=len(all_candles),
                    next_from=from_ts,
                )
        except KrakenConnectionError:
            raise
        except Exception as e:
            raise KrakenConnectionError(f"Failed to fetch OHLCV from Kraken: {e}") from e

        return all_candles

    async def fetch_bars(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
        bar_type_spec: str = "1-MINUTE-LAST",
    ) -> tuple[list[Bar], CurrencyPair | None]:
        """Fetch historical bars from Kraken Charts API.

        Returns:
            Tuple of (bars, currency_pair).

        Raises:
            KrakenConnectionError: On API failure.
            DataNotFoundError: When no data returned for unknown pair.
        """
        user_pair, venue = KrakenPairMapper.from_nautilus_id(instrument_id)
        charts_sym = KrakenPairMapper.to_kraken_charts(user_pair)
        resolution = self._map_resolution(bar_type_spec)
        naut_instrument_id = InstrumentId(Symbol(user_pair), Venue(venue))

        pair_info = await self._fetch_pair_info(user_pair)

        all_candles = await self._paginated_fetch(
            charts_sym, resolution, int(start.timestamp()), int(end.timestamp())
        )

        if not all_candles:
            raise DataNotFoundError(instrument_id, start, end)

        # Detect actual price precision from candle data — Kraken's Charts API
        # often returns higher precision than pair_decimals metadata claims.
        metadata_prec = pair_info.get("pair_decimals", 1) if pair_info else 1
        data_prec = _detect_price_precision(all_candles)
        price_prec = max(metadata_prec, data_prec)

        size_prec = pair_info.get("lot_decimals", 8) if pair_info else 8
        bars = convert_ohlcv_to_bars(
            all_candles, naut_instrument_id, bar_type_spec, price_prec, size_prec
        )

        currency_pair = None
        if pair_info:
            # Override pair_decimals with detected precision so instrument matches bars
            adjusted_pair_info = {**pair_info, "pair_decimals": price_prec}
            currency_pair = build_currency_pair(
                adjusted_pair_info,
                naut_instrument_id,
                user_pair,
                self._default_maker_fee,
                self._default_taker_fee,
            )

        logger.info(
            "kraken_bars_fetched",
            instrument_id=instrument_id,
            bar_count=len(bars),
            resolution=resolution,
        )

        return bars, currency_pair

    async def fetch_asset_pairs(self) -> dict:
        """Fetch all asset pairs from Kraken Spot API."""
        if self._spot_market is None:
            raise KrakenConnectionError("Kraken client not connected.")
        return self._spot_market.get_asset_pairs()
