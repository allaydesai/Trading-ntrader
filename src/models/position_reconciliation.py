"""The startup comparison between the session's cache and the broker (Story 4.2).

IBKR is the authority for what the account holds (architecture D2, FR35); the
engine cache is a copy that defers to it. This module is the pure half of the
check Story 4.2's ``reconcile`` phase runs before any strategy trades: given
the cache's open positions (converted at the boundary into
:class:`CachedPosition`) and Story 4.1's :class:`~src.models.broker_state.BrokerState`,
name every instrument on which they disagree. What to *do* about a
disagreement is ``src/core/live_startup_reconcile.py``'s.

**Two questions, not one** (decisions D-C and D-D). First, NFR9's: does the
cache's **net** signed quantity per instrument — summed over every open
position, every strategy — equal the broker's, exactly? That is the quantity
the broker reports and the quantity Nautilus's own reconciliation aligns.
Second: does any strategy's **own** position contradict the broker? Nautilus
resolves a disagreement by attributing a correcting fill to a synthetic owner
(``EXTERNAL`` or ``INTERNAL-DIFF``), and under NETTING that fill lands in the
synthetic owner's position, never the strategy's (measured against 1.220.0,
Story 4.2 Task 1). So a matching net can hide a strategy that believes it
holds shares the broker does not — and a started strategy acts on that belief.
A row answers both: ``kind`` is :data:`STRATEGY_POSITION` when the strategy's
own net is non-zero and differs from the broker's, else :data:`POSITION`.

Compared exactly, as ``Decimal``: no tolerance, no "close enough" (FR36).
Average price and cash are not compared here (Story 4.1's routing).

Stdlib only (plus the equally stdlib-only ``broker_state``), pinned by
``tests/unit/models/test_position_reconciliation.py`` — Story 4.6's service
layer reuses :func:`compare_positions` and must never import Nautilus (AR38).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from src.models.broker_state import BrokerState

#: The two strategy ids Nautilus's reconciliation stamps on what it generates:
#: ``EXTERNAL`` for an order the broker reports that the cache does not know,
#: ``INTERNAL-DIFF`` for a net-quantity correction
#: (``live/execution_engine.py:1708-1718``). Duplicated from
#: ``live_trade_recorder.RECONCILIATION_STRATEGY_IDS`` rather than imported
#: (``src.core`` is off limits here), and pinned equal to it by import in this
#: module's tests — a duplicated literal needs its own equality pin (CLAUDE.md).
SYNTHETIC_STRATEGY_IDS = frozenset({"EXTERNAL", "INTERNAL-DIFF"})

#: A strategy's own position contradicts the broker (decision D-D: refused).
STRATEGY_POSITION = "strategy_position"
#: Only the net disagrees; no strategy's own belief does (D-E: resolvable).
POSITION = "position"

_ZERO = Decimal(0)


def _require_decimal(name: str, value: object) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite, got {value}")
    return value


@dataclass(frozen=True, slots=True)
class CachedPosition:
    """One open position in the session's engine cache, as a domain value.

    Attributes:
        instrument_id: The Nautilus identifier, e.g. ``NVDA.NASDAQ``.
        strategy_id: The owning strategy's id, or one of
            :data:`SYNTHETIC_STRATEGY_IDS`.
        quantity: Signed: positive long, negative short. Never zero — a flat
            position is not open.
    """

    instrument_id: str
    strategy_id: str
    quantity: Decimal

    def __post_init__(self) -> None:
        if not self.instrument_id:
            raise ValueError("instrument_id is required")
        if not self.strategy_id:
            raise ValueError("strategy_id is required")
        if _require_decimal("quantity", self.quantity) == 0:
            raise ValueError("quantity must be non-zero; a flat position is not open")

    @property
    def is_synthetic(self) -> bool:
        """Whether reconciliation, not a strategy, owns this position."""
        return self.strategy_id in SYNTHETIC_STRATEGY_IDS


@dataclass(frozen=True, slots=True)
class PositionDiscrepancy:
    """One instrument on which the cache and the broker disagree.

    Attributes:
        instrument_id: The instrument, as either side names it.
        local_quantity: The cache's net over every open position.
        strategy_quantity: The part of ``local_quantity`` owned by strategies
            (every non-synthetic owner).
        broker_quantity: The broker's signed quantity; ``0`` when it holds none.
        broker_resolved: ``False`` when the broker's row could not be resolved
            to a Nautilus instrument (Story 4.1's ``IB-CONID-*`` fallback), so
            nothing can be reconciled against it.
    """

    instrument_id: str
    local_quantity: Decimal
    strategy_quantity: Decimal
    broker_quantity: Decimal
    broker_resolved: bool = True

    @property
    def strategy_contradicted(self) -> bool:
        """Whether a strategy holds a position the broker contradicts."""
        return self.strategy_quantity != 0 and self.strategy_quantity != self.broker_quantity

    @property
    def kind(self) -> str:
        """:data:`STRATEGY_POSITION` or :data:`POSITION`."""
        return STRATEGY_POSITION if self.strategy_contradicted else POSITION

    @property
    def difference(self) -> Decimal:
        """What the cache's net must move by to reach the broker's."""
        return self.broker_quantity - self.local_quantity


def compare_positions(
    cached: Iterable[CachedPosition], broker: BrokerState
) -> tuple[PositionDiscrepancy, ...]:
    """Every instrument on which the cache disagrees with the broker.

    Spans both sides — an instrument only the cache holds and one only the
    broker holds are each a row. Empty means 0 discrepancy (NFR9), on net and
    on every strategy's own position.

    Args:
        cached: The cache's open positions.
        broker: Story 4.1's read of the broker.

    Returns:
        One row per disagreeing instrument, sorted by instrument id.
    """
    net: dict[str, Decimal] = {}
    owned: dict[str, Decimal] = {}
    for position in cached:
        net[position.instrument_id] = net.get(position.instrument_id, _ZERO) + position.quantity
        if not position.is_synthetic:
            owned[position.instrument_id] = (
                owned.get(position.instrument_id, _ZERO) + position.quantity
            )
    held = {position.instrument_id: position for position in broker.positions}
    rows = []
    for instrument_id in sorted(net.keys() | held.keys()):
        broker_row = held.get(instrument_id)
        row = PositionDiscrepancy(
            instrument_id=instrument_id,
            local_quantity=net.get(instrument_id, _ZERO),
            strategy_quantity=owned.get(instrument_id, _ZERO),
            broker_quantity=_ZERO if broker_row is None else broker_row.quantity,
            broker_resolved=True if broker_row is None else broker_row.instrument_resolved,
        )
        if row.local_quantity != row.broker_quantity or row.strategy_contradicted:
            rows.append(row)
    return tuple(rows)


def count_synthetic(cached: Iterable[CachedPosition]) -> int:
    """How many open positions reconciliation, not a strategy, owns."""
    return sum(1 for position in cached if position.is_synthetic)


@dataclass(frozen=True, slots=True)
class StartupReconciliation:
    """What a successful ``reconcile`` phase established (Story 4.2).

    Its existence is the fact ``_phase_trading`` requires before it starts a
    strategy (decision D-I); its fields are what ``reconcile.ok`` renders.

    Attributes:
        broker: The broker's view the cache now matches exactly.
        framework_resolved: Disagreements between the pre-reconciliation
            snapshot and the broker that Nautilus's own pass (inside
            ``node:connect``) corrected.
        reconcile_resolved: Disagreements the phase itself corrected,
            broker-ward, through the framework's reconciliation entry point.
        open_orders: Working orders in the cache after reconciliation (AR25).
        synthetic_positions: Open positions reconciliation owns.
        elapsed_ms: How long the phase's own work took (NFR5).
    """

    broker: BrokerState
    framework_resolved: tuple[PositionDiscrepancy, ...]
    reconcile_resolved: tuple[PositionDiscrepancy, ...]
    open_orders: int
    synthetic_positions: int
    elapsed_ms: float

    @property
    def discrepancy_count(self) -> int:
        """Every discrepancy this phase logged and resolved."""
        return len(self.framework_resolved) + len(self.reconcile_resolved)
