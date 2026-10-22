"""What a seal does with what the session still holds (Story 5.5, FR47, AR30).

Owns: the open-position and working-order dispositions a seal reports, the
refusal an unforced seal gives a session that is not known to be flat, and the
open trade with no exit a forced seal writes for each open position — the
backtest's own open-row shape (``save_trades_from_positions``). Pure: no
database session, no clock, no I/O.

Does not own: reading the holdings (``src.core.live_session_view``, injected by
the CLI), deciding when ``--force`` applies or writing the rows
(``session_seal_service`` — one transaction), or the conditions document
(``session_conditions``).

**Never the broker, never an order.** Nothing here exits, flattens, cancels or
modifies a position or an order (AR31, NFR14, AR43); every disposition names the
session's engine cache as its basis, never the broker's own state.

Split out of ``session_seal_service`` at the 5-4/5-5 integration merge to keep
that module under its statement cap, without loosening the cap.
"""

from decimal import Decimal
from typing import Any

from sqlalchemy import String

from src.db.exceptions import SealRefusedError
from src.db.models.trade import Trade
from src.models.reconciliation import ORDERS_NOT_LISTED, OpenLeg, SessionHoldings
from src.models.trade import TradeCreate

#: Every disposition names its basis — the session's engine cache, read
#: load-only — never the broker's own state (code review P2). No AR36 stem: an
#: open trade has "no exit", never a "close".
#: The disposition a seal reports when the session held nothing (Story 5.3).
NONE_OPEN_DISPOSITION = "none open in the session's engine cache at seal"
#: AC 5.5c: what a forced seal says of the open positions it recorded (D-D, D-E;
#: code review P3 — P&L already realized on a partly reduced position lives only
#: on its open row, so it is outside the metrics too).
RECORDED_OPEN_DISPOSITION = (
    "recorded, as the session's engine cache last held them, as open trades with no exit; "
    "they, and any P&L already realized on them, are outside the record's metrics; they "
    "remain at the broker and are no longer owned by any session"
)
#: PO ruling R2a: positions held for another account, sealed past with ``force``.
OTHER_ACCOUNT_DISPOSITION = (
    "held for another account in the session's engine cache and not recorded; they remain "
    "at the broker and are no longer owned by any session"
)
#: PO ruling R2a (DN2a): a forced seal of a session whose engine cache held
#: nothing. Code review DN2a: a reachable but wrong Redis reads exactly so.
UNKNOWN_HOLDINGS_DISPOSITION = (
    "unknown: the session's engine cache held nothing to read (confirm REDIS_HOST and "
    "REDIS_PORT point at the session's own Redis); anything held at the broker is no longer "
    "owned by any session"
)
#: PO ruling R3b: no order was working in the session's cache.
NONE_WORKING_DISPOSITION = "none working in the session's engine cache at seal"
#: PO ruling R3b: orders the cache held working — reported, never cancelled,
#: with the limitation R3b states.
WORKING_ORDERS_DISPOSITION = (
    "working in the session's engine cache, which cannot see a fill or cancel made at IBKR "
    "while the session was stopped; left as they are at IBKR and owned by no session; the "
    "seal cancels nothing, so check them in TWS and cancel any that should not fill"
)
#: Code review DN1a: orders the cache holds but could not read — named, never a refusal.
UNREADABLE_ORDERS_DISPOSITION = (
    "could not be read from the session's engine cache; check them in TWS, as any that is "
    "still working is owned by no session"
)
#: PR #36 review DN1: the engine cache's orders could not be listed — no count.
ORDERS_NOT_LISTED_DISPOSITION = (
    "unknown: the session's orders could not be listed from its engine cache; check them in "
    "TWS, as any that is still working is owned by no session"
)
#: PO ruling R3b with R2a: the orders of a session whose engine cache held nothing.
UNKNOWN_ORDERS_DISPOSITION = (
    "unknown: the session's engine cache held nothing to read; any order working at "
    "IBKR is owned by no session"
)


def refuse_unless_flat(name: str, holdings: SessionHoldings) -> None:
    """PO ruling R2a: without ``force``, fail closed on anything but a known-flat
    session — naming each open position (AC 5.5a: R1a's position, one per leg,
    with its owner; code review P4) and ``--force`` as the other way."""
    if holdings.legs:
        held = ", ".join(
            f"{leg.instrument_id} {leg.quantity} ({leg.strategy_id})" for leg in holdings.legs
        )
        raise SealRefusedError(
            f"Session {name!r} still holds open positions ({held}). A seal never exits or "
            "modifies a position: restart the session so its strategy can exit it, then stop "
            "the session and seal; or seal with --force to record each one as an open trade "
            "with no exit, owned by no session from then on."
        )
    skipped = holdings.view.positions_skipped
    if skipped:
        raise SealRefusedError(
            f"Session {name!r}'s engine cache holds {skipped} position(s) on another account "
            "than TWS_ACCOUNT, so it is not known to be flat. Check TWS_ACCOUNT is the account "
            "this session traded: a changed TWS_ACCOUNT is the usual cause. Only if it is, seal "
            "with --force to seal past them, unrecorded and owned by no session."
        )


def open_trade(leg: OpenLeg, session_pk: int) -> Trade:
    """AC 5.5b: one open position as an open trade with a null exit.

    The backtest's own open row (``save_trades_from_positions``): ``trade_id`` is
    the position id, ``venue_order_id`` the opening order, no closing order, no
    exit price, time, holding period or ``profit_pct``, and ``profit_loss`` the
    position's realized P&L — column for column, pinned against a real
    positions report in ``tests/component/services/test_open_trade_parity.py``.
    The paths part only on a partly reduced position (D-B): the backtest keeps
    ``peak_qty`` and the reduction's price as an exit price with no exit time;
    this row keeps what remains (PO ruling R1a) and no exit at all.
    Validated through ``TradeCreate`` first, as both other trade writes are.

    Raises:
        SealRefusedError: The leg is not a valid trade row (e.g. a quantity
            finer than ``trades.quantity``'s 8 decimal places, or an id longer
            than its column), by position.
    """
    _require_ids_fit(leg)
    try:
        validated = TradeCreate(
            session_id=session_pk,
            instrument_id=leg.instrument_id,
            trade_id=leg.position_id,
            venue_order_id=leg.opening_order_id,
            client_order_id=None,
            strategy_id=leg.strategy_id,
            order_side=leg.side,
            quantity=abs(leg.quantity),
            entry_price=leg.avg_px_open,
            exit_price=None,
            commission_amount=leg.commission_amount,
            commission_currency=leg.commission_currency,
            fees_amount=Decimal("0.00"),
            entry_timestamp=leg.opened_at,
            exit_timestamp=None,
        )
    except ValueError as exc:  # pydantic's ValidationError is one
        raise SealRefusedError(
            f"Position {leg.position_id} cannot be recorded as an open trade: {exc}"
        ) from exc
    return Trade(
        **validated.model_dump(),
        profit_loss=leg.realized_pnl,
        profit_pct=None,
        holding_period_seconds=None,
    )


def _require_ids_fit(leg: OpenLeg) -> None:
    """Code review P8: ``TradeCreate`` sets no length on these ids, so one past
    its ``String`` column would reach Postgres as a truncation error naming
    nothing. The bound is read from the column itself."""
    named = leg.position_id if len(leg.position_id) <= 60 else f"{leg.position_id[:60]}…"
    for column, value in (("trade_id", leg.position_id), ("venue_order_id", leg.opening_order_id)):
        column_type = Trade.__table__.c[column].type
        length = column_type.length if isinstance(column_type, String) else None
        if length is not None and len(value) > length:
            raise SealRefusedError(
                f"Position {named} cannot be recorded as an open trade: its {column} is "
                f"{len(value)} characters, more than the column's {length}."
            )


def open_positions_disposition(holdings: SessionHoldings | None) -> str:
    """D-D: what became of what the session held — one function, every outcome."""
    if holdings is None:
        return UNKNOWN_HOLDINGS_DISPOSITION
    parts = [RECORDED_OPEN_DISPOSITION] if holdings.legs else []
    skipped = holdings.view.positions_skipped
    if skipped:
        parts.append(f"{skipped} position(s) {OTHER_ACCOUNT_DISPOSITION}")
    return "; and ".join(parts) or NONE_OPEN_DISPOSITION


def working_orders_disposition(holdings: SessionHoldings | None) -> str:
    """PO ruling R3b: what becomes of the orders the session's cache held working
    — and of any it held but could not read (code review DN1a)."""
    if holdings is None:
        return UNKNOWN_ORDERS_DISPOSITION
    if holdings.unreadable_orders == (ORDERS_NOT_LISTED,):
        return ORDERS_NOT_LISTED_DISPOSITION
    parts = [WORKING_ORDERS_DISPOSITION] if holdings.working_orders else []
    unreadable = len(holdings.unreadable_orders)
    if unreadable:
        parts.append(f"{unreadable} order(s) {UNREADABLE_ORDERS_DISPOSITION}")
    return "; and ".join(parts) or NONE_WORKING_DISPOSITION


def holdings_report(holdings: SessionHoldings | None) -> dict[str, Any]:
    """The report's holdings fields — what was held, and what became of it."""
    fields: dict[str, Any] = {
        "holdings_known": holdings is not None,
        "open_positions_disposition": open_positions_disposition(holdings),
        "working_orders_disposition": working_orders_disposition(holdings),
    }
    if holdings is not None:  # unknown holdings keep the empty defaults, flagged above
        fields["open_positions"] = holdings.view.positions
        fields["positions_on_another_account"] = holdings.view.positions_skipped
        fields["working_orders"] = holdings.working_orders
        fields["unreadable_orders"] = holdings.unreadable_orders
    return fields
