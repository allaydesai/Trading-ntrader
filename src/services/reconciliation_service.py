"""Compare a session's view against the broker's, and say what differs (Story 4.6, FR36).

Owns: the line-by-line comparison behind ``ntrader live reconcile`` and its
operator-readable rendering. Does not own: reading either side (the broker —
``src/core/live_broker_state.py``; the session's engine cache —
``src/core/live_session_view.py``), the node, or the exit code.

**The broker is the authority, and nothing here resolves anything.** A
difference is reported, by instrument and currency, with both sides and the
exact ``Decimal`` difference — never averaged, rounded or "close enough"
(FR36), and never written back anywhere (FR35). What to do about it is the
operator's call; at the next start, Story 4.2's reconciliation hydrates from
the broker.

**What the two sides are.** The broker side is IBKR's positions and
``TotalCashValue`` now. The session side is its durable engine cache: the
net of every open position it holds per instrument, and the last
``TotalCashValue`` its engine was pushed. So a stopped session whose account
moved since it last ran — another session's fill, interest, a dividend —
honestly reads as a discrepancy; the rendered note says why, and since when
("session cash as of <time>", Story 4.7 D-C).

**Price is informational.** IBKR's average cost is commission-inclusive and
Nautilus's is not (Story 4.1, D-G), so it is printed beside the broker's
quantity and never compared.

Pure: no Nautilus, no SQL, no I/O (AR38). The live driver receives
:func:`compare` as an injected port rather than importing this module.
"""

from collections.abc import Iterable
from decimal import Decimal

from src.models.broker_state import BrokerPosition, BrokerState
from src.models.reconciliation import (
    CashLine,
    PositionLine,
    ReconciliationReport,
    SessionView,
)

ZERO = Decimal(0)


def compare(
    broker: BrokerState,
    local: SessionView,
    *,
    session_name: str,
    elapsed_ms: float,
) -> ReconciliationReport:
    """Compare the session's view with the broker's, line by line.

    Args:
        broker: IBKR's view (Story 4.1's ``read_broker_state``).
        local: The session's engine-cache view.
        session_name: For the report's header.
        elapsed_ms: The check's time from start to verdict.

    Returns:
        Every instrument either side holds and every currency either side
        reported, each with both sides; ``is_clean`` only when every line
        matches exactly.
    """
    return ReconciliationReport(
        session_name=session_name,
        trader_id=local.trader_id,
        account=broker.account,
        positions=_position_lines(broker.positions, local),
        cash=_cash_lines(broker, local),
        broker_retrieved_at=broker.retrieved_at,
        elapsed_ms=elapsed_ms,
        local_cash_recorded_at=local.cash_recorded_at,
        local_positions_skipped=local.positions_skipped,
    )


def _position_lines(
    broker_positions: Iterable[BrokerPosition], local: SessionView
) -> tuple[PositionLine, ...]:
    held = {position.instrument_id: position for position in broker_positions}
    expected = {position.instrument_id: position.quantity for position in local.positions}
    lines = []
    for instrument_id in sorted(held.keys() | expected.keys()):
        row = held.get(instrument_id)
        lines.append(
            PositionLine(
                instrument_id=instrument_id,
                local_quantity=expected.get(instrument_id, ZERO),
                broker_quantity=ZERO if row is None else row.quantity,
                broker_average_price=None if row is None else row.average_price,
                broker_symbol=None if row is None else row.symbol,
                broker_resolved=True if row is None else row.instrument_resolved,
            )
        )
    return tuple(lines)


def _cash_lines(broker: BrokerState, local: SessionView) -> tuple[CashLine, ...]:
    actual = {balance.currency: balance.total_cash for balance in broker.cash}
    recorded = {balance.currency: balance.total_cash for balance in local.cash}
    return tuple(
        CashLine(
            currency=currency,
            local_cash=recorded.get(currency),
            broker_cash=actual.get(currency),
        )
        for currency in sorted(actual.keys() | recorded.keys())
    )


def _signed(quantity: Decimal) -> str:
    return "0" if quantity == 0 else f"{quantity:+}"


def _render_position(line: PositionLine) -> str:
    price = "unknown" if line.broker_average_price is None else str(line.broker_average_price)
    text = (
        f"position {line.instrument_id} session={_signed(line.local_quantity)} "
        f"broker={_signed(line.broker_quantity)}"
    )
    if line.broker_quantity != 0:
        text += f" avg_price={price}"
    if not line.broker_resolved:
        text += f" (unresolved symbol={line.broker_symbol})"
    if line.matches:
        return f"{text} match"
    return f"{text} difference={_signed(line.difference)} DISCREPANCY"


def _render_cash(line: CashLine) -> str:
    local = "unknown" if line.local_cash is None else str(line.local_cash)
    actual = "none" if line.broker_cash is None else str(line.broker_cash)
    text = f"cash {line.currency} session={local} broker={actual}"
    if line.matches:
        return f"{text} match"
    if line.difference is None:
        return f"{text} DISCREPANCY"
    return f"{text} difference={_signed(line.difference)} DISCREPANCY"


def _cash_note(report: ReconciliationReport) -> str:
    """Why session cash can differ, and since when (Story 4.7, D-C)."""
    if all(line.local_cash is None for line in report.cash):
        return (
            "note: the session's engine never recorded a TotalCashValue, so its cash is unknown "
            "(never zero)"
        )
    recorded = report.local_cash_recorded_at
    when = "an unknown time" if recorded is None else recorded.isoformat()
    return (
        f"note: session cash as of {when} is the last TotalCashValue the session's engine "
        "received; for a stopped session that is its value when the session last ran, so a "
        "dividend, interest or another client's trade since then reads here"
    )


def render_report(report: ReconciliationReport) -> list[str]:
    """Operator-readable lines, ending in one ``RESULT:`` line; account masked."""
    lines = [
        f"reconcile session={report.session_name} trader_id={report.trader_id} "
        f"account={report.account} broker_read_at={report.broker_retrieved_at.isoformat()}"
    ]
    lines.extend(_render_position(line) for line in report.positions)
    lines.extend(_render_cash(line) for line in report.cash)
    if report.local_positions_skipped:
        # Without this, every broker line reads as a real mismatch after a
        # TWS_ACCOUNT change (PR #35 code review, P9).
        lines.append(
            f"note: {report.local_positions_skipped} position(s) in the session's engine cache "
            "are held for an account other than the one configured and were left out of the "
            "session side; if TWS_ACCOUNT changed since the session ran, the position lines "
            "above compare the wrong account"
        )
    counts = f"positions={len(report.positions)} currencies={len(report.cash)}"
    if report.is_clean:
        lines.append(
            f"RESULT: clean — positions and cash match IBKR exactly ({counts}) "
            f"elapsed_ms={report.elapsed_ms:.0f}"
        )
        return lines
    if any(not line.matches for line in report.cash):
        lines.append(_cash_note(report))
    position_count = sum(1 for line in report.positions if not line.matches)
    cash_count = sum(1 for line in report.cash if not line.matches)
    lines.append(
        f"RESULT: discrepancy — positions={position_count} cash={cash_count} line(s) differ "
        f"from IBKR ({counts}); nothing was changed elapsed_ms={report.elapsed_ms:.0f}"
    )
    return lines
