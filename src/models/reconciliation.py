"""A session's own view, and its comparison with the broker's, as domain values (Story 4.6).

IBKR is the authority for what the account holds (architecture D2, FR35); a
session's Redis-held engine cache is what the *system* believes. ``live
reconcile`` reads both, compares them line by line, and reports — it never
resolves anything (FR36). These are the shapes that crosses AR38's boundary in:

- ``SessionView`` — the session's engine-cache view, built from Nautilus
  objects by ``src/core/live_session_view.py`` and converted here;
- ``PositionLine`` / ``CashLine`` / ``ReconciliationReport`` — the verdict
  ``src/services/reconciliation_service.py`` produces.

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
    """

    trader_id: str
    positions: tuple[ViewPosition, ...]
    cash: tuple[CashBalance, ...]

    def __post_init__(self) -> None:
        if not self.trader_id:
            raise ValueError("trader_id is required")
        _tuple_of("positions", self.positions, ViewPosition)
        _tuple_of("cash", self.cash, CashBalance)
        instruments = [position.instrument_id for position in self.positions]
        if len(set(instruments)) != len(instruments):
            raise ValueError(f"one row per instrument, got {sorted(instruments)}")
        currencies = [balance.currency for balance in self.cash]
        if len(set(currencies)) != len(currencies):
            raise ValueError(f"one row per currency, got {sorted(currencies)}")

    @property
    def cash_known(self) -> bool:
        """Whether the session's engine recorded any cash at all."""
        return bool(self.cash)


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
    """

    session_name: str
    trader_id: str
    account: str
    positions: tuple[PositionLine, ...]
    cash: tuple[CashLine, ...]
    broker_retrieved_at: datetime
    elapsed_ms: float

    def __post_init__(self) -> None:
        _require_masked(self.account)
        _tuple_of("positions", self.positions, PositionLine)
        _tuple_of("cash", self.cash, CashLine)
        if not self.cash:
            raise ValueError("cash lines must not be empty; the broker always reports cash")
        if self.broker_retrieved_at.utcoffset() is None:
            raise ValueError("broker_retrieved_at must be timezone-aware")

    @property
    def discrepancies(self) -> tuple[PositionLine | CashLine, ...]:
        """Every line that does not match exactly, positions first."""
        lines: tuple[PositionLine | CashLine, ...] = (*self.positions, *self.cash)
        return tuple(line for line in lines if not line.matches)

    @property
    def is_clean(self) -> bool:
        return not self.discrepancies
