"""A spot crypto run's cost overrides: one Kraken Pro fee tier and a modelled spread.

The one definition ``backtest run --fee-tier/--spread-bps`` (CLI) and the research MCP's
``fee_tier``/``spread_bps`` share, so both refuse the same inputs and build the same run:

- **fee tier (M3)**: one Kraken Pro spot tier per run (decision C21); it must be a tier of the
  shipped schedule (``coin_specs.yaml``, tiers 1..17). Unset, the schedule's ``default_tier``
  (tier 2) applies.
- **spread (M5)**: replaces the coin's modelled full spread for the run (decision C4), in
  finite, non-negative basis points.

Both are part of the run's identity only when set (``BacktestRequest.crypto_fee_tier`` /
``crypto_spread_bps``) and are applied to the instrument (:func:`with_crypto_terms`), which
the fee model charges and ``config_snapshot.crypto_pairs`` records.
"""

from __future__ import annotations

import math
from decimal import Decimal

from src.models.backtest_request import BacktestRequest


class CryptoOverrideError(ValueError):
    """A fee tier or spread the run cannot take; the message names the field as given."""


def _require_coin(request: BacktestRequest, name: str) -> None:
    from src.services.crypto.coin_specs import coin_names
    from src.services.sizing_overrides import coin_for_symbol

    if coin_for_symbol(request.symbol) is None:
        raise CryptoOverrideError(
            f"{name} is for spot crypto only; '{request.symbol}' is not a coin with a spec "
            f"({', '.join(sorted(coin_names()))})."
        )


def apply_crypto_overrides(
    request: BacktestRequest,
    fee_tier: int | None,
    spread_bps: float | Decimal | None = None,
    *,
    names: tuple[str, str] = ("fee_tier", "spread_bps"),
) -> BacktestRequest:
    """The request charging ``fee_tier`` and ``spread_bps``; unchanged when both are ``None``.

    ``names`` are the two inputs as the caller's user wrote them (``--fee-tier``/
    ``--spread-bps`` for the CLI), so every message names what to fix.

    Raises:
        CryptoOverrideError: The tier is not in the schedule, the spread is negative or not
            finite, or the symbol names no coin spec.
    """
    tier_name, spread_name = names
    if fee_tier is not None:
        from src.services.crypto.coin_specs import get_fee_schedule

        tiers = sorted(get_fee_schedule().tiers)
        if isinstance(fee_tier, bool) or fee_tier not in tiers:
            raise CryptoOverrideError(
                f"{tier_name} must be a Kraken tier {tiers[0]}..{tiers[-1]}, got {fee_tier}"
            )
        _require_coin(request, tier_name)
        request = request.model_copy(update={"crypto_fee_tier": fee_tier})
    if spread_bps is not None:
        if isinstance(spread_bps, bool) or not math.isfinite(spread_bps) or spread_bps < 0:
            raise CryptoOverrideError(
                f"{spread_name} must be a finite number of bps >= 0, got {spread_bps}"
            )
        _require_coin(request, spread_name)
        # Through float, as click hands the CLI its value: 1 and 1.0 are saved alike ("1.0")
        spread = Decimal(str(float(spread_bps)))
        request = request.model_copy(update={"crypto_spread_bps": spread})
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
