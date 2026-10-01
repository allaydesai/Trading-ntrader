"""The broker's authoritative view of positions and cash, as domain values (Story 4.1).

IBKR is the ground truth for what the account holds (architecture D2, FR32);
Nautilus's cache and Redis only defer to it. This module is the shape that truth
takes once it has crossed AR38's boundary: ``src/core/live_broker_state.py``
reads it from the IB adapter and converts every adapter type into these, so a
consumer — Story 4.2's startup check, Story 4.6's comparison service — depends
on nothing but the standard library.

**Success is a value; failure is not.** A retrieval that could not complete
raises (``BrokerStateUnavailableError``, in the reader) and never produces a
``BrokerState``. So a flat account — ``positions == ()`` — is always a real,
successful answer from the broker, and the invariants below make the shapes a
half-failed read would produce unrepresentable: a state with no cash, an
unmasked account, a zero-quantity "position", a quantity or price carried as a
binary float.

Stdlib only, pinned by ``tests/unit/models/test_broker_state.py``.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

#: What ``live_gate.mask_account`` puts in front of an account's last characters.
#: Duplicated rather than imported (``src.core`` is off limits to this module),
#: so ``tests/unit/models/test_broker_state.py`` pins it to ``mask_account``'s
#: real output — a duplicated literal needs its own equality pin (CLAUDE.md).
MASK_PREFIX = "***"
#: ``mask_account`` reveals at most this many trailing characters.
MASKED_TAIL_MAX = 3


def is_currency_code(code: str) -> bool:
    """Whether ``code`` is a three-letter ISO-style currency code (``USD``).

    Deliberately excludes IBKR's ``BASE`` pseudo-currency, which aggregates
    every other currency and would double-count against them.
    """
    return len(code) == 3 and code.isascii() and code.isalpha() and code.isupper()


def _require_finite_decimal(name: str, value: object) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite, got {value}")
    return value


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    """One open position as the broker reports it.

    Attributes:
        instrument_id: The Nautilus-style identifier (``NVDA.NASDAQ``) the
            session's own cache uses, or ``IB-CONID-<conId>`` when the adapter
            could not resolve the contract — see ``instrument_resolved``.
        quantity: Signed: positive long, negative short. Never zero — a flat
            instrument is the absence of a row.
        average_price: IBKR's average cost per unit, or ``None`` when IBKR did
            not report a usable one. Never zero.
        con_id: IBKR's contract id — the one identifier that survives a failed
            resolution.
        symbol: IBKR's symbol for the contract, for an operator to read.
        instrument_resolved: ``False`` when ``instrument_id`` is the fallback.
            Such a row is kept, never dropped: dropping it would under-report
            what the broker holds.
    """

    instrument_id: str
    quantity: Decimal
    average_price: Decimal | None
    con_id: int
    symbol: str
    instrument_resolved: bool = True

    def __post_init__(self) -> None:
        if not self.instrument_id:
            raise ValueError("instrument_id is required")
        if _require_finite_decimal("quantity", self.quantity) == 0:
            raise ValueError("quantity must be non-zero; a flat instrument has no row")
        if self.average_price is not None:
            if _require_finite_decimal("average_price", self.average_price) <= 0:
                raise ValueError("average_price must be positive, or None when unknown")


@dataclass(frozen=True, slots=True)
class CashBalance:
    """The broker's cash in one currency — IBKR's ``TotalCashValue`` tag.

    Not net liquidation, not buying power, not Nautilus's ``AccountBalance``
    (whose ``total`` is net liquidation, or an invented placeholder — see the
    reader's module docstring). May be zero or negative: a margin account can
    owe.
    """

    currency: str
    total_cash: Decimal

    def __post_init__(self) -> None:
        if not is_currency_code(self.currency):
            raise ValueError(f"currency must be a three-letter code, got {self.currency!r}")
        _require_finite_decimal("total_cash", self.total_cash)


@dataclass(frozen=True, slots=True)
class BrokerState:
    """Everything the broker reported for the configured account, in one read.

    Not one instant: positions are IBKR's answer to the read's own (or a
    joined) ``reqPositions``, and cash is the latest account-summary push the
    process had received — see the reader's module docstring on freshness.

    Attributes:
        account: The account, **masked** (``***626``): ``***`` plus at most
            three trailing characters. Anything longer is refused, so no
            rendering of this value can leak a raw identifier (NFR26).
        positions: Every open position; ``()`` is a flat account.
        cash: One ``CashBalance`` per currency the broker reported. Never
            empty: a read that could not get cash failed.
        retrieved_at: When the read completed (timezone-aware).
    """

    account: str
    positions: tuple[BrokerPosition, ...]
    cash: tuple[CashBalance, ...]
    retrieved_at: datetime

    def __post_init__(self) -> None:
        masked = self.account.startswith(MASK_PREFIX)
        if not masked or len(self.account) > len(MASK_PREFIX) + MASKED_TAIL_MAX:
            raise ValueError("account must be the masked form, never a raw identifier")
        self._check_members("positions", self.positions, BrokerPosition)
        self._check_members("cash", self.cash, CashBalance)
        if not self.cash:
            raise ValueError("cash must not be empty; a read without cash has failed")
        instruments = [position.instrument_id for position in self.positions]
        if len(set(instruments)) != len(instruments):
            raise ValueError(f"one row per instrument, got {sorted(instruments)}")
        currencies = [balance.currency for balance in self.cash]
        if len(set(currencies)) != len(currencies):
            raise ValueError(f"one row per currency, got {sorted(currencies)}")
        if self.retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")

    @staticmethod
    def _check_members(name: str, values: object, member_type: type) -> None:
        if not isinstance(values, tuple):
            raise TypeError(f"{name} must be a tuple, got {type(values).__name__}")
        for value in values:
            if not isinstance(value, member_type):
                raise TypeError(f"{name} holds {type(value).__name__}, not {member_type.__name__}")

    @property
    def is_flat(self) -> bool:
        """Whether the broker reported no open position at all."""
        return not self.positions
