"""``backtest run``'s spot crypto options: symbol forms, ``--fee-tier N``, ``--spread-bps X``.

- **Symbol forms (M5)**: against a named catalog, ``BTC``, ``BTCUSD``, ``BTC/USD``,
  ``XBTUSD`` and ``BTC/USD.KRAKEN`` all become the catalog ticker ``BTC``
  (:mod:`src.services.crypto.symbols`); a crypto-shaped symbol no spec ships is a usage error
  naming the shipped pairs. Crypto bars are ``LAST``, the default price type, so the bar type
  is unchanged. Other data sources (``--data-source kraken``) keep their own symbol form.
- **``--fee-tier N`` (M3)**: one Kraken Pro spot fee tier per run (decision C21): ``N`` must
  be a tier of the shipped schedule (``coin_specs.yaml``, tiers 1..17), else exit 2. Without
  it the schedule's ``default_tier`` (tier 2) applies.
- **``--spread-bps X`` (M5)**: replaces the coin's modelled full spread for the run
  (decision C4), like FX's ``--spread-pips``.

Both are part of the run's identity only when set (``BacktestRequest.crypto_fee_tier`` /
``crypto_spread_bps``) and are applied to the instrument (:func:`with_crypto_terms`), which
the fee model charges and ``config_snapshot.crypto_pairs`` records.
"""

from __future__ import annotations

from decimal import Decimal

import click

from src.models.backtest_request import BacktestRequest
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


def _require_coin(request: BacktestRequest, flag: str) -> None:
    from src.services.crypto.coin_specs import coin_names
    from src.services.sizing_overrides import coin_for_symbol

    if coin_for_symbol(request.symbol) is None:
        raise click.UsageError(
            f"{flag} is for spot crypto only; '{request.symbol}' is not a coin with a spec "
            f"({', '.join(sorted(coin_names()))})."
        )


def apply_crypto_options(
    request: BacktestRequest, fee_tier: int | None, spread_bps: float | None = None
) -> BacktestRequest:
    """The request with ``--fee-tier`` and ``--spread-bps`` applied.

    Raises:
        click.UsageError: The tier is not in the schedule, or the symbol names no coin spec.
    """
    if fee_tier is not None:
        from src.services.crypto.coin_specs import get_fee_schedule

        tiers = sorted(get_fee_schedule().tiers)
        if fee_tier not in tiers:
            raise click.UsageError(
                f"--fee-tier must be a Kraken tier {tiers[0]}..{tiers[-1]}, got {fee_tier}"
            )
        _require_coin(request, "--fee-tier")
        request = request.model_copy(update={"crypto_fee_tier": fee_tier})
    if spread_bps is not None:
        _require_coin(request, "--spread-bps")
        request = request.model_copy(update={"crypto_spread_bps": Decimal(str(spread_bps))})
    return request


def with_crypto_terms(instrument, fee_tier: int | None, spread_bps: Decimal | None = None):
    """The crypto pair rebuilt to charge ``fee_tier`` and ``spread_bps``; unchanged when both
    are ``None``.

    Raises:
        ValueError: A tier or spread for an instrument that is not a spec-built crypto pair.
    """
    if fee_tier is None and spread_bps is None:
        return instrument
    from src.core.position_sizing import is_crypto

    if not is_crypto(instrument):
        raise ValueError(
            "a fee tier or spread applies to spot crypto only; "
            f"{getattr(instrument, 'id', instrument)} is not a crypto pair"
        )
    from src.services.crypto.coin_specs import get_coin_spec
    from src.services.crypto.instrument_builder import build_crypto_pair

    spec = get_coin_spec(instrument.base_currency.code)
    if spread_bps is not None:
        spec = spec.model_copy(update={"typical_spread_bps": spread_bps})
    tier = fee_tier if fee_tier is not None else instrument.info.get("fee_tier")
    return build_crypto_pair(
        spec,
        nautilus_id=str(instrument.id),
        price_precision=instrument.price_precision,
        fee_tier=tier,
    )
