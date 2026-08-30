"""Node-facing mechanics a live session's runner sits on top of (Story 2.5).

Owns: waiting for a node to be *genuinely started* rather than merely
connected, the message that wait fails with, the refusal an unexplained account
verdict becomes, turning a frozen ``StrategySpec`` into a live ``Strategy``,
refusing a spec this phase cannot materialise at all, and reporting the
requested-versus-loaded instrument shortfall.

Does not own: the *sequence* those steps belong to, the event loop, or the
teardown — all of which are ``src/core/live_session_runner.py``'s (AR38). Nor
the steady-state tick (``src/core/live_session_steady_state.py``).

Split out of the runner for the same reason ``live_check_node`` was split out
of ``live_check_driver``: both stay inside CLAUDE.md's 500-line file limit
without deleting the explanations, and every comment here records something
read out of the nautilus-trader 1.220.0 wheel or paid for by a live run.

**No SQLAlchemy** (AR38) — this module is imported by the runner, whose purity
guard forbids it.
"""

import asyncio
import time
from collections.abc import Sequence
from typing import Any

from nautilus_trader.config import LoggingConfig
from nautilus_trader.live.node import TradingNode

from src.config import IBKRSettings
from src.core.live_check import BrokerUnreachableError, LiveCheckReport
from src.core.live_check_node import POLL_SECONDS, endpoint
from src.core.live_gate import GateRefusal, GateRefusalReason
from src.core.live_market_data import (
    LiveMarketDataError,
    instrument_ids_for,
    resolve_live_bar_types,
)
from src.core.live_node_builder import (
    NODE_TIMEOUT_CONNECTION,
    NODE_TIMEOUT_PORTFOLIO,
    NODE_TIMEOUT_RECONCILIATION,
)
from src.models.session import SessionSpec, StrategySpec

#: How long the whole of `node:build` + `node:connect` may take. Strictly
#: greater than Nautilus's own three pre-`trader.start()` waits
#: (60 + 30 + 10), because `node:connect` now waits for a post-condition of all
#: three. **Deliberately not** the check's `DEFAULT_CONNECT_TIMEOUT_SECONDS`
#: (60.0): a check waits only for `check_connected()`, so a node that connects
#: in 40s and reconciles in 25s would be called unreachable on a perfectly
#: healthy gateway. That constant also cannot be imported — it lives in
#: `src/cli/commands/live.py`, which imports the runner (circular) and imports
#: SQLAlchemy (fails this module's own purity guard).
#:
#: Relocated from `live_session_runner.py` (Story 2.6): the runner's own file
#: was at the 500-line budget and this module already owns "how a session's
#: node is configured". No re-export shim is kept — importers use this module.
DEFAULT_SESSION_CONNECT_TIMEOUT_SECONDS = 120.0

#: The IB reconnect budget a *session* installs, where a *check* installs "1".
#: `live_check_node` documents its own value as "a check policy, explicitly not
#: a session policy: a check exists to report what it found, not to outlast a
#: gateway restart." A 6.5-hour session started while the gateway is coming
#: back up should not be defeated by that; three attempts costs about 60s of
#: the 120s budget above and is spent only when the first attempt fails.
SESSION_CONNECTION_ATTEMPTS = "3"

#: Nautilus's own logging, passed explicitly rather than inherited (AC #9).
#: `log_level_file=None` means **no Nautilus file sink**: this repo's file
#: logging is structlog's (`logs/ntrader.log`), and a second Rust-side writer
#: would duplicate every line. `bypass_logging` stays False —
#: `kernel.py:253-257` raises `InvalidConfiguration` for True in a LIVE
#: environment, so it is not a way to silence the node.
SESSION_LOGGING = LoggingConfig(
    log_level="INFO", log_level_file=None, log_colors=True, use_pyo3=False
)

#: The message-bus topic every bar is published to (`data/engine.pyx:2327`
#: builds `f"data.bars.{bar_type}"`). The `*` glob matches every bar topic and
#: correctly excludes `data.quotes.*` — executed, not assumed.
BAR_TOPIC = "data.bars.*"

#: Used only when an account verifier *returns* a refusal carrying no reason —
#: unreachable via ``live_gate``'s own producers, which always populate it. A
#: value rather than an ``assert`` because ``python -O`` strips asserts, and
#: losing this would turn a refusal into an ``AttributeError`` on the
#: safety-critical path. Same posture as ``live_check_driver`` and
#: ``live_account_gate``, which each keep their own for the same reason.
UNEXPLAINED_ACCOUNT_REFUSAL = GateRefusal(
    reason=GateRefusalReason.ACCOUNT_VERIFICATION_ERROR,
    message=(
        "Account verification refused the connection but supplied no reason; refusing to "
        "continue against an unexplained refusal."
    ),
)


def refusal_from_report(report: LiveCheckReport) -> GateRefusal:
    """Turn ``preflight_gate``'s *returned* refusal into the exception's payload.

    ⚠️ ``preflight_gate`` refuses by **returning** a report, not by raising
    (``live_check.py:222`` is ``-> LiveCheckReport | None``), and the report
    carries a ``GateRefusalReason`` and a ``str`` — not the ``GateRefusal``
    ``GateRefusedError`` takes. Without this conversion the startup sequence
    would walk on to ``node:build``, where the builder's own gate raises,
    producing a log in which ``gate:static status=failed`` is followed by
    ``node:build status=started``.

    A bare ``GateRefusal`` rather than ``build_refusal``, whose whole
    ``GateDecision`` carries an *Optional* refusal; ``live_account_gate`` and
    ``live_check_driver`` build theirs the same way. ``build_refusal`` remains
    the only sanctioned constructor for a ``GateDecision``, which this is not.

    The reason fallback is unreachable via ``evaluate_gate``, whose only
    refusal producer always populates it — see :data:`UNEXPLAINED_ACCOUNT_REFUSAL`
    for why that is a value and not an ``assert``.
    """
    return GateRefusal(
        reason=report.refusal_reason or GateRefusalReason.ACCOUNT_VERIFICATION_ERROR,
        message=report.message,
    )


def engines_connected(node: TradingNode) -> bool:
    """Whether both engines report a completed connect."""
    return node.kernel.data_engine.check_connected() and node.kernel.exec_engine.check_connected()


async def await_trader_started(
    node: TradingNode,
    run_task: asyncio.Task,
    deadline: float,
    timeout: float,
    settings: IBKRSettings,
    log: Any,
) -> None:
    """Block until the trader is genuinely running, or call the broker unreachable.

    Polls **both** engines *and* ``trader.is_running``. ``check_connected()``
    alone is the wrong observable here: it turns True inside
    ``_await_engines_connected()``, before reconciliation, before portfolio
    init, before ``trader.start()`` — which is exactly the race the Epic 1
    retrospective flagged. ``Trader._start()`` iterates actors, then
    strategies, then exec algorithms (``trading/trader.py:250-270``), and
    ``Component.start()`` reaches ``RUNNING`` only after its action returns
    (``common/component.pyx:1877-1907``, ``:2140-2143``), so ``is_running`` is
    a post-condition of the whole of ``start_async`` rather than an
    intermediate state.

    Polled rather than awaited on an exception because **none of the three
    failures raises**: connect timeout, reconciliation failure and
    portfolio-init timeout each log and ``return`` from
    ``kernel.start_async()`` (``system/kernel.py:1012``, ``:1014``, ``:1024``).
    Worse, the last two do not complete the run task either — ``run_async``
    continues past the early return to ``await asyncio.gather(*queue_tasks)``
    over eight tasks that never finish (``live/node.py:343-370``). So
    ``run_task.done()`` is kept, because it catches a node that genuinely died,
    but **the deadline is the only signal for those two**.

    Args:
        node: The running node.
        run_task: The task driving ``node.run_async()``.
        deadline: A ``time.monotonic()`` instant, set by the caller *before*
            the node was built so the build's own connect attempt is spent from
            the same budget. It may already be in the past on entry, which is
            correct: that time was spent trying to connect.
        timeout: The budget that produced ``deadline``, for the message only.
        settings: For the endpoint in the messages.
        log: A bound structlog logger.

    Raises:
        BrokerUnreachableError: The node died, or the deadline passed.
    """
    while time.monotonic() < deadline:
        if run_task.done():
            run_task.result()
            raise BrokerUnreachableError(
                f"the node stopped running before the session started at "
                f"{endpoint(settings)} — the gateway refused or dropped the connection"
            )
        if engines_connected(node) and node.trader.is_running:
            log.info("session.connected", endpoint=endpoint(settings))
            return
        await asyncio.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))

    raise BrokerUnreachableError(deadline_message(settings, timeout))


def deadline_message(settings: IBKRSettings, timeout: float) -> str:
    """Name all three possibilities, because they are observationally identical.

    ⚠️ ``live_check_node.await_connected``'s *"is IB Gateway or TWS running on
    the configured paper port?"* must **not** be copied here. A reconciliation
    failure and a portfolio-init timeout produce exactly this observable, and
    that message would send an operator to restart a perfectly healthy gateway.
    """
    return (
        f"the session did not start within {timeout:g}s at {endpoint(settings)}. Three causes "
        f"produce this identically, and Nautilus logs rather than raises for all three: the "
        f"gateway never accepted the connection (timeout_connection={NODE_TIMEOUT_CONNECTION:g}s), "
        f"execution reconciliation did not complete "
        f"(timeout_reconciliation={NODE_TIMEOUT_RECONCILIATION:g}s), or portfolio initialisation "
        f"timed out (timeout_portfolio={NODE_TIMEOUT_PORTFOLIO:g}s). Check the Nautilus log lines "
        f"above this one before restarting anything."
    )


def validate_spec_is_materialisable(spec: SessionSpec) -> None:
    """Refuse a spec this phase's strategy configurations cannot express.

    Called at ``gate:static``, before any socket opens, following Epic 1 retro
    Key Insight #2 (*"config-time validation beats kernel-time validation"*) —
    so the failure costs no connection. ``SessionSpec`` legitimately allows
    more than one bar type per strategy because Story 2.1 modelled the list for
    later; ``SMAConfig`` takes exactly one ``bar_type`` and one
    ``instrument_id`` with no defaults, so a two-bar-type entry cannot be built
    at all. This is the runner declining to run something, not a model change
    (*Judgment call #4*).

    Raises:
        LiveMarketDataError: A strategy names other than exactly one bar type.
    """
    for strategy_spec in spec.strategies:
        if len(strategy_spec.bar_types) != 1:
            raise LiveMarketDataError(
                f"Strategy {strategy_spec.strategy_id!r} names "
                f"{len(strategy_spec.bar_types)} bar types "
                f"({', '.join(strategy_spec.bar_types)}), and this phase's strategy "
                "configurations take exactly one instrument and one bar type. Create one "
                "session per instrument, or one strategy entry per bar type."
            )


def materialise_strategy(strategy_spec: StrategySpec) -> Any:
    """Turn a frozen ``StrategySpec`` into a live Nautilus ``Strategy``.

    Follows ``BacktestOrchestrator._create_strategy``
    (``src/core/backtest_orchestrator.py:443-457``) — **but deliberately does
    not re-run ``StrategyLoader.build_strategy_params``**.
    ``StrategySpec.parameters`` is already that function's frozen output
    (``src/models/session.py``), and re-running it would let today's settings
    silently change what a multi-week forward test is running, which is the
    exact failure FR14 and FR53 exist to prevent.

    ``SMAParameters`` carries neither ``instrument_id`` nor ``bar_type``
    (``src/models/strategy.py:27-37``) while ``SMAConfig`` requires both with
    no defaults (``src/core/strategies/sma_crossover.py:37-42``), so those two
    come from the spec's own bar type — the same union the backtest path
    performs from its bar data.

    Imported lazily for one reason only: ``src.core.strategy_factory`` triggers
    registry discovery on first use, and importing it at module scope would
    make that a cost of importing the runner.
    """
    from nautilus_trader.model.data import BarType

    from src.core.strategy_factory import StrategyLoader

    bar_type = BarType.from_str(strategy_spec.bar_types[0])
    params = dict(strategy_spec.parameters) | {
        "instrument_id": bar_type.instrument_id,
        "bar_type": bar_type,
    }
    return StrategyLoader.create_strategy(strategy_spec.strategy_id, params)


def request_node_stop(
    node: TradingNode | None,
    loop: asyncio.AbstractEventLoop | None,
    log: Any,
    *,
    signal_name: str,
    trader_started: bool,
) -> None:
    """Log the stop, then ask the node to stop — never call ``node.stop()``
    directly from a signal handler (Story 2.6).

    This is ``SessionStopSignals``'s ``on_stop`` callback, run on the main
    thread, synchronously, inside its ``_handle`` — between two arbitrary
    bytecodes. Kept to the two things that module's docstring permits: one
    structlog record, and handing off through ``loop.call_soon_threadsafe``,
    which is safe to call from a handler where re-entering ``node.stop()``'s
    own ``create_task``/``run_until_complete`` branch is not
    (``live/node.py:374-388``).

    Guarded against both a node that does not exist yet (a signal during
    ``gate:static`` or the start of ``node:build``) and a loop already closed
    (the narrow window after ``shutdown()`` but before ``restore()``).
    """
    log.info("session.stopped", signal=signal_name, trader_started=trader_started)
    if node is not None and loop is not None and not loop.is_closed():
        loop.call_soon_threadsafe(node.stop)


def unsubscribe_runner_topics(
    node: TradingNode | None, subscriptions: Sequence[tuple[str, Any]], log: Any
) -> None:
    """Cancel every message-bus subscription the runner made, on the stop path.

    ``LiveBarObserver.on_stop()`` already unsubscribes exactly the bar types
    it dispatched, and ``Trader._stop()`` runs actors before strategies —
    both already handled. What nothing cancels is the runner's *own*
    subscriptions, made in ``_phase_subscribe``.

    Takes the whole list rather than one named handler (review 2026-08-30).
    This was ``unsubscribe_bar_topic(node, steady_state, log)``, which
    cancelled exactly the steady state's ``note_bar`` — so when Story 3.2
    added the order observer's bar anchor and its ``events.order*`` handler,
    both silently kept firing through teardown and ``order.submitted`` records
    could land after ``session.stopped``. Iterating what the runner recorded
    means the next subscription added is cancelled without anyone remembering
    to come back here.

    Guarded per subscription: a raising ``unsubscribe`` must not pre-empt the
    node teardown behind it, nor stop the remaining cancellations. A no-op
    when ``subscribe`` never ran (a stop before that phase) — there is nothing
    to cancel.
    """
    if node is None:
        return
    for topic, handler in subscriptions:
        if handler is None:
            continue
        try:
            node.trader.unsubscribe(topic, handler)
        except Exception as exc:  # noqa: BLE001 - must never pre-empt the teardown behind it
            log.error("session.unsubscribe_failed", error_type=type(exc).__name__, topic=topic)


def report_instrument_shortfall(node: TradingNode, bar_types: tuple[str, ...], log: Any) -> None:
    """Say which requested contracts IBKR never qualified.

    ``InteractiveBrokersInstrumentProvider.load_ids_with_return_async``
    *skips* a contract that will not qualify (``providers.py:243-265``) —
    nothing raises and nothing reports, so a connected session with zero bars
    for that subscription is indistinguishable from a healthy one. This
    difference is the only visible trace of it, which is why it is logged.
    Copied in shape from ``live_check_driver._record_instruments``, which
    exists for the same reason.
    """
    requested = instrument_ids_for(resolve_live_bar_types(bar_types))
    loaded = {str(instrument.id) for instrument in node.cache.instruments()}
    missing = [name for name in requested if name not in loaded]
    if missing:
        log.warning(
            "session.instruments",
            requested=list(requested),
            missing=missing,
            reason="IBKR did not qualify these contracts; their subscriptions cannot deliver",
        )
