"""``backtest run``'s spot crypto options (crypto M3): ``--fee-tier N``.

One Kraken Pro spot fee tier per run (decision C21): ``N`` must be a tier of the shipped
schedule (``coin_specs.yaml``, tiers 1..17) and the symbol a coin with a spec, else a usage
error (exit 2). The tier is part of the run's identity (``BacktestRequest.crypto_fee_tier``)
and is applied to the instrument as ``info["fee_tier"]`` (:func:`with_fee_tier`), which the fee
model charges and ``config_snapshot.fee_tier`` records. Without the flag the schedule's
``default_tier`` (tier 2) applies. The ``BTC/USD`` symbol forms and ``--spread-bps`` are M5's.
"""

from __future__ import annotations

import click

from src.models.backtest_request import BacktestRequest


def apply_crypto_options(request: BacktestRequest, fee_tier: int | None) -> BacktestRequest:
    """The request with ``--fee-tier`` applied.

    Raises:
        click.UsageError: The tier is not in the schedule, or the symbol names no coin spec.
    """
    if fee_tier is None:
        return request
    from src.services.crypto.coin_specs import coin_names, get_fee_schedule

    tiers = sorted(get_fee_schedule().tiers)
    if fee_tier not in tiers:
        raise click.UsageError(
            f"--fee-tier must be a Kraken tier {tiers[0]}..{tiers[-1]}, got {fee_tier}"
        )
    if request.symbol.upper() not in coin_names():
        raise click.UsageError(
            f"--fee-tier is for spot crypto only; '{request.symbol}' is not a coin with a spec "
            f"({', '.join(sorted(coin_names()))})."
        )
    return request.model_copy(update={"crypto_fee_tier": fee_tier})


def with_fee_tier(instrument, fee_tier: int | None):
    """The crypto pair rebuilt to charge ``fee_tier``; unchanged when ``None``.

    Raises:
        ValueError: A tier for an instrument that is not a spec-built crypto pair.
    """
    if fee_tier is None:
        return instrument
    from src.core.position_sizing import is_crypto

    if not is_crypto(instrument):
        raise ValueError(
            f"a fee tier applies to spot crypto only; {getattr(instrument, 'id', instrument)} "
            "is not a crypto pair"
        )
    from src.services.crypto.coin_specs import get_coin_spec
    from src.services.crypto.instrument_builder import build_crypto_pair

    return build_crypto_pair(
        get_coin_spec(instrument.base_currency.code),
        nautilus_id=str(instrument.id),
        price_precision=instrument.price_precision,
        fee_tier=fee_tier,
    )
