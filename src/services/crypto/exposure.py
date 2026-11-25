"""Exact peak notional and exposure for a spot crypto run, replayed from its fill series.

Pure and framework-free. A crypto run saved since this change carries, per pair,
``config_snapshot.crypto_pairs[<id>].position_series`` (written by
:func:`src.core.crypto_costs.crypto_snapshot`): every fill on the pair in engine order as
``[seq, ts_ns, position_id, position_qty, fill_px, commission_usd]``. ``seq`` orders fills
across pairs, ``position_qty`` is the signed quantity of the fill's position after it (the
crypto venue is ``HEDGING``, so a pair can hold several positions; the pair's quantity is
their sum) and ``commission_usd`` is what the fill was charged (commission plus modelled spread).

Replaying the fills in ``seq`` order keeps each position's quantity and average open price
(Nautilus' ``avg_px_open``: adds re-average it, reductions keep it) and the USD balance (the
starting capital plus realised P&L net of costs, each fill's P&L in whole cents, as the
single-currency account books it; the replay agrees with the run's final balance within a
cent per fill, which :func:`src.core.crypto_costs.crypto_snapshot` checks before saving).
After every fill the run's notional is the sum over open positions of ``|quantity| x average
open price`` (cost basis, the figure the M5 upper bound uses per trade) and exposure is that
notional over the balance. A position opened and closed inside one bar counts while it was
held, because it was.

Runs saved without the series fall back to the M5 upper bound
(:func:`src.services.crypto.returns.summarise_run`); there is no backfill.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

#: The position-series entry fields, in order.
SERIES_FIELDS = ("seq", "ts_ns", "position_id", "position_qty", "fill_px", "commission_usd")
_ZERO = Decimal(0)
_CENT = Decimal("0.01")


def has_series(series: Mapping[str, Any]) -> bool:
    """True when every pair carries a recorded series (an empty list is a pair with no fills)."""
    return bool(series) and all(isinstance(s, list) for s in series.values())


@dataclass
class _Holding:
    """One position's signed quantity and average open price during the replay."""

    qty: Decimal = _ZERO
    avg: Decimal = _ZERO

    def fill(self, qty_after: Decimal, px: Decimal) -> Decimal:
        """Move to ``qty_after`` at ``px``; returns the P&L the fill realised (before costs)."""
        before, self.qty = self.qty, qty_after
        if qty_after == before:
            return _ZERO
        if before == 0 or ((before > 0) == (qty_after > 0) and abs(qty_after) > abs(before)):
            added = abs(qty_after) - abs(before)
            self.avg = (self.avg * abs(before) + px * added) / abs(qty_after)
            return _ZERO
        direction = 1 if before > 0 else -1
        if qty_after == 0 or (qty_after > 0) == (before > 0):  # reduced or closed
            realised = (px - self.avg) * (abs(before) - abs(qty_after)) * direction
            if qty_after == 0:
                self.avg = _ZERO
            return realised
        realised = (px - self.avg) * abs(before) * direction  # flipped through flat
        self.avg = px
        return realised

    @property
    def notional(self) -> Decimal:
        return abs(self.qty) * self.avg


@dataclass(frozen=True)
class Sample:
    """The run after one fill: its timestamp, open notional (USD) and USD balance."""

    ts_ns: int
    notional: Decimal
    balance: Decimal | None


def _fills(series: Mapping[str, Iterable[Any]]) -> list[tuple]:
    rows = [
        (seq, int(ts), (iid, str(pid)), Decimal(str(qty)), Decimal(str(px)), Decimal(str(fee)))
        for iid, entries in series.items()
        for seq, ts, pid, qty, px, fee in entries or []
    ]
    return sorted(rows, key=lambda r: r[0])


def replay(series: Mapping[str, Iterable[Any]], capital: Decimal | None) -> list[Sample]:
    """One :class:`Sample` per fill, in engine order (balance ``None`` without ``capital``)."""
    holdings: dict[tuple[str, str], _Holding] = {}
    notional, balance = _ZERO, capital
    samples = []
    for _, ts, key, qty_after, px, commission in _fills(series):
        holding = holdings.setdefault(key, _Holding())
        notional -= holding.notional
        # The account books each fill's realised P&L in whole cents
        realised = holding.fill(qty_after, px).quantize(_CENT, rounding=ROUND_HALF_EVEN)
        notional += holding.notional
        if balance is not None:
            balance += realised - commission
        samples.append(Sample(ts, notional, balance))
    return samples


def peaks(
    series: Mapping[str, Iterable[Any]], capital: Decimal | None
) -> tuple[Decimal | None, Decimal | None]:
    """Peak concurrent notional (USD) and peak notional / balance, sampled after every fill.

    ``(None, None)`` for a run with no fills; the exposure is ``None`` without ``capital``.
    """
    samples = replay(series, capital)
    if not samples:
        return None, None
    ratios = [s.notional / s.balance for s in samples if s.balance is not None and s.balance > 0]
    return max(s.notional for s in samples), max(ratios, default=None)
