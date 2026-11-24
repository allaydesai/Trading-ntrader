"""Spot crypto symbol forms for run requests (crypto M5, design section 10).

``BTC``, ``BTC/USD``, ``BTCUSD``, ``BTC-USD``, ``XBTUSD`` (Kraken's altname), ``XXBTZUSD`` (its
REST name) and ``BTC/USD.KRAKEN`` (the Nautilus id) all name the coin ``BTC``, which is the
``firstrate-crypto`` catalog ticker. The aliases come from ``coin_specs.yaml`` (the coin, its
Kraken ``pair``, ``altname`` and ``rest``), so a new coin needs no edit here.

A symbol shaped like a crypto pair that no spec ships is refused (:class:`CryptoSymbolError`)
naming the shipped pairs: a known coin with another quote (``BTC/EUR``, ``BTCUSDT``), another
venue (``BTC/USD.BINANCE``), an unknown coin written as a USD pair or on ``KRAKEN``
(``DOGE/USD``), or an alias two coins share. Anything else (an equity such as ``ETHE``, an FX
pair, a futures root) is not crypto: :func:`crypto_coin` returns ``None`` and the caller's own
resolver handles it, so equity, futures and FX requests are unchanged.
"""

from __future__ import annotations

from functools import lru_cache

from src.services.crypto.coin_specs import load_coin_specs

VENUE = "KRAKEN"
QUOTE = "USD"
#: Quotes a crypto pair could be written in that no shipped spec trades (refused, not ignored).
_OTHER_QUOTES = frozenset({"USDT", "USDC", "DAI", "EUR", "GBP", "JPY", "CAD", "CHF", "AUD"})


class CryptoSymbolError(ValueError):
    """A crypto-shaped symbol names no shipped pair, or names more than one."""


@lru_cache(maxsize=1)
def _registry_aliases() -> tuple[dict[str, frozenset[str]], dict[str, str]]:
    """``({base alias: coins}, {full pair alias: coin})`` from the shipped specs."""
    bases: dict[str, set[str]] = {}
    pairs: dict[str, str] = {}
    for coin, spec in load_coin_specs().coins.items():
        bases.setdefault(coin, set()).add(coin)
        for name in (spec.kraken.altname, spec.kraken.rest):
            name = name.upper()
            pairs[name] = coin
            if name.endswith(QUOTE) and len(name) > len(QUOTE):
                bases.setdefault(name[: -len(QUOTE)], set()).add(coin)
    return {k: frozenset(v) for k, v in bases.items()}, pairs


def _aliases() -> dict[str, set[str]]:
    """Base aliases (``BTC``, ``XBT``, ``XXBTZ``...) and the coins each names."""
    return {k: set(v) for k, v in _registry_aliases()[0].items()}


def _shipped() -> list[str]:
    return sorted(load_coin_specs().coins)


def _refuse(symbol: str, why: str) -> CryptoSymbolError:
    pairs = ", ".join(f"{c}/{QUOTE}" for c in _shipped())
    return CryptoSymbolError(
        f"'{symbol}': {why}. Shipped crypto pairs: {pairs} on {VENUE} (write BTC, BTCUSD, "
        "BTC/USD, XBTUSD or BTC/USD.KRAKEN)."
    )


def _fx_currencies() -> frozenset[str]:
    from src.services.fx.pair_specs import pair_names

    return frozenset(c for t in pair_names() for c in (t[:3], t[3:]))


def _split(text: str, aliases: dict[str, set[str]]) -> tuple[str, str] | None:
    """``(base, quote)`` of a written pair, or ``None`` when it is not pair-shaped."""
    for sep in ("/", "-"):
        if sep in text:
            base, _, quote = text.partition(sep)
            return base, quote
    if text in aliases:
        return text, QUOTE  # a bare coin: BTC
    quotes = {QUOTE, *_OTHER_QUOTES, *aliases}
    for base in sorted(aliases, key=len, reverse=True):
        rest = text[len(base) :]
        if text.startswith(base) and rest in quotes:
            return base, rest
    return None


def crypto_coin(symbol: str) -> str | None:
    """The shipped coin (``BTC``) a symbol names; ``None`` when it is not a crypto symbol.

    Raises:
        CryptoSymbolError: The symbol is shaped like a crypto pair that no spec ships, or an
            alias names two coins.
    """
    text, shown = symbol.strip().upper(), symbol.strip()
    head, dot, venue = text.rpartition(".")
    if not dot:
        return _crypto(text, shown, strict=False)
    if venue == VENUE:
        return _crypto(head, shown, strict=True)
    try:  # another venue: refused only when the rest is crypto-shaped (BRK.B is an equity)
        if _crypto(head, shown, strict=False) is None:
            return None
    except CryptoSymbolError:
        pass
    raise _refuse(shown, f"crypto runs use the {VENUE} venue, not {venue}")


def _crypto(text: str, shown: str, *, strict: bool) -> str | None:
    pairs = _registry_aliases()[1]
    if text in pairs:
        return pairs[text]
    aliases = _aliases()
    split = _split(text, aliases)
    if split is None:
        if strict:  # DOGEUSD.KRAKEN: on the crypto venue, so a coin we do not ship
            raise _refuse(shown, f"unknown crypto coin {text.removesuffix(QUOTE) or text}")
        return None
    base, quote = split
    coins = aliases.get(base)
    if not coins:
        fx_pair = base in _fx_currencies() and quote in _fx_currencies()
        if strict or (quote == QUOTE and not fx_pair and "/" in text):
            raise _refuse(shown, f"unknown crypto coin {base}")
        return None
    if len(coins) > 1:
        raise _refuse(shown, f"ambiguous: {base} names {', '.join(sorted(coins))}")
    if quote != QUOTE:
        raise _refuse(shown, f"crypto pairs here are quoted in {QUOTE} only, not {quote}")
    return next(iter(coins))


def nautilus_id(coin: str) -> str:
    """``BTC/USD.KRAKEN``: the instrument id a coin's pair is built with (decision C2)."""
    return f"{coin.upper()}/{QUOTE}.{VENUE}"
