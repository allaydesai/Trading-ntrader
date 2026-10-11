"""A session's own view, and its comparison with the broker's, as domain values (Story 4.6).

IBKR is the authority for what the account holds (architecture D2, FR35); a
session's Redis-held engine cache is what the *system* believes. ``live
reconcile`` reads both, compares them line by line, and reports — it never
resolves anything (FR36). These are the shapes that crosses AR38's boundary in:

- ``SessionView`` — the session's engine-cache view, built from Nautilus
  objects by ``src/core/live_session_view.py`` and converted here;
- ``PositionLine`` / ``CashLine`` / ``ReconciliationReport`` — the verdict
  ``src/services/reconciliation_service.py`` produces;
- ``SessionHoldings`` (Story 5.5) — the same view with what the seal needs
  behind it: one ``OpenLeg`` per open position, and one ``WorkingOrder`` per
  order the cache still holds working. Read-side boundary values, not a results structure: a
  sealed session's result is the existing ``trades`` row (FR42).

**Exactness is structural.** Every quantity and amount is a finite ``Decimal``
and every ``matches`` is ``==``: there is no tolerance to configure, because
FR36 allows none ("no averaging, no close enough").

Stdlib plus ``src.models.broker_state`` (itself stdlib-only), pinned by
``tests/unit/models/test_reconciliation.py``.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.models.broker_state import (
    MASK_PREFIX,
    MASKED_TAIL_MAX,
    CashBalance,
    is_currency_code,
)


def _finite_decimal(name: str, value: object) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite, got {value}")
    return value


def _tuple_of(name: str, values: object, member_type: type) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{name} must be a tuple, got {type(values).__name__}")
    for value in values:
        if not isinstance(value, member_type):
            raise TypeError(f"{name} holds {type(value).__name__}, not {member_type.__name__}")


def _require_masked(account: str) -> None:
    if not account.startswith(MASK_PREFIX) or len(account) > len(MASK_PREFIX) + MASKED_TAIL_MAX:
        raise ValueError("account must be the masked form, never a raw identifier")


#: An entry or order side, in the ``trades.order_side`` vocabulary.
SIDES = ("BUY", "SELL")


def _require_present(value: object, *names: str) -> None:
    for name in names:
        if not getattr(value, name):
            raise ValueError(f"{name} is required")


def _require_side(side: str) -> None:
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}, got {side!r}")


def _require_unique(name: str, keys: list[str]) -> None:
    if len(set(keys)) != len(keys):
        raise ValueError(f"each {name} counts once, got {sorted(keys)}")


@dataclass(frozen=True, slots=True)
class ViewPosition:
    """The session's net position in one instrument.

    Attributes:
        instrument_id: The Nautilus identifier (``NVDA.NASDAQ``) — the same id
            space the broker reader resolves into.
        quantity: Signed net quantity over every open position the session's
            cache holds for the instrument (one per strategy under NETTING, plus
            any reconciliation-stamped ones). Never zero.
    """

    instrument_id: str
    quantity: Decimal

    def __post_init__(self) -> None:
        if not self.instrument_id:
            raise ValueError("instrument_id is required")
        if _finite_decimal("quantity", self.quantity) == 0:
            raise ValueError("quantity must be non-zero; a flat instrument has no row")


@dataclass(frozen=True, slots=True)
class SessionView:
    """What a session's durable engine cache says the account holds.

    Attributes:
        trader_id: The session's ``PAPER-<8hex>`` namespace.
        positions: One row per instrument with a non-zero net; ``()`` is flat.
        cash: ``TotalCashValue`` per currency, from the last account-summary
            push the session's engine received. ``()`` means the engine never
            recorded one — cash is then **unknown**, never zero.
        cash_recorded_at: When the broker last reported that cash — the
            session's account's last reported ``AccountState`` (Story 4.7,
            D-C) — or ``None`` when unknown.
        positions_skipped: How many open positions in the cache are held for
            an account other than the one configured, and so are not in
            ``positions``. Non-zero after a ``TWS_ACCOUNT`` change since the
            session ran (PR #35 code review, P9).
    """

    trader_id: str
    positions: tuple[ViewPosition, ...]
    cash: tuple[CashBalance, ...]
    cash_recorded_at: datetime | None = None
    positions_skipped: int = 0

    def __post_init__(self) -> None:
        if not self.trader_id:
            raise ValueError("trader_id is required")
        if self.positions_skipped < 0:
            raise ValueError("positions_skipped is a count")
        _tuple_of("positions", self.positions, ViewPosition)
        _tuple_of("cash", self.cash, CashBalance)
        instruments = [position.instrument_id for position in self.positions]
        if len(set(instruments)) != len(instruments):
            raise ValueError(f"one row per instrument, got {sorted(instruments)}")
        currencies = [balance.currency for balance in self.cash]
        if len(set(currencies)) != len(currencies):
            raise ValueError(f"one row per currency, got {sorted(currencies)}")
        if self.cash_recorded_at is not None and self.cash_recorded_at.utcoffset() is None:
            raise ValueError("cash_recorded_at must be timezone-aware")

    @property
    def cash_known(self) -> bool:
        """Whether the session's engine recorded any cash at all."""
        return bool(self.cash)


@dataclass(frozen=True, slots=True)
class OpenLeg:
    """One open position the session's engine cache holds (Story 5.5, R1a).

    What a seal with ``--force`` records as an open trade with no exit — the
    fields the backtest's own open row is built from (``save_trades_from_positions``).

    Attributes:
        position_id: The Nautilus position id — the row's ``trade_id``. It names
            its owner (``NVDA.NASDAQ-EXTERNAL`` for reconciliation's own).
        instrument_id: The instrument (``NVDA.NASDAQ``).
        strategy_id: The position's owner, strategy or reconciliation stamp.
        side: The entry side, ``BUY`` or ``SELL``; agrees with ``quantity``.
        quantity: Signed **current** open quantity — what remains. Never zero.
        avg_px_open: The volume-weighted entry price; positive.
        opened_at: When the position opened (timezone-aware).
        opening_order_id: The order that opened it — the row's ``venue_order_id``.
        realized_pnl: Realized so far (an open leg's is minus its commission);
            ``None`` when the position carries none.
        commission_amount: Commission charged so far; ``None`` when unknown.
        commission_currency: Its currency code; ``None`` when unknown.
    """

    position_id: str
    instrument_id: str
    strategy_id: str
    side: str
    quantity: Decimal
    avg_px_open: Decimal
    opened_at: datetime
    opening_order_id: str
    realized_pnl: Decimal | None = None
    commission_amount: Decimal | None = None
    commission_currency: str | None = None

    def __post_init__(self) -> None:
        _require_present(self, "position_id", "instrument_id", "strategy_id", "opening_order_id")
        _require_side(self.side)
        quantity = _finite_decimal("quantity", self.quantity)
        if quantity == 0:
            raise ValueError("an open leg's quantity must be non-zero")
        if (quantity > 0) != (self.side == "BUY"):
            raise ValueError(f"side {self.side} disagrees with the quantity {quantity}")
        if _finite_decimal("avg_px_open", self.avg_px_open) <= 0:
            raise ValueError(f"avg_px_open must be positive, got {self.avg_px_open}")
        if self.opened_at.utcoffset() is None:
            raise ValueError("opened_at must be timezone-aware")
        for name in ("realized_pnl", "commission_amount"):
            if getattr(self, name) is not None:
                _finite_decimal(name, getattr(self, name))
        currency = self.commission_currency
        if currency is not None and not is_currency_code(currency):
            raise ValueError(f"commission_currency must be a three-letter code, got {currency!r}")


@dataclass(frozen=True, slots=True)
class WorkingOrder:
    """An order the session's engine cache still holds working (Story 5.5, R3b).

    Reported at seal time, never acted on: the seal cancels nothing.

    Attributes:
        client_order_id: The session's own order id.
        instrument_id: The instrument.
        side: ``BUY`` or ``SELL``.
        quantity: What is left to fill; positive.
        order_type: ``MARKET``, ``LIMIT``, ``STOP_MARKET``, …
        status: Its last status in the cache (``ACCEPTED``, ``SUBMITTED``, …).
    """

    client_order_id: str
    instrument_id: str
    side: str
    quantity: Decimal
    order_type: str
    status: str

    def __post_init__(self) -> None:
        _require_present(self, "client_order_id", "instrument_id", "order_type", "status")
        _require_side(self.side)
        if _finite_decimal("quantity", self.quantity) <= 0:
            raise ValueError(f"quantity must be positive, got {self.quantity}")


@dataclass(frozen=True, slots=True)
class SessionHoldings:
    """A session's view plus what stands behind it (Story 5.5).

    Read once, by ``src/core/live_session_view.py``, so the legs and the net
    cannot disagree — and pinned here so they never can: every instrument's
    legs sum to its ``ViewPosition.quantity``, and no leg stands on an
    instrument the view does not list (legs netting to zero leave no row, D-C).

    Attributes:
        view: The session's view, exactly as ``read_session_view`` returns it.
        legs: One per open position on the configured account.
        working_orders: The orders the cache still holds working.
        unreadable_orders: The client order ids of orders the cache holds but
            that could not be rebuilt or read — named, never a refusal and never
            silently left out (code review DN1a).
    """

    view: SessionView
    legs: tuple[OpenLeg, ...]
    working_orders: tuple[WorkingOrder, ...] = ()
    unreadable_orders: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.view, SessionView):
            raise TypeError(f"view must be a SessionView, got {type(self.view).__name__}")
        _tuple_of("legs", self.legs, OpenLeg)
        _tuple_of("working_orders", self.working_orders, WorkingOrder)
        _tuple_of("unreadable_orders", self.unreadable_orders, str)
        if not all(self.unreadable_orders):
            raise ValueError("an unreadable order is named by a non-empty client order id")
        _require_unique("position", [leg.position_id for leg in self.legs])
        _require_unique(
            "order",
            [order.client_order_id for order in self.working_orders] + list(self.unreadable_orders),
        )
        net: dict[str, Decimal] = {}
        for leg in self.legs:
            net[leg.instrument_id] = net.get(leg.instrument_id, Decimal(0)) + leg.quantity
        listed = {position.instrument_id: position.quantity for position in self.view.positions}
        if net != listed:
            raise ValueError(
                f"the legs must sum to the view's net per instrument: legs {net}, view {listed}"
            )


@dataclass(frozen=True, slots=True)
class PositionLine:
    """One instrument, compared: the session's quantity against the broker's.

    Attributes:
        instrument_id: The instrument, or the broker's ``IB-CONID-<n>``
            fallback when the contract could not be resolved.
        local_quantity: The session's signed net (``0`` when it holds none) —
            what the system *expected*.
        broker_quantity: IBKR's signed quantity (``0`` when it holds none) —
            what is *actually* there.
        broker_average_price: IBKR's average cost per unit. Informational only:
            it is commission-inclusive, so it is shown and never compared.
        broker_symbol: IBKR's symbol, for an unresolved row an operator must
            recognise.
        broker_resolved: ``False`` when ``instrument_id`` is the fallback.
    """

    instrument_id: str
    local_quantity: Decimal
    broker_quantity: Decimal
    broker_average_price: Decimal | None
    broker_symbol: str | None = None
    broker_resolved: bool = True

    def __post_init__(self) -> None:
        _finite_decimal("local_quantity", self.local_quantity)
        _finite_decimal("broker_quantity", self.broker_quantity)

    @property
    def difference(self) -> Decimal:
        """``broker − session``: what the broker holds beyond the session's view."""
        return self.broker_quantity - self.local_quantity

    @property
    def matches(self) -> bool:
        return self.local_quantity == self.broker_quantity


@dataclass(frozen=True, slots=True)
class CashLine:
    """One currency, compared.

    Attributes:
        currency: A three-letter code.
        local_cash: The session's last-recorded ``TotalCashValue``, or ``None``
            when its engine recorded none (unknown, never zero).
        broker_cash: IBKR's ``TotalCashValue`` now, or ``None`` when the broker
            reported no cash in this currency.
    """

    currency: str
    local_cash: Decimal | None
    broker_cash: Decimal | None

    def __post_init__(self) -> None:
        if not is_currency_code(self.currency):
            raise ValueError(f"currency must be a three-letter code, got {self.currency!r}")
        if self.local_cash is None and self.broker_cash is None:
            raise ValueError("a cash line needs at least one side")
        for name, value in (("local_cash", self.local_cash), ("broker_cash", self.broker_cash)):
            if value is not None:
                _finite_decimal(name, value)

    @property
    def difference(self) -> Decimal | None:
        """``broker − session``, or ``None`` when either side is unknown."""
        if self.local_cash is None or self.broker_cash is None:
            return None
        return self.broker_cash - self.local_cash

    @property
    def matches(self) -> bool:
        return self.difference is not None and self.local_cash == self.broker_cash


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """The verdict of one on-demand check: every line, matched or not.

    Attributes:
        session_name: The session the local view belongs to.
        trader_id: Its engine-cache namespace.
        account: The account, **masked** — the broker state's own masked form.
        positions: One line per instrument either side holds, sorted.
        cash: One line per currency either side reported, sorted. Never empty:
            the broker always reports cash (``BrokerState`` refuses none).
        broker_retrieved_at: When the broker read completed (timezone-aware).
        elapsed_ms: The check's own time from start to verdict.
        local_cash_recorded_at: When the broker last reported the session's
            cash (Story 4.7, D-C) — "session cash as of" — or ``None``.
        local_positions_skipped: The session view's ``positions_skipped`` —
            cached positions held for another account, left out of the
            session side of every position line (PR #35 code review, P9).
    """

    session_name: str
    trader_id: str
    account: str
    positions: tuple[PositionLine, ...]
    cash: tuple[CashLine, ...]
    broker_retrieved_at: datetime
    elapsed_ms: float
    local_cash_recorded_at: datetime | None = None
    local_positions_skipped: int = 0

    def __post_init__(self) -> None:
        _require_masked(self.account)
        if self.local_positions_skipped < 0:
            raise ValueError("local_positions_skipped is a count")
        _tuple_of("positions", self.positions, PositionLine)
        _tuple_of("cash", self.cash, CashLine)
        if not self.cash:
            raise ValueError("cash lines must not be empty; the broker always reports cash")
        if self.broker_retrieved_at is None:
            raise ValueError("broker_retrieved_at is required")
        for name in ("broker_retrieved_at", "local_cash_recorded_at"):
            value = getattr(self, name)
            if value is not None and value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")

    @property
    def discrepancies(self) -> tuple[PositionLine | CashLine, ...]:
        """Every line that does not match exactly, positions first."""
        lines: tuple[PositionLine | CashLine, ...] = (*self.positions, *self.cash)
        return tuple(line for line in lines if not line.matches)

    @property
    def is_clean(self) -> bool:
        return not self.discrepancies
