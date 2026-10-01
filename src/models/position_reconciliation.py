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
A row answers both: ``kind`` is :data:`STRATEGY_POSITION` when the broker does
not **cover** the strategy's own net — it holds less in the strategy's
direction, none, or the opposite side — else :data:`POSITION`.

**Coverage, not equality** (Story 4.7, PO ruling A, 2026-09-28). A corporate
action that grows a held position — a forward split, a stock dividend — leaves
the broker holding *more* than the strategy believes, on the same side. That is
absorbed, not refused: the net moves broker-ward into a synthetic owner and the
strategy keeps its own lot, which the broker still covers, so the strategy
closing it can never cross the account through zero (NFR14). A reverse split, a
cash merger or a symbol change leaves the broker holding less, none or another
instrument, and stays refused; :attr:`PositionDiscrepancy.likely_cause` names
which, for the refusal's own message.

Compared exactly, as ``Decimal``: no tolerance, no "close enough" (FR36).
Average price is not compared here (Story 4.1's routing). Cash is never a
discrepancy either: :func:`cash_changes` only *names* cash that differs from
what the previous run last recorded, against the pre-run
:class:`LocalSnapshot` (Story 4.7, D-B) — informational, never a refusal.

Stdlib only (plus the equally stdlib-only ``broker_state``), pinned by
``tests/unit/models/test_position_reconciliation.py`` — Story 4.6's service
layer reuses :func:`compare_positions` and must never import Nautilus (AR38).
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.models.broker_state import BrokerState, CashBalance, is_currency_code

#: The two strategy ids Nautilus's reconciliation stamps on what it generates:
#: ``EXTERNAL`` for an order the broker reports that the cache does not know,
#: ``INTERNAL-DIFF`` for a net-quantity correction
#: (``live/execution_engine.py:1708-1718``). Duplicated from
#: ``live_trade_recorder.RECONCILIATION_STRATEGY_IDS`` rather than imported
#: (``src.core`` is off limits here), and pinned equal to it by import in this
#: module's tests — a duplicated literal needs its own equality pin (CLAUDE.md).
SYNTHETIC_STRATEGY_IDS = frozenset({"EXTERNAL", "INTERNAL-DIFF"})

#: The broker does not cover a strategy's own position (D-D, narrowed by Story
#: 4.7's coverage rule): refused.
STRATEGY_POSITION = "strategy_position"
#: Only the net disagrees; no strategy's own belief does (D-E: resolvable).
POSITION = "position"

#: The likely cause a refusal names (Story 4.7, PO ruling), by the broker's shape.
CAUSE_SHRANK = (
    "the broker holds fewer shares than the strategy believes — a reverse split, a partial sale "
    "outside the session, or a lost fill"
)
CAUSE_FLAT = (
    "the broker holds none — a cash merger, a symbol change, or a close outside the session"
)
CAUSE_OPPOSITE = "the broker holds the opposite side — a trade outside the session"
CAUSE_MIXED = (
    "this session's strategies hold both sides of the instrument, so only an exact match with "
    "the broker is accepted — a trade outside the session, or a corporate action"
)
#: Appended when the cache's net already equals the broker's (code review,
#: 2026-09-28): the disagreement is then between the strategy's own lot and the
#: reconciliation-owned positions beside it — e.g. a strategy that sold shares
#: a split had added — not necessarily an event at the broker.
CAUSE_NET_AGREES = (
    "; or, because the session's net already matches the broker, this session's own orders "
    "against a reconciliation-owned position on this instrument (see Story 4.5)"
)

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
        strategy_mixed_sides: ``True`` when the strategies' own lots on this
            instrument are on both sides (one long, another short), so their
            net says nothing about whether any one lot is covered.
        broker_symbol: The broker's own symbol for its row (IB's
            ``contract.symbol``), or ``None`` when the broker holds none. What
            an unresolved ``IB-CONID-*`` row still says about *which* holding
            it is — the runtime cycle holds back only the cache row with the
            same symbol (PR #35 code review, D1 ruling).
    """

    instrument_id: str
    local_quantity: Decimal
    strategy_quantity: Decimal
    broker_quantity: Decimal
    broker_resolved: bool = True
    strategy_mixed_sides: bool = False
    broker_symbol: str | None = None

    @property
    def symbol_key(self) -> str:
        """The instrument's symbol as the broker would spell it, normalised for
        matching: IB writes class shares ``BRK B`` where Nautilus writes
        ``BRK-B``, so spaces, hyphens and dots are dropped and case ignored."""
        return symbol_key(self.instrument_id.split(".", 1)[0])

    @property
    def strategy_contradicted(self) -> bool:
        """Whether a strategy holds a position the broker does not cover.

        Story 4.7's coverage rule (PO ruling A, 2026-09-28): the broker holds
        less in the strategy's direction, none, or the opposite side. Growth
        in the strategy's direction — a forward split, a stock dividend — is
        not a contradiction: the strategy closing its own lot leaves the broker
        at ``broker − strategy``, on the broker's own side, so *that* close can
        never carry the account through zero into a position nobody asked for.
        Strategies on both sides of one instrument keep the pre-4.7 equality
        rule: their net cannot tell whether each lot is covered (code review).
        Exact equality, with no ``strategy != 0`` guard: lots exist whenever
        the sides are mixed, so a net of zero is two open lots, not "no
        position" — A +10 and B −10 against a broker at +5 is refused, or the
        correction lands in a synthetic +5 and A closing its 10 carries the
        account short 5 (code review of PR #35).
        """
        strategy, broker = self.strategy_quantity, self.broker_quantity
        if self.strategy_mixed_sides:
            return strategy != broker
        return (strategy > 0 and broker < strategy) or (strategy < 0 and broker > strategy)

    @property
    def likely_cause(self) -> str | None:
        """What a refused row most likely means, by the broker's shape — and, when
        the net already agrees, the session's own orders too; ``None`` when the
        row is not refused (Story 4.7, PO ruling)."""
        if not self.strategy_contradicted:
            return None
        if self.strategy_mixed_sides:
            cause = CAUSE_MIXED
        elif self.broker_quantity == 0:
            cause = CAUSE_FLAT
        elif (self.broker_quantity > 0) != (self.strategy_quantity > 0):
            cause = CAUSE_OPPOSITE
        else:
            cause = CAUSE_SHRANK
        if self.local_quantity == self.broker_quantity:
            return f"{cause}{CAUSE_NET_AGREES}"
        return cause

    @property
    def broker_covers_strategy(self) -> bool:
        """Whether the broker holds everything the strategies own, on the same
        side, **and more** (Story 4.5, PO ruling 2026-09-28).

        The strategies' position is real, and the excess belongs to no
        strategy. Since Story 4.7's coverage rule such a row is contradicted
        only when the strategies hold both sides (:attr:`strategy_mixed_sides`,
        the equality rule); otherwise it is an ordinary row, absorbed at
        startup and at runtime alike. Only the *startup* phase reads this — it
        leaves the excess to the per-strategy resume check, which refuses the
        strategies on that instrument — and the running session's cycle still
        stops on a mixed-sides row, unchanged (integration merge, 2026-09-28).
        """
        if self.strategy_quantity == 0 or self.broker_quantity == 0:
            return False
        same_side = (self.strategy_quantity > 0) == (self.broker_quantity > 0)
        return same_side and abs(self.broker_quantity) > abs(self.strategy_quantity)

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
    sides: dict[str, set[bool]] = {}
    for position in cached:
        net[position.instrument_id] = net.get(position.instrument_id, _ZERO) + position.quantity
        if not position.is_synthetic:
            owned[position.instrument_id] = (
                owned.get(position.instrument_id, _ZERO) + position.quantity
            )
            sides.setdefault(position.instrument_id, set()).add(position.quantity > 0)
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
            strategy_mixed_sides=len(sides.get(instrument_id, ())) > 1,
            broker_symbol=None if broker_row is None else broker_row.symbol,
        )
        if row.local_quantity != row.broker_quantity or row.strategy_contradicted:
            rows.append(row)
    return tuple(rows)


def symbol_key(symbol: str | None) -> str:
    """A symbol normalised for matching across the broker's and Nautilus's
    spellings (``BRK B`` / ``BRK-B`` / ``BRK.B`` → ``BRKB``); ``""`` for none."""
    if not symbol:
        return ""
    return "".join(ch for ch in symbol.upper() if ch not in " -.")


def count_synthetic(cached: Iterable[CachedPosition]) -> int:
    """How many open positions reconciliation, not a strategy, owns."""
    return sum(1 for position in cached if position.is_synthetic)


def strategies_on_both_sides(
    cached: Iterable[CachedPosition],
    instrument_id: str,
    *,
    session_strategy_ids: Collection[str] | None = None,
) -> bool:
    """Whether the session's own lots on ``instrument_id`` are long *and* short.

    Their net then says nothing about whether any one lot is covered (the
    mixed-sides rule, Story 4.7's code review), so the resume check must not
    read same-side growth off it: A +20 and B −10 net +10 beside an unowned +5
    against a broker at +15 looks like covered growth, yet A closing its 20
    carries the account through zero. Ownership follows :func:`split_by_owner`.
    """
    sides = set()
    for position in cached:
        if position.instrument_id != instrument_id or position.is_synthetic:
            continue
        if session_strategy_ids is not None and position.strategy_id not in session_strategy_ids:
            continue
        sides.add(position.quantity > 0)
    return len(sides) > 1


def split_by_owner(
    cached: Iterable[CachedPosition],
    instrument_id: str,
    *,
    session_strategy_ids: Collection[str] | None = None,
) -> tuple[Decimal, Decimal]:
    """The strategies' signed net on ``instrument_id``, and the part no strategy owns.

    Story 4.5 (D-C): a holding reconciliation imported because no strategy's
    position explains it — a manual trade, an engine cache that lost it — is
    the **unowned** part. Synthetic positions that net to zero (the triple a
    pre-4.5 restart left, ``EXTERNAL +N / INTERNAL-DIFF −N``) own nothing.

    ``session_strategy_ids`` (PR #35 code review, D4 ruling): when given, a
    position under a strategy id **this session will not start** — one a
    pre-fix run left under an id no spec entry resolves to any more — is
    unowned too. Nothing of this session will manage it, and the strategy that
    resolves to the new id would otherwise read its own book as flat and enter
    beside it (FR38). ``None`` keeps Story 4.5's rule: every non-synthetic id
    is owned.

    Returns:
        ``(owned, unowned)``, each exact.
    """
    owned = unowned = _ZERO
    for position in cached:
        if position.instrument_id != instrument_id:
            continue
        stale = (
            session_strategy_ids is not None and position.strategy_id not in session_strategy_ids
        )
        if position.is_synthetic or stale:
            unowned += position.quantity
        else:
            owned += position.quantity
    return owned, unowned


def _tuple_of(name: str, values: object, member_type: type) -> None:
    if not isinstance(values, tuple) or not all(isinstance(v, member_type) for v in values):
        raise TypeError(f"{name} must be a tuple of {member_type.__name__}")


@dataclass(frozen=True, slots=True)
class LocalSnapshot:
    """What the session's cache held before Nautilus's own pass touched it.

    Taken at one instant — immediately before ``node.run_async()``, while the
    cache holds only what Redis restored (Story 4.2 D-F). Diagnostic only: it
    lets the ``reconcile`` phase name what changed, never decide anything.

    Attributes:
        positions: The cache's open positions (Story 4.2).
        cash: The previous run's last-recorded ``TotalCashValue`` per currency
            (Story 4.7, D-B). ``()`` means none was recorded — unknown, never
            zero — so nothing is compared.
        cash_recorded_at: When the broker last reported that cash — the
            restored account's last reported ``AccountState`` — or ``None``.
    """

    positions: tuple[CachedPosition, ...]
    cash: tuple[CashBalance, ...] = ()
    cash_recorded_at: datetime | None = None

    def __post_init__(self) -> None:
        _tuple_of("positions", self.positions, CachedPosition)
        _tuple_of("cash", self.cash, CashBalance)
        currencies = [balance.currency for balance in self.cash]
        if len(set(currencies)) != len(currencies):
            raise ValueError(f"one row per currency, got {sorted(currencies)}")
        if self.cash_recorded_at is not None and self.cash_recorded_at.utcoffset() is None:
            raise ValueError("cash_recorded_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class CashChange:
    """One currency whose cash differs from what the previous run last recorded (D-B).

    Not a discrepancy — the running session's cash *is* the broker's push, so
    nothing is resolved — but a named observation: a dividend, interest, a fee,
    or another client's trade (FR39). IBKR pushes the summary only every few
    minutes, so the previous run's own last fills or commissions can appear
    here too; ``recorded_at`` says since when (code review, 2026-09-28).

    Attributes:
        currency: A three-letter code.
        before: The previous run's last-recorded cash, or ``None`` when that
            run recorded none in this currency.
        after: The broker's cash now, or ``None`` when it reports none.
    """

    currency: str
    before: Decimal | None
    after: Decimal | None

    def __post_init__(self) -> None:
        if not is_currency_code(self.currency):
            raise ValueError(f"currency must be a three-letter code, got {self.currency!r}")
        if self.before is None and self.after is None:
            raise ValueError("a cash change needs at least one side")
        for name, value in (("before", self.before), ("after", self.after)):
            if value is not None:
                _require_decimal(name, value)

    @property
    def difference(self) -> Decimal | None:
        """``after − before``, or ``None`` when either side is unknown."""
        if self.before is None or self.after is None:
            return None
        return self.after - self.before


def cash_changes(before: LocalSnapshot, broker: BrokerState) -> tuple[CashChange, ...]:
    """Every currency whose cash differs, exactly, from what the previous run
    recorded; ``()`` when it recorded none (a fresh session — no before)."""
    if not before.cash:
        return ()
    recorded = {balance.currency: balance.total_cash for balance in before.cash}
    actual = {balance.currency: balance.total_cash for balance in broker.cash}
    return tuple(
        CashChange(currency=currency, before=recorded.get(currency), after=actual.get(currency))
        for currency in sorted(recorded.keys() | actual.keys())
        if recorded.get(currency) != actual.get(currency)
    )


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
