"""A sealed session's results, reconstructed from its trade rows (Story 5.2, AR35).

A sealed session has no engine history to draw an equity curve from, and its
node's cache is no record of it either: a NETTING cache loses closed legs on a
flip and on a restart, and duplicates one on a redelivered fill (Story 5.1 code
review). Its ``trades`` rows — one per ``PositionClosed``, written idempotently
(Story 3.6) — are the record. This module turns them into the legs Story 5.1's
:meth:`~src.core.results_extractor.LiveHost.settle` reads, and runs the
unmodified :class:`~src.core.results_extractor.ResultsExtractor` over them. It
computes no metric itself: max drawdown, CAGR, Calmar and every other field come
out of the extractor's own code path (FR41).

**The series is the backtest's, by PO ruling R1b (2026-10-07).** A backtest's
max drawdown compounds each position's *price* return, filed at its close time,
as if all capital were in every trade — it does not depend on position size
(story F1: −1.59% at 10% and at 2% sizing, where the account's own drawdown is
−0.15% / −0.03%). So each leg here carries the return Nautilus would have
computed for that position, ``(exit − entry) / entry``, side-adjusted, and a
paper record's drawdown means what a backtest's means. ``session_conditions``
says so, and says the series is trade-granular, not bar-granular.

**Closed rows only.** A row with no exit is not part of a closed-trade sequence,
and as a leg it would file its return at the epoch (Story 5.1's deferred
artifact). A backtest that ends mid-position keeps that artifact; a paper record
does not — recorded in ``equity_series.excludes`` and in deferred-work.

No SQLAlchemy: rows arrive through :class:`SessionTradeRow`, which the ORM
``Trade`` and the domain ``src.models.trade.Trade`` both satisfy. Reading them
is the seal service's (Story 5.3).
"""

import copy
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import MessageBus, TestClock
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.identifiers import PositionId, TraderId
from nautilus_trader.model.objects import Money
from nautilus_trader.portfolio.portfolio import Portfolio

from src.core.results_extractor import LiveHost, ResultsExtractor
from src.models.backtest_result import BacktestResult

#: The one key this story writes into ``session_conditions`` — namespaced, so it
#: cannot collide with the flat AR7 keys Story 5.4 owns.
EQUITY_SERIES_KEY = "equity_series"
#: AR35's recorded limitation: points only at closed trades, never per bar — one
#: per distinct close instant (legs closing at the identical timestamp are
#: summed into one return, as on a backtest), timed to the whole second.
GRANULARITY = "closed_trade"
#: PO ruling R1b: each point compounds the closed trade's own price return.
BASIS = "per_trade_price_return"
#: What the series cannot see (D-D; story F9 — reconciliation-owned positions,
#: including a corporate action reconciliation absorbed, are never trades).
EXCLUDES = ("open_positions", "reconciliation_owned_activity")
#: Every session row is strategy-owned (Story 3.6 D-D) and rows carry no
#: strategy id; any id outside ``RECONCILIATION_STRATEGY_IDS`` keeps the leg.
SESSION_STRATEGY_ID = "SESSION"

_SIDE_SIGN = {"BUY": Decimal(1), "SELL": Decimal(-1)}
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND = timedelta(microseconds=1)
#: The offline portfolio's trader id; nothing outside this module ever sees it.
_SEAL_TRADER_ID = TraderId("SEAL-000")


class SessionTradeRow(Protocol):
    """The members of a ``trades`` row the reconstruction reads — nothing else."""

    @property
    def id(self) -> int: ...

    @property
    def order_side(self) -> str: ...

    @property
    def entry_price(self) -> Decimal: ...

    @property
    def exit_price(self) -> Decimal | None: ...

    @property
    def profit_loss(self) -> Decimal | None: ...

    @property
    def exit_timestamp(self) -> datetime | None: ...

    @property
    def commission_currency(self) -> str | None: ...


def _utc(stamp: datetime) -> datetime:
    """A naive timestamp is UTC — ``backtest_persistence._to_utc``'s convention."""
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


@dataclass(frozen=True)
class _TradeLeg:
    """A closed trade row as the leg a ``Position`` would have been.

    ``PortfolioAnalyzer.calculate_statistics`` reads four members of a position
    (``id``, ``realized_pnl``, ``ts_closed``, ``realized_return`` — story F2);
    ``LiveHost.settle`` and the extractor read ``strategy_id``, ``is_open`` and
    ``is_closed``. This offers exactly those: a Nautilus upgrade that reads more
    fails with ``AttributeError`` rather than a defaulted value.
    """

    id: PositionId
    strategy_id: str
    realized_pnl: Money
    realized_return: float
    ts_closed: int
    is_open: bool = False
    is_closed: bool = True

    @classmethod
    def from_row(cls, row: SessionTradeRow) -> "_TradeLeg":
        """The leg for one closed row.

        Raises:
            ValueError: The row cannot be read honestly (D-E) — no P&L or exit
                price, a non-positive entry price, an unknown side, or a
                commission in a currency other than USD (rows carry no P&L
                currency; the extractor reads USD only).
        """
        exit_price, profit_loss, closed_at = _readable(row)
        # Position._calculate_return: points / avg_px_open, side-adjusted.
        points = (exit_price - row.entry_price) * _SIDE_SIGN[row.order_side]
        return cls(
            id=PositionId(f"trade-{row.id}"),
            strategy_id=SESSION_STRATEGY_ID,
            realized_pnl=Money(profit_loss, USD),
            realized_return=float(points / row.entry_price),
            ts_closed=(_utc(closed_at) - _EPOCH) // _MICROSECOND * 1_000,
        )


def _readable(row: SessionTradeRow) -> tuple[Decimal, Decimal, datetime]:
    """A closed row's ``(exit_price, profit_loss, exit_timestamp)``, or a refusal
    — never a value the reconstruction would have to guess (D-E)."""
    exit_price, profit_loss, closed_at = row.exit_price, row.profit_loss, row.exit_timestamp
    problem = None
    if closed_at is None:
        problem = "has no exit_timestamp"
    elif profit_loss is None:
        problem = "has no profit_loss"
    elif exit_price is None:
        problem = "has no exit_price"
    elif row.entry_price <= 0:
        problem = f"has entry_price {row.entry_price}; a return needs a positive one"
    elif row.order_side not in _SIDE_SIGN:
        problem = f"has side {row.order_side!r}, neither BUY nor SELL"
    elif row.commission_currency not in (None, USD.code):
        problem = (
            f"was charged in {row.commission_currency}; a sealed record is USD-only, "
            "and a trade row carries no P&L currency to convert from"
        )
    if problem is not None or closed_at is None or profit_loss is None or exit_price is None:
        raise ValueError(f"trade {row.id} {problem}; refusing to reconstruct a leg from it")
    return exit_price, profit_loss, closed_at


def _legs_from(rows: Iterable[SessionTradeRow]) -> list[_TradeLeg]:
    """The closed rows' legs in close order, ties broken by row id (D-D).

    Raises:
        ValueError: A row id appears twice — the analyzer keys P&L by it, so a
            repeat would count twice in the ledger and once in the statistics —
            or a row has an exit price but no exit timestamp, which is neither
            open (a backtest open row has no exit price) nor closed.
    """
    rows = list(rows)
    seen: set[int] = set()
    for row in rows:
        if row.id in seen:
            raise ValueError(f"trade {row.id} appears more than once; refusing to count it twice")
        seen.add(row.id)
        if row.exit_timestamp is None and row.exit_price is not None:
            raise ValueError(
                f"trade {row.id} has an exit_price but no exit_timestamp; it is neither "
                "open nor closed, so refusing to drop or to guess it"
            )
    closed = [row for row in rows if row.exit_timestamp is not None]
    closed.sort(key=lambda row: (_utc(row.exit_timestamp), row.id))  # type: ignore[arg-type]
    return [_TradeLeg.from_row(row) for row in closed]


@dataclass(frozen=True)
class _LegsCache:
    """The two reads ``LiveHost.settle`` makes of a cache, answered from rows."""

    legs: tuple[_TradeLeg, ...]

    def positions(self) -> list[_TradeLeg]:
        return list(self.legs)

    def position_snapshots(self) -> list[_TradeLeg]:
        return []


@dataclass(frozen=True)
class _TradesSource:
    """A ``ResultsHost`` built offline: a real ``Portfolio`` for its analyzer.

    A real ``Portfolio`` registers the statistics every backtest kernel's does
    (Story 5.1 F9) — copying that registration list would be a second source
    for it. Its ``Cache`` stays empty, and no clock is ever read.
    """

    portfolio: Any
    cache: _LegsCache

    @classmethod
    def over(cls, legs: Iterable[_TradeLeg]) -> "_TradesSource":
        clock = TestClock()
        portfolio = Portfolio(MessageBus(_SEAL_TRADER_ID, clock), Cache(database=None), clock)
        return cls(portfolio, _LegsCache(tuple(legs)))


@dataclass(frozen=True)
class SessionResults:
    """A sealed session's results — the hand-over to the seal service (Story 5.3).

    ``result`` is the existing ``BacktestResult`` (FR42: one results
    vocabulary). ``session_conditions`` is this story's fragment of the
    ``config_snapshot["session_conditions"]`` document.
    """

    result: BacktestResult
    session_conditions: dict[str, Any]

    def merged_into(self, config_snapshot: Mapping[str, Any]) -> dict[str, Any]:
        """A copy of ``config_snapshot`` with ``equity_series`` added to its
        ``session_conditions`` — beside any keys already there (Story 5.4's flat
        AR7 keys), never replacing the document. Neither input is changed.

        Raises:
            ValueError: ``session_conditions`` already holds an ``equity_series``
                — two sources for one series.
        """
        merged = copy.deepcopy(dict(config_snapshot))
        conditions = dict(merged.get("session_conditions") or {})
        if EQUITY_SERIES_KEY in conditions:
            raise ValueError(
                f"session_conditions already holds an {EQUITY_SERIES_KEY}; refusing to "
                "write a second one over it"
            )
        conditions.update(copy.deepcopy(self.session_conditions))
        merged["session_conditions"] = conditions
        return merged


def _session_conditions(closed_trades: int, curve: list[dict[str, Any]]) -> dict[str, Any]:
    """The ``equity_series`` fragment: the series and what it can and cannot show."""
    return {
        EQUITY_SERIES_KEY: {
            "granularity": GRANULARITY,
            "basis": BASIS,
            "closed_trades": closed_trades,
            "excludes": list(EXCLUDES),
            "curve": curve,
        }
    }


def _require_finite(result: BacktestResult) -> None:
    """PO ruling R3a: a NaN or an infinity is refused here, never written."""
    for name, value in result.to_dict().items():
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(
                f"the reconstructed {name} is {value}; refusing to seal a record "
                "that would carry a non-finite metric"
            )


def reconstruct_session_results(
    trades: Iterable[SessionTradeRow],
    *,
    starting_capital: Decimal,
    start: datetime,
    end: datetime,
) -> SessionResults:
    """Compute a sealed session's results from its trade rows (AR35, FR41).

    Args:
        trades: The session's ``trades`` rows. Rows with no exit are not part of
            the closed-trade sequence and are left out (D-D).
        starting_capital: The session's own starting capital — a positive,
            whole-cent ``Decimal``, stored as-is (AR7; Story 5.1's rule).
        start: The session's first start, for CAGR.
        end: The seal time, for CAGR.

    Returns:
        The ``BacktestResult`` the unmodified extractor computes over the legs,
        and the ``equity_series`` fragment of ``session_conditions``. Zero
        closed trades is an explicit record (PO ruling R3a): ``total_trades``
        and ``closed_trades`` 0, an empty series, no drawdown or Calmar.

    Raises:
        ValueError: ``start`` or ``end`` is naive, or ``end`` is not after
            ``start``; a row cannot be read honestly (D-E); capital that is not
            positive or not whole cents; a metric came out non-finite (R3a); or
            closed trades came back with no equity series.
        TypeError: ``starting_capital`` is not a ``Decimal``.
    """
    _require_period(start, end)
    legs = _legs_from(trades)
    host = LiveHost.settle(_TradesSource.over(legs), starting_balance=starting_capital)
    extractor = ResultsExtractor(engine=host)
    result = extractor.extract_results(start, end)
    _require_finite(result)
    curve = extractor.extract_equity_curve(start, end)
    if legs and not curve:
        # ``extract_equity_curve`` logs and returns [] on any exception.
        raise ValueError(
            f"{len(legs)} closed trade(s) produced no equity series; refusing to store "
            "a record whose series is silently missing"
        )
    return SessionResults(result, _session_conditions(len(legs), curve))


def _require_period(start: datetime, end: datetime) -> None:
    """A timezone-aware period that moves forward — never a raw ``TypeError``."""
    for name, bound in (("start", start), ("end", end)):
        if bound.tzinfo is None or bound.utcoffset() is None:
            raise ValueError(
                f"the session's {name} ({bound}) has no timezone; the period comes from "
                "timezone-aware sources, so a naive bound is refused rather than guessed"
            )
    if end <= start:
        raise ValueError(f"the session's period must move forward: {start} -> {end}")
