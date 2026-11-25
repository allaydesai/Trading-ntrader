"""Kraken **spot** candles for ``--data-source kraken`` (crypto M7, design section 11).

Until M7 this path paged the Kraken *Futures* Charts API with the perpetual symbol
``PF_XBTUSD`` and wrapped those prices in a spot ``CurrencyPair``. The M7 spike (2026-11-24,
public endpoints, no API key) found that the Charts API lists only futures symbols under every
tick type, and its ``spot`` tick type is the futures' reference index (no volume), not Kraken
spot trades. Kraken's spot REST ``OHLC`` endpoint
(<https://docs.kraken.com/api/docs/rest-api/get-ohlc-data>) returns only the latest
:data:`REST_WINDOW_CANDLES` candles of an interval plus the still-open one (whose timestamp is
the response's ``last``), whatever ``since`` asks for.

So this path reads spot ``OHLC`` and refuses, before any request, a range that starts before
that window, pointing at the same-venue history instead (``import --format kraken`` into the
``kraken-crypto`` catalog). The instrument is the coin spec's ``BTC/USD.KRAKEN``
(:func:`build_crypto_pair`, so the venue gets ``CryptoCommissionModel`` at the run's tier); a
USD coin without a spec gets a fallback spec from Kraken's ``AssetPairs`` metadata, flagged
UNVERIFIED, on the same fee schedule.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal

from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Price, Quantity

from src.services.crypto.coin_specs import QUOTE_CURRENCY, CoinSpec, coin_names, get_coin_spec
from src.services.crypto.instrument_builder import build_crypto_pair
from src.services.exceptions import DataNotFoundError, KrakenSpotRequestError

#: Bar step -> Kraken OHLC ``interval`` (minutes). Kraken has no 12-hour spot interval.
SPOT_INTERVALS = {
    "1-MINUTE": 1,
    "5-MINUTE": 5,
    "15-MINUTE": 15,
    "30-MINUTE": 30,
    "1-HOUR": 60,
    "4-HOUR": 240,
    "1-DAY": 1440,
}
#: Committed candles the spot OHLC endpoint returns (Kraken API docs: "720 data points").
REST_WINDOW_CANDLES = 720
#: Modelled spread of a coin with no spec (UNVERIFIED; the widest shipped default, SOL's).
FALLBACK_SPREAD_BPS = "5"
SIZE_PRECISION = 8
_PRICE_TYPES = {"LAST"}
_HISTORY_HINT = (
    "For older ranges use Kraken's own history: "
    "`uv run python -m src.services.crypto.kraken_ohlcvt_download --dest DIR XBTUSD`, then "
    "`import --format kraken --asset-class crypto --catalog kraken-crypto DIR` and "
    "`backtest run --catalog kraken-crypto ...`."
)


def spot_interval(bar_type_spec: str) -> int:
    """``1-HOUR-LAST`` (or ``1-HOUR``) -> 60.

    Raises:
        ValueError: Kraken publishes no such interval, or the price type is not ``LAST``
            (Kraken candles are trade prices).
    """
    parts = bar_type_spec.upper().split("-")
    step, price_type = "-".join(parts[:2]), (parts[2] if len(parts) > 2 else "LAST")
    if step not in SPOT_INTERVALS or price_type not in _PRICE_TYPES:
        raise ValueError(
            f"Kraken spot OHLC has no {bar_type_spec!r} series: intervals "
            f"{', '.join(SPOT_INTERVALS)}, trade prices (LAST) only"
        )
    return SPOT_INTERVALS[step]


def rest_window_start(interval: int, now: datetime) -> datetime:
    """Open of the oldest committed candle the spot OHLC endpoint serves at ``now``."""
    step = interval * 60
    open_now = int(now.timestamp()) // step * step  # the still-open candle
    return datetime.fromtimestamp(open_now - REST_WINDOW_CANDLES * step, tz=timezone.utc)


def spot_request(
    instrument_id: str, bar_type_spec: str, start: datetime, end: datetime, now: datetime
) -> tuple[str, int]:
    """``("BTC/USD", 60)`` for a request Kraken spot can serve, checked before any request.

    Raises:
        KrakenSpotRequestError: The id is not ``BASE/QUOTE.KRAKEN``, the quote is not USD
            (decision C6), Kraken publishes no such interval or price type, or ``start`` is
            older than the endpoint's window.
    """
    pair = instrument_id.rpartition(".")[0] or instrument_id
    try:
        if pair.count("/") != 1:
            raise ValueError(f"Kraken pairs are BASE/QUOTE (e.g. BTC/USD), got {pair!r}")
        if pair.split("/")[1] != QUOTE_CURRENCY:
            raise ValueError(f"Spot crypto backtests are USD-quoted only (C6); got {pair}")
        interval = spot_interval(bar_type_spec)
    except ValueError as e:
        raise KrakenSpotRequestError(instrument_id, start, end, message=str(e)) from e
    check_spot_range(instrument_id, start, end, interval, now)
    return pair, interval


def check_spot_range(
    instrument_id: str, start: datetime, end: datetime, interval: int, now: datetime
) -> None:
    """Raise :class:`KrakenSpotRequestError` when ``start`` is older than the REST window."""
    first = rest_window_start(interval, now)
    if start < first:
        raise KrakenSpotRequestError(
            instrument_id,
            start,
            end,
            message=(
                f"Kraken's spot OHLC endpoint serves only the latest {REST_WINDOW_CANDLES} "
                f"{interval}-minute candles of {instrument_id} (from {first:%Y-%m-%d %H:%M} "
                f"UTC); {start:%Y-%m-%d %H:%M} is older. {_HISTORY_HINT}"
            ),
        )


def _quantized(value: str, places: int) -> str:
    return str(Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN))


def spot_bars(
    rows: list[list],
    *,
    last: int,
    instrument_id: str,
    bar_type_spec: str,
    start: datetime,
    end: datetime,
    price_precision: int,
    size_precision: int = SIZE_PRECISION,
) -> list[Bar]:
    """Committed rows ``[time, o, h, l, c, vwap, volume, count]`` inside ``[start, end]``."""
    step = "-".join(bar_type_spec.upper().split("-")[:2])
    bar_type = BarType.from_str(f"{instrument_id}-{step}-LAST-EXTERNAL")
    lo, hi = int(start.timestamp()), int(end.timestamp())
    bars = []
    for t, o, h, low, c, _vwap, volume, _count in rows:
        if int(t) >= last or not lo <= int(t) <= hi:
            continue
        ts = int(t) * 1_000_000_000
        o, h, low, c = (Price.from_str(_quantized(v, price_precision)) for v in (o, h, low, c))
        size = Quantity.from_str(_quantized(volume, size_precision))
        bars.append(Bar(bar_type, o, h, low, c, size, ts, ts))
    return bars


def _decimals(text: str) -> int:
    return len(text.split(".")[1].rstrip("0")) if "." in text else 0


def fallback_coin_spec(base: str, pair_info: dict) -> CoinSpec:
    """A spec for a USD coin with none shipped, from Kraken ``AssetPairs`` (all UNVERIFIED)."""
    source = {
        "status": "UNVERIFIED",
        "source": "Kraken AssetPairs at fetch time; no coin spec (fallback)",
        "as_of": datetime.now(timezone.utc).date().isoformat(),
    }
    fields = ("price_precision", "size_increment", "typical_spread_bps", "kraken_rest")
    more = ("kraken_altname", "kraken_min_order_base", "kraken_min_order_usd", "kraken_tick_size")
    return CoinSpec.model_validate(
        {
            "coin": base,
            "base": base,
            "quote": QUOTE_CURRENCY,
            "data": "none",
            "price_precision": max(
                int(pair_info["pair_decimals"]), _decimals(str(pair_info["tick_size"]))
            ),
            "size_precision": SIZE_PRECISION,
            "size_increment": "0.00000001",
            "typical_spread_bps": FALLBACK_SPREAD_BPS,
            "kraken": {
                "pair": f"{base}/{QUOTE_CURRENCY}",
                "rest": pair_info.get("key", pair_info["altname"]),
                "altname": pair_info["altname"],
                "min_order_base": pair_info["ordermin"],
                "min_order_usd": pair_info["costmin"],
                "tick_size": pair_info["tick_size"],
                "discounted_pair": False,
            },
            "sources": {f: source for f in (*fields, *more, "kraken_discounted_pair")},
        }
    )


def spot_instrument(user_pair: str, pair_info: dict | None) -> CurrencyPair:
    """The spot pair for ``BTC/USD``: spec-built, or a fallback spec from ``pair_info``.

    Raises:
        ValueError: The quote is not USD (decision C6).
        DataNotFoundError: No coin spec and no ``AssetPairs`` metadata for the pair.
    """
    base, quote = user_pair.split("/")
    if quote != QUOTE_CURRENCY:
        raise ValueError(f"Spot crypto backtests are USD-quoted only (C6); got {user_pair}")
    if base in coin_names():
        return build_crypto_pair(get_coin_spec(base))
    if not pair_info:
        now = datetime.now(timezone.utc)
        raise DataNotFoundError(
            f"{user_pair}.KRAKEN",
            now - timedelta(days=1),
            now,
            message=f"Kraken lists no spot pair {user_pair} (AssetPairs) and it has no coin spec",
        )
    return build_crypto_pair(fallback_coin_spec(base, pair_info))
