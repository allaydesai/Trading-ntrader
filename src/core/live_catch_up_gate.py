"""No order acts on a bar the strategy handles long after it closed (Story 5.9).

**Why there is a catch-up.** After a reconnect the IB adapter resubscribes each
bar subscription (:mod:`src.core.live_bar_resubscribe`). Its history request
re-delivers every bar that closed during the outage in one burst: its only guard
drops bars older than the last one it saw (``client/market_data.py:1142-1151``),
which an outage never touches. PO ruling, Allay 2026-10-12: *ignore the missed
prices for trading* — the indicators see every replayed bar, but no order may act
on one.

**How one is recognised.** A replayed bar is stamped with its own period end, not
its arrival (``market_data.py:1175-1176``), so neither ``ts_init`` nor the
freshness guard can tell it from a live bar. Only the wall clock at the moment
the strategy handles it can: a bar handled more than :data:`CATCH_UP_GRACE_SECONDS`
after its period closed is a catch-up bar. PO ruling R1 accepted this as the
operational form of "closed before ``connection.restored``". It holds whichever
side of the reconnect grant the burst lands on, which nothing orders (story F9).
A live IB minute bar is handled about 5 s after its close, a session's first bar
13-26 s after (measured, story Task 1.3).

**Where it sits.** :func:`install_catch_up_gate` wraps the strategy's own
``handle_bar``, so the gate notes the exact bar the strategy is about to react
to, then hands the order path a monitor of its own: withheld when the connection
monitor withholds, or when the bar now being handled is a catch-up bar. An order
raised outside a bar (a fill, a timer) is judged against the strategy's last bar,
which is conservative: a strategy still catching up does not trade on an event
either.

**What it records.** :data:`CATCH_UP_ORDER_WITHHELD_EVENT` when the gate, not the
monitor, is why an order was withheld (the order path's own ``order.suppressed``
follows it unchanged), and one :data:`CATCH_UP_EVENT` per burst, when the first
live bar follows it. A republished bar (``ts_event`` not newer than the last seen
for its bar type, e.g. the adapter's re-delivery of the previous session's last
bar after every RTH gap) is judged but never counted.

Framework-free, like :mod:`src.core.live_order_path`, which imports it: bars,
strategies and monitors are duck-typed, and the standard library is the only
import. It never raises into the strategy's bar handler.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

__all__ = [
    "CATCH_UP_EVENT",
    "CATCH_UP_GRACE_SECONDS",
    "CATCH_UP_ORDER_WITHHELD_EVENT",
    "CatchUpGate",
    "install_catch_up_gate",
]

#: One burst of catch-up bars has ended: a live bar followed it.
CATCH_UP_EVENT = "bars.catch_up"

#: An order-creating call was withheld because the bar being handled is a
#: catch-up bar (and the connection monitor would have let it through).
CATCH_UP_ORDER_WITHHELD_EVENT = "bars.catch_up_order_withheld"

#: ``live_market_data.DEFAULT_DELAYED_DATA_GRACE_SECONDS``, duplicated so this
#: module stays framework-free; pinned equal to it by test (PO ruling R1: "late"
#: means the same to the freshness guard and to this gate).
CATCH_UP_GRACE_SECONDS = 120.0

_NS = 1_000_000_000
_DETAIL = "the bar being handled closed during an outage; no order may act on it (Story 5.9)"


@dataclass
class _Burst:
    count: int
    first_ts_event: int
    last_ts_event: int


class CatchUpGate:
    """The order path's monitor: the connection monitor, plus catch-up bars."""

    def __init__(
        self,
        monitor: Any,
        *,
        clock: Callable[[], int],
        log: Any,
        grace_seconds: float = CATCH_UP_GRACE_SECONDS,
    ) -> None:
        """Args:
        monitor: Anything with a ``submission_withheld`` bool property.
        clock: The wall clock, in Unix nanoseconds, read when a bar is handled.
        log: A structlog logger, ideally already bound to ``session_id``.
        grace_seconds: How long past its close a bar may be handled and still
            count as live.
        """
        self._monitor = monitor
        self._clock = clock
        self._log = log
        self._grace_ns = int(grace_seconds * _NS)
        self._last_ts_event: dict[str, int] = {}
        self._bursts: dict[str, _Burst] = {}
        self._catching_up: tuple[str, int] | None = None

    @property
    def submission_withheld(self) -> bool:
        """Withheld by the monitor, or because the bar now handled is catch-up.

        Read once per order-creating call by the order path, so the gate's own
        record is written once per order it withholds.
        """
        if self._monitor.submission_withheld:
            return True
        if self._catching_up is None:
            return False
        bar_type, ts_event = self._catching_up
        self._log.warning(
            CATCH_UP_ORDER_WITHHELD_EVENT,
            bar_type=bar_type,
            ts_event=_iso_utc(ts_event),
            detail=_DETAIL,
        )
        return True

    def note_bar(self, bar: Any) -> None:
        """Judge the bar the strategy is about to handle. Never raises."""
        try:
            self._note(bar)
        except Exception:  # noqa: BLE001 - runs inside the strategy's bar handler
            pass

    def _note(self, bar: Any) -> None:
        key = str(bar.bar_type)
        ts_event = int(bar.ts_event)
        closed_at = ts_event + int(bar.bar_type.spec.timedelta.total_seconds() * _NS)
        late = self._clock() - closed_at > self._grace_ns
        self._catching_up = (key, ts_event) if late else None
        last = self._last_ts_event.get(key)
        if last is not None and ts_event <= last:
            return  # a republish: judged above, never counted
        self._last_ts_event[key] = ts_event
        if late:
            self._extend_burst(key, ts_event)
        else:
            self._close_burst(key)

    def _extend_burst(self, key: str, ts_event: int) -> None:
        burst = self._bursts.get(key)
        if burst is None:
            self._bursts[key] = _Burst(count=1, first_ts_event=ts_event, last_ts_event=ts_event)
            return
        burst.count += 1
        burst.last_ts_event = ts_event

    def _close_burst(self, key: str) -> None:
        burst = self._bursts.pop(key, None)
        if burst is None:
            return
        self._log.info(
            CATCH_UP_EVENT,
            bar_type=key,
            count=burst.count,
            first_ts_event=_iso_utc(burst.first_ts_event),
            last_ts_event=_iso_utc(burst.last_ts_event),
        )


def install_catch_up_gate(strategy: Any, monitor: Any, log: Any) -> CatchUpGate:
    """Wrap ``strategy.handle_bar`` with a gate over ``monitor``; return the gate.

    Must run before ``Trader.add_strategy``, the ``install_order_path``
    constraint: ``subscribe_bars`` binds ``self.handle_bar`` when the strategy's
    ``on_start`` runs, and honours an instance attribute (the warm-up seam's
    precedent, ``live_session_warmup._install_seam``). The clock is read from
    the strategy at handling time, when it is registered. A strategy without
    ``handle_bar`` (a test stub) gets a gate that only ever defers to ``monitor``.
    """
    gate = CatchUpGate(monitor, clock=lambda: strategy.clock.timestamp_ns(), log=log)
    base = getattr(strategy, "handle_bar", None)
    if base is None:
        return gate

    def handle_bar(bar: Any) -> Any:
        gate.note_bar(bar)
        return base(bar)

    setattr(strategy, "handle_bar", handle_bar)
    return gate


def _iso_utc(ts_ns: int) -> str:
    """Render Unix nanoseconds as an ISO-8601 UTC string, to the microsecond."""
    whole = datetime.fromtimestamp(ts_ns // _NS, tz=UTC)
    return whole.replace(microsecond=(ts_ns % _NS) // 1000).isoformat()
