"""``backtest run``'s spot crypto options: symbol forms, ``--fee-tier N``, ``--spread-bps X``.

- **Symbol forms (M5)**: against a named catalog, ``BTC``, ``BTCUSD``, ``BTC/USD``,
  ``XBTUSD`` and ``BTC/USD.KRAKEN`` all become the catalog ticker ``BTC``
  (:mod:`src.services.crypto.symbols`); a crypto-shaped symbol no spec ships is a usage error
  naming the shipped pairs. Crypto bars are ``LAST``, the default price type, so the bar type
  is unchanged. Other data sources (``--data-source kraken``) keep their own symbol form.
- **``--fee-tier N`` (M3)** and **``--spread-bps X`` (M5)**: the run's Kraken Pro spot fee
  tier and modelled spread, validated and applied by :mod:`src.services.crypto.cost_overrides`
  (shared with the research MCP's ``fee_tier``/``spread_bps``); a bad value is exit 2.
"""

from __future__ import annotations

import click

from src.models.backtest_request import BacktestRequest
from src.services.crypto.cost_overrides import (
    CryptoOverrideError,
    apply_crypto_overrides,
    with_crypto_terms,  # noqa: F401  # re-export: backtest helpers and tests import it here
)
from src.services.crypto.symbols import CryptoSymbolError, crypto_coin


def resolve_crypto_request(request: BacktestRequest) -> BacktestRequest:
    """The request with a crypto symbol's catalog ticker (``BTC/USD`` -> ``BTC``).

    Raises:
        click.UsageError: A crypto-shaped symbol that names no shipped pair.
    """
    if not request.catalog_name:
        return request
    try:
        coin = crypto_coin(request.symbol)
    except CryptoSymbolError as exc:
        raise click.UsageError(str(exc)) from None
    if coin is None or coin == request.symbol:
        return request
    update = {"symbol": coin, "instrument_id": f"{coin}.NAMED_CATALOG"}
    return request.model_copy(update=update)


def apply_crypto_options(
    request: BacktestRequest, fee_tier: int | None, spread_bps: float | None = None
) -> BacktestRequest:
    """The request with ``--fee-tier`` and ``--spread-bps`` applied.

    Raises:
        click.UsageError: The tier is not in the schedule, or the symbol names no coin spec.
    """
    try:
        return apply_crypto_overrides(
            request, fee_tier, spread_bps, names=("--fee-tier", "--spread-bps")
        )
    except CryptoOverrideError as exc:
        raise click.UsageError(str(exc)) from None
