"""Run one paper-trading session in the foreground, in AR39's phase order.

Owns: ``LiveSessionRunner`` — the event loop a session lives on, the eight
ordered startup phases, the serve loop that is the rest of the session's life,
the single teardown path every failure and every stop goes through (AR38), and
(Story 2.6) the *wiring* of the stop signal into that sequence: arming and
re-arming ``SessionStopSignals``, the phase-boundary stop checks, and the
subscription cancellation on the way down.

Does not own: the phase *vocabulary* (``live_session_phases``), the node-facing
mechanics it sits on (``live_session_node``), the steady-state tick
(``live_session_steady_state``), node assembly (``live_node_builder``), the
static gate (``live_check``) or the account gate (``live_account_gate``) — each
of which logs its own half of the phase it owns — the database
(``src/services/session_record.py``, reached only through AR32's port), the
stop-signal *policy* (``live_session_signals`` — Judgment call #8), strategy-
failure containment (Story 2.7), ``status``/``list`` (Story 2.8), reconcile and
warm-up (Epic 4, no-op placeholders here), or orders and trades (Epic 3).

**No SQLAlchemy, ever** (AR38). The runner holds a ``SessionRecordPort``, never
a session, a repository or an engine. Kept true in both forms by an AST scan
and a fresh-subprocess ``sys.modules`` check; ``nautilus_trader`` is
*permitted* here, so the polarity is inverted from ``session_service``'s guard.

**The runner owns an explicit event loop; it is not run under ``asyncio.run``**
(*Judgment call #1*, departing from ``architecture.md:311``).
``TradingNode.dispose()`` calls ``loop.stop()`` whenever it finds the loop
running (``live/node.py:451-458``), which is fatal from a coroutine executing
on that loop — *"which is what Story 1.3 lost a live run to"*
(``live_check_driver.py:180-184``). :meth:`LiveSessionRunner.run` is therefore
synchronous and drives everything through ``run_until_complete``; the
architecture's intent (foreground, no daemon, one process) is unchanged.

**Three orderings are load-bearing and easy to undo by accident.**

1. *The connect deadline starts before the node factory call.* The build is
   where the adapter's own connect attempt happens — an unreachable gateway
   took **115 seconds** to say so when the two budgets were additive.
2. *The ``finally`` cancels and awaits the heartbeat before ``shutdown``.*
   The record write runs on the steady state's **own** pool, not the loop's
   default one that ``dispose()`` joins with ``wait=True`` (decision D2) — see
   :meth:`_stop_heartbeat` and ``SessionSteadyState._write_activity``.
3. *The signals are armed three times, not twice* — before ``gate:static``,
   again the instant the node factory returns (**inside** ``node:build``, before
   the synchronous connect), and again when the phase ends. Node construction
   clobbers whatever was armed before it, and re-arming only after the phase
   left its slowest stretch deaf; see :meth:`_phase_node_build`.

**What ``session_id`` does and does not reach.** Bound two ways — this
runner's logger and ``structlog.contextvars`` — so every structlog record
carries it. It does **not** reach Nautilus's own stdout, which is Rust-side.

Known, accepted limits, stated rather than implied: a clean phase log does
**not** mean reconciliation happened; a heartbeat proves a process is writing,
not that it is trading; ``confirm_state_reestablished`` is deliberately never
called until Epic 4 has a real reconciliation to follow (*Judgment call #6*);
and stopping does not yet leave positions alone end to end, because
``sma_crossover.on_stop()`` still calls ``close_all_positions()`` — Story 3.1's
to remove (see AC #2's warning in Story 2.6).
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from uuid import UUID

import structlog
from nautilus_trader.config import CacheConfig, LoggingConfig
from nautilus_trader.live.node import TradingNode
from structlog.contextvars import bind_contextvars, unbind_contextvars

from src.config import IBKRSettings
from src.core.live_account_gate import verify_connected_account
from src.core.live_bar_observer import LiveBarObserver, build_bar_observer_config
from src.core.live_check import GATE_PHASE, preflight_gate
from src.core.live_check_driver import AccountVerifier, NodeFactory
from src.core.live_check_node import (
    build_clients,
    current_event_loop,
    restore_event_loop,
    shutdown,
)
from src.core.live_connection_monitor import ConnectionMonitor
from src.core.live_connection_probe import read_ibkr_connection_status
from src.core.live_node_builder import GateRefusedError, build_trading_node
from src.core.live_session_controller import build_session_controller_config
from src.core.live_session_node import (
    BAR_TOPIC,
    DEFAULT_SESSION_CONNECT_TIMEOUT_SECONDS,
    SESSION_CONNECTION_ATTEMPTS,
    SESSION_LOGGING,
    UNEXPLAINED_ACCOUNT_REFUSAL,
    await_trader_started,
    materialise_strategy,
    refusal_from_report,
    report_instrument_shortfall,
    request_node_stop,
    unsubscribe_bar_topic,
    validate_spec_is_materialisable,
)
from src.core.live_session_phases import PHASE_EVENT, phase
from src.core.live_session_record import SessionReclaimedError, SessionRecordPort
from src.core.live_session_signals import SessionStopRequested, SessionStopSignals
from src.core.live_session_steady_state import (
    DEFAULT_NO_BARS_AFTER_SECONDS,
    ConnectionReader,
    SessionSteadyState,
    StartupHeartbeat,
    join_heartbeat,
    release_record,
)
from src.core.live_strategy_guard import (
    GUARD_FAILED_EVENT,
    NoStrategyStartedError,
    StrategyFailure,
    StrategyGuard,
)
from src.core.live_trader_id import derive_trader_id
from src.models.session import DEFAULT_HEARTBEAT_INTERVAL_SECONDS, SessionSpec

ClientBuilder = Callable[..., None]


def _utc_now() -> datetime:
    """The repo's house clock idiom."""
    return datetime.now(timezone.utc)


class LiveSessionRunner:
    """Drive one session through AR39's startup sequence and then serve it.

    Args:
        settings: Loaded **IBKR** settings, matching ``build_trading_node``.
        session_id: The session's UUID business key. Used for the ``trader_id``
            derivation and the log binding only — never a database lookup.
        spec: The frozen specification, read back through ``from_stored``.
        record: AR32's record port, already bound to this session and to the
            instant its ``-> running`` transition stamped.
        started_at: That same instant, for the log line only.
        cache: The Redis engine cache. ``None`` runs on an in-memory cache,
            throwing away AR10's per-session namespace — the CLI always passes one.
        logging: Nautilus's logging config; defaults to :data:`SESSION_LOGGING`.
        connect_timeout: The whole build-and-connect budget.
        heartbeat_interval_seconds: AR32's cadence.
        no_bars_after_seconds: The first-bar watchdog window.
        time_source: Aware ``datetime`` clock, injected for deterministic tests.
        sleeper: How the heartbeat waits one interval, injected so a test drives
            780 ticks without waiting 6.5 hours.
        node_factory, account_verifier, client_builder, connection_reader: The
            four broker-facing seams (``live_check_driver.py:124-125``).
        stop_signals: The stop-signal policy (Story 2.6), injected so a test
            needs no real signal or event loop; ``None`` builds the production
            :class:`~src.core.live_session_signals.SessionStopSignals`.
    """

    def __init__(
        self,
        settings: IBKRSettings,
        *,
        session_id: UUID,
        spec: SessionSpec,
        record: SessionRecordPort,
        started_at: datetime,
        cache: CacheConfig | None = None,
        logging: LoggingConfig | None = None,
        connect_timeout: float = DEFAULT_SESSION_CONNECT_TIMEOUT_SECONDS,
        heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        no_bars_after_seconds: float = DEFAULT_NO_BARS_AFTER_SECONDS,
        time_source: Callable[[], datetime] = _utc_now,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        node_factory: NodeFactory = build_trading_node,
        account_verifier: AccountVerifier = verify_connected_account,
        client_builder: ClientBuilder = build_clients,
        connection_reader: ConnectionReader = read_ibkr_connection_status,
        stop_signals: SessionStopSignals | None = None,
    ) -> None:
        self._settings = settings
        self._session_id = session_id
        self._spec = spec
        self._record = record
        self._started_at = started_at
        self._cache = cache
        self._logging = SESSION_LOGGING if logging is None else logging
        self._connect_timeout = connect_timeout
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._no_bars_after_seconds = no_bars_after_seconds
        self._time_source = time_source
        self._sleeper = sleeper
        self._node_factory = node_factory
        self._account_verifier = account_verifier
        self._client_builder = client_builder
        self._connection_reader = connection_reader
        self._trader_id = derive_trader_id(session_id)
        self._log = structlog.get_logger(__name__).bind(session_id=str(session_id))
        # Story 2.7. The runner *wires* the containment; the policy is the
        # guard's (see `live_strategy_guard`). It is built here rather than in
        # `_phase_trading` so the CLI can read `contained_failures` off a runner
        # whose trading phase never ran. The account string arms AC #8's
        # configured-value redaction without the guard reading settings.
        self._guard = StrategyGuard(
            log=self._log, time_source=self._time_source, account=settings.tws_account
        )
        self._signals = (
            stop_signals
            if stop_signals is not None
            else SessionStopSignals(log=self._log, on_stop=self._request_node_stop)
        )

        # Mutable run state, all of it set by the phases and read by the
        # teardown. `_deadline` is monotonic seconds, not a datetime.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._node: TradingNode | None = None
        self._run_task: asyncio.Task | None = None
        self._heartbeat: asyncio.Task | None = None
        self._startup_heartbeat: StartupHeartbeat | None = None
        self._steady_state: SessionSteadyState | None = None
        self._monitor: ConnectionMonitor | None = None
        self._deadline, self._trader_started, self._ownership_lost = 0.0, False, False
        self._record_release_failed = False
        self._shutdown_problems: list[str] = []

    @property
    def trader_started(self) -> bool:
        """Whether the ``trading`` phase completed, as the runner's own fact —
        never inferred from a ``ComponentState`` name: ``READY`` is reachable
        again after a reset (Epic 1 retro Action Item #5).
        """
        return self._trader_started

    @property
    def stop_signal(self) -> str | None:
        """The name of the signal that stopped this run, or ``None``.

        ``None`` when the run ended for any other reason — see
        :attr:`stopped_by_signal`, which is the fact to branch on.
        """
        return self._signals.signal_name

    @property
    def shutdown_problems(self) -> list[str]:
        """What the teardown could not do, if anything (decision D4).

        Logged at WARNING and otherwise discarded before this: a non-empty list
        means the node may still hold the broker socket and the live client id,
        so the CLI warns — still exit 0, as AC #9's failed release does.
        """
        return list(self._shutdown_problems)

    @property
    def stopped_by_signal(self) -> bool:
        """Whether a stop signal is what ended this run (decision D4).

        ``run()`` returning is not proof that one did: ``run_async`` swallows
        cancellation (``live/node.py:371-373``), so a node that died on its own
        returns cleanly too. This is how the CLI tells the two apart.
        """
        return self._signals.signal_name is not None

    @property
    def record_release_failed(self) -> bool:
        """AC #9: whether the final ``-> stopped`` write failed for a reason
        other than a reclaim — the CLI's cue to print an operator warning.
        """
        return self._record_release_failed

    @property
    def contained_failures(self) -> tuple[StrategyFailure, ...]:
        """Every strategy failure contained during this run (Story 2.7).

        Read by the CLI after ``run()`` returns, so an operator watching a stop
        is told which strategies stopped trading and when — the in-process half
        of the visibility ``runtime_flags`` provides across processes. Empty on
        a clean run, which is the common case and prints nothing.
        """
        return self._guard.failures

    @property
    def all_strategies_failed(self) -> bool:
        """Whether every strategy this session started with was contained.

        The CLI branches its report on this (review fix, 2026-08-23): telling
        an operator "the other strategies were unaffected" when every strategy
        failed — or when the session only ever had one — is an affirmative
        falsehood in a safety report.
        """
        return bool(self._guard.all_failed)

    def run(self) -> None:
        """Start the session and serve it until the node stops. Blocking.

        Raises:
            GateRefusedError: Either gate refused (exit 3).
            BrokerUnreachableError: Broker never connected, or trader never
                started, inside the budget (exit 4).
            RedisUnreachableError: The engine cache is unusable (exit 1).
            SessionReclaimedError: Another process took the session mid-run.
            Exception: Whatever a phase raised, unchanged. A stop signal never
                raises out of here — see :class:`SessionStopRequested`.
        """
        bind_contextvars(session_id=str(self._session_id))
        previous_loop = current_event_loop()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        # The phases outlast the 90s staleness threshold — see `StartupHeartbeat`.
        self._startup_heartbeat = StartupHeartbeat(
            record=self._record,
            log=self._log,
            time_source=self._time_source,
            interval_seconds=self._heartbeat_interval_seconds,
        )
        try:
            self._startup_heartbeat.start()
            # Armed before any phase (module docstring, load-bearing ordering
            # #3), and INSIDE the `try`: `arm()` can raise, and outside it that
            # raise skipped the whole `finally` and left the row `running`.
            self._signals.arm(loop)
            self._phase_gate_static()
            self._signals.raise_if_requested()
            try:
                self._phase_node_build()
            finally:
                # Re-armed even when the build RAISED: on the failure path the
                # kernel otherwise kept the signals for a ~40s teardown that
                # only SIGKILL could end. Idempotent; the phase re-arms once
                # more mid-flight — see its docstring.
                self._signals.arm(loop)
            self._signals.raise_if_requested()
            self._phase_node_connect()
            self._signals.raise_if_requested()
            loop.run_until_complete(self._phase_gate_account())
            self._signals.raise_if_requested()
            self._phase_reconcile()
            self._signals.raise_if_requested()
            self._phase_warmup()
            self._signals.raise_if_requested()
            self._phase_subscribe()
            self._signals.raise_if_requested()
            self._phase_trading()
            self._signals.raise_if_requested()
            loop.run_until_complete(self._serve())
        except SessionStopRequested:
            # A stop noticed at a phase boundary. A stop is a success, not a
            # failure — the `finally` does the whole of the teardown, exactly
            # as it would for a stop that ended `_serve()` via `node.stop()`.
            pass
        except SessionReclaimedError:
            # Recorded before the `finally` runs, because it is what decides
            # whether the teardown may touch the row at all. Re-raised
            # unchanged: a session taken away mid-run has not succeeded.
            self._ownership_lost = True
            raise
        except Exception as exc:
            # A stop was requested and then a phase failed BECAUSE of it.
            # `raise_if_requested()` fires only between phases, but three of
            # the eight run the loop, and a signal inside one of those stops
            # the node underneath the phase still awaiting it — reproduced:
            # a Ctrl-C during `node:connect` raised `BrokerUnreachableError`
            # and exited 4 blaming a healthy gateway, where AC #1 wants 0.
            # Demoted, never silently; with no stop requested it propagates.
            if not self._signals.requested:
                raise
            self._log.info(
                "session.stop_superseded_failure",
                error_type=type(exc).__name__,
                signal=self._signals.signal_name,
                detail="a phase failed after a stop was requested; treated as part of the stop",
            )
        finally:
            try:
                self._unsubscribe()
                self._stop_heartbeat(loop)
                self._shutdown_problems = shutdown(self._node, self._run_task, loop)
                if self._shutdown_problems:
                    self._log.warning("session.shutdown_problems", problems=self._shutdown_problems)
                # Closing the loop un-installed every handler it registered,
                # leaving `_finish_record()`'s Postgres round trip unprotected.
                self._signals.rearm_process_handlers()
                self._flush_contained_failures()
                self._finish_record()
                restore_event_loop(previous_loop)
            finally:
                # Its own `finally`: every statement above can raise, and a
                # raise there leaked `_handle` and left SIGABRT at `SIG_DFL`
                # for the rest of the interpreter's life.
                self._signals.restore()
                unbind_contextvars("session_id")

    def _request_node_stop(self, signal_name: str) -> None:
        """``SessionStopSignals``'s ``on_stop`` callback; see :func:`request_node_stop`."""
        request_node_stop(
            self._node,
            self._loop,
            self._log,
            signal_name=signal_name,
            trader_started=self._trader_started,
        )

    def _unsubscribe(self) -> None:
        """Cancel the runner's own bar-topic subscription; see :func:`unsubscribe_bar_topic`."""
        unsubscribe_bar_topic(self._node, self._steady_state, self._log)

    # The eight AR39 phases, in PHASE_SEQUENCE order. Separately named and
    # separately patchable is a CONTRACT: the landmine test monkeypatches the
    # later phases to prove no phase runs after a failure, and Story 2.6
    # attaches its stop path to a single named teardown.

    def _phase_gate_static(self) -> None:
        """Layer 1, plus the config-time checks that cost no socket.

        Two record owners: ``preflight_gate`` emits only the terminal one, so
        ``started`` is emitted here and ``failed`` only when the failure is
        *ours*. ``preflight_gate`` **refuses by returning**, not by raising —
        skipping the conversion below would let the sequence walk on to
        ``node:build`` after a refusal, which AC #2 forbids.
        """
        self._log.info(PHASE_EVENT, phase=GATE_PHASE, status="started")
        try:
            validate_spec_is_materialisable(self._spec)
            report = preflight_gate(self._settings, None)
        except BaseException as exc:
            self._log.error(
                PHASE_EVENT, phase=GATE_PHASE, status="failed", error_type=type(exc).__name__
            )
            raise
        if report is not None:
            # `preflight_gate` has already logged this phase's `failed` record.
            raise GateRefusedError(refusal_from_report(report))

    def _phase_node_build(self) -> None:
        """Build the node and its clients, inside the connect budget.

        The deadline is taken **before** the factory call. See the module
        docstring's first load-bearing ordering.

        ⚠️ **The re-arm is inside this phase, not after it** (review fix,
        2026-08-22, decision D1). ``NautilusKernel._setup_loop`` runs during
        ``TradingNode.__init__`` — inside ``self._node_factory(...)`` — and
        hands all three signals to ``loop.add_signal_handler``, which installs
        asyncio's ``_sighandler_noop`` process-wide. Re-arming only *after*
        the phase left the rest of it kernel-owned, above all
        ``self._client_builder(...)``, whose ``node.build()`` drives the
        adapter's synchronous 3-attempt reconnect with the loop stopped. A
        Ctrl-C there was **silently discarded** — measured
        ``requested=False, count=0`` — because the later re-arm drains the
        pending wakeup to ``_noop`` itself. That is the regression this story
        exists for (Dev Notes finding #2; P7 criterion #3) and it survived
        implementation. Residual: the tail of ``TradingNode.__init__``, which
        performs no I/O, is knowingly accepted.
        """
        with phase(self._log, "node:build"):
            self._deadline = time.monotonic() + self._connect_timeout
            try:
                self._node = self._node_factory(
                    self._settings,
                    trader_id=self._trader_id,
                    bar_types=list(self._spec.subscription_bar_types),
                    # No declarative observer: it is registered at `subscribe`,
                    # after the gate — which is what makes AC #8's "zero
                    # non-controller actors when gate:account decides" true.
                    bar_observer=None,
                    cache=self._cache,
                    logging=self._logging,
                    controller=build_session_controller_config(),
                    loop=self._loop,
                )
            finally:
                # Take the signals back BEFORE the synchronous connect below.
                # `finally`, so a factory that raises after `_setup_loop` but
                # before returning does not leave them with the kernel.
                assert self._loop is not None
                self._signals.arm(self._loop)
            self._client_builder(
                self._node,
                self._settings,
                trader_id=self._trader_id,
                max_connection_attempts=SESSION_CONNECTION_ATTEMPTS,
            )

    def _phase_node_connect(self) -> None:
        """Start the node; wait for a post-condition of the whole startup."""
        with phase(self._log, "node:connect"):
            assert self._loop is not None and self._node is not None
            self._run_task = self._loop.create_task(self._node.run_async())
            wait = await_trader_started(
                self._node,
                self._run_task,
                self._deadline,
                self._connect_timeout,
                self._settings,
                self._log,
            )
            self._loop.run_until_complete(wait)
            self._monitor = ConnectionMonitor(session_id=str(self._session_id))

    async def _phase_gate_account(self) -> None:
        """Layer 2. **Emits no record of its own** — the module it calls logs
        both halves of ``gate:account`` itself (``live_account_gate.py:149``,
        ``:156``, ``:169``). Wrapping it would double every line.
        """
        decision = await self._account_verifier(self._node, self._settings, cli_flags=None)
        if not decision.permitted:
            # A returned refusal treated as a pass would start strategies on
            # an unverified account — same fail-closed reading as `live_check_driver`.
            raise GateRefusedError(decision.refusal or UNEXPLAINED_ACCOUNT_REFUSAL)

    def _phase_reconcile(self) -> None:
        """Epic 4 (FR35, AR25). A no-op placeholder — deliberately does
        nothing (``deferred-work.md:542-548``).
        """
        with phase(self._log, "reconcile"):
            pass

    def _phase_warmup(self) -> None:
        """Epic 4. A no-op placeholder; nothing warmed."""
        with phase(self._log, "warmup"):
            pass

    def _phase_subscribe(self) -> None:
        """Register the bar observer and start watching the bus for bars."""
        with phase(self._log, "subscribe"):
            assert self._node is not None
            bar_types = self._spec.subscription_bar_types
            observer = LiveBarObserver(build_bar_observer_config(self._settings, bar_types))
            self._node.trader.add_actor(observer)
            self._node.trader.start_actor(observer.id)
            self._steady_state = self._build_steady_state()
            self._node.trader.subscribe(BAR_TOPIC, self._steady_state.note_bar)
            report_instrument_shortfall(self._node, bar_types, self._log)

    def _phase_trading(self) -> None:
        """Materialise and start the strategies, then declare the trader started."""
        with phase(self._log, "trading"):
            assert self._node is not None
            # A session reclaimed during the earlier phases must never trade.
            if self._startup_heartbeat is not None and self._startup_heartbeat.reclaim is not None:
                raise self._startup_heartbeat.reclaim
            self._guard.expect(len(self._spec.strategies))
            started = [spec for spec in self._spec.strategies if self._start_strategy(spec)]
            if not started:
                names = ", ".join(s.strategy_id for s in self._spec.strategies)
                raise NoStrategyStartedError(
                    "No strategy in this session started, so it cannot trade. Every "
                    f"specification failed: {names}. See the `strategy.start_failed` records "
                    "for each one's error and traceback."
                )
            self._trader_started = True
            self._log.info(
                "session.started",
                trader_id=self._trader_id,
                strategies=[s.strategy_id for s in started],
                started_at=self._started_at.isoformat(),
            )

    def _start_strategy(self, strategy_spec) -> bool:
        """Materialise, guard, register and start one spec — contained (AC #5).

        The ``try`` is **per spec and inside** ``with phase(...)``, never around
        the phase: wrapping the phase would defeat AR39's *"a failure in any
        phase stops the sequence"* for genuine phase failures, which is a
        property the whole startup sequence rests on.

        ``guard.wrap`` comes before ``add_strategy``, always: ``register()``
        subscribes the bound ``handle_event`` during that call, and
        ``subscribe_bars`` binds ``handle_bar`` during ``on_start``. Wrapping
        here is before both; wrapping after would leave ``handle_event``
        unguarded forever (finding #5).

        Returns:
            ``True`` when the strategy is live. ``False`` when it was contained
            — the caller counts these, because a session where *none* returned
            ``True`` cannot trade and must not report itself started.
        """
        strategy = None
        try:
            strategy = materialise_strategy(strategy_spec)
            self._guard.wrap(strategy, spec_strategy_id=strategy_spec.strategy_id)
            assert self._node is not None
            self._node.trader.add_strategy(strategy)
            self._node.trader.start_strategy(strategy.id)
            return True
        except Exception as exc:  # noqa: BLE001 - AC #5: one bad spec is not the session
            self._guard.record_start_failure(spec_strategy_id=strategy_spec.strategy_id, exc=exc)
            self._fault_quietly(strategy, strategy_spec.strategy_id)
            return False

    def _fault_quietly(self, strategy: object | None, spec_strategy_id: str) -> None:
        """Isolate a strategy that failed to start. ``fault()``, not the others.

        ``degrade()`` is **illegal** from ``STARTING`` and is *silently
        swallowed* (``common/component.pyx:2130-2134``), so it would leave a
        strategy that looks contained and is not. ``stop()`` would run
        ``on_stop()``, which for ``sma_crossover`` still calls
        ``close_all_positions()`` — an exit the strategy never requested, over
        an unrelated startup bug (NFR14, AR43). Two verbs across the two paths
        rather than one, deliberately (*Judgment call #4*).

        Guarded because this runs on the containment path: a raise here would
        become the thing that stopped the loop it exists to keep going. Both
        states this path can arrive in are measured (review fix, 2026-08-23,
        against the installed 1.220.0): a spec that failed in ``on_start`` is
        left at **``STARTING``**, from which ``fault()`` *succeeds* and lands
        the strategy at ``FAULTED`` — isolated, exactly as intended; a spec
        that failed before registration is at ``PRE_INITIALIZED``, from which
        Nautilus swallows the illegal trigger itself, state unchanged, nothing
        raised.
        """
        if strategy is None:
            return
        try:
            strategy.fault()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - AR42
            self._log.error(
                GUARD_FAILED_EVENT,
                spec_strategy_id=spec_strategy_id,
                error_type=type(exc).__name__,
            )

    # ------------------------------------------------------------------
    # The rest of the session's life, and the pieces the phases lean on.
    # ------------------------------------------------------------------

    async def _serve(self) -> None:
        """Wait for the node to stop, or for the session to be taken away.

        Awaiting the run task is the *only* thing that should end a session:
        ``run_async()`` sits on an ``asyncio.gather`` over the engine queue
        tasks (``live/node.py:343-370``), so it returns when the node stops
        (Story 2.6's ``node.stop()``, called from the signal handler's
        callback) and raises if the node died. The heartbeat is waited on
        *alongside* it rather than fired and forgotten, because its one fatal
        outcome (this process no longer owns the session) has to reach the
        caller rather than sit unretrieved on a task nobody inspects until the
        ``finally``.
        """
        assert self._run_task is not None and self._steady_state is not None
        if self._startup_heartbeat is not None:
            self._startup_heartbeat.stop()
        self._heartbeat = asyncio.create_task(self._steady_state.run())
        done, _ = await asyncio.wait(
            {self._run_task, self._heartbeat}, return_when=asyncio.FIRST_COMPLETED
        )
        # Unordered `done`: retrieve the heartbeat first so a reclaim is
        # never shadowed by the run task's outcome.
        if self._heartbeat in done:
            self._heartbeat.result()
        for task in done:
            task.result()

    def _build_steady_state(self) -> SessionSteadyState:
        assert self._monitor is not None
        return SessionSteadyState(
            record=self._record,
            settings=self._settings,
            monitor=self._monitor,
            log=self._log,
            time_source=self._time_source,
            sleeper=self._sleeper,
            interval_seconds=self._heartbeat_interval_seconds,
            no_bars_after_seconds=self._no_bars_after_seconds,
            connection_reader=self._connection_reader,
            guard=self._guard,
        )

    def _stop_heartbeat(self, loop: asyncio.AbstractEventLoop) -> None:
        """Stop both heartbeat writers first (load-bearing ordering #2); policy
        in :func:`join_heartbeat`. Either writer may have observed the reclaim.
        """
        if self._startup_heartbeat is not None:
            self._startup_heartbeat.stop()
            if self._startup_heartbeat.reclaim is not None:
                self._ownership_lost = True
        if join_heartbeat(self._heartbeat, loop, self._log):
            self._ownership_lost = True
        if self._steady_state is not None:
            # Released without waiting, so a wedged write cannot hold the node
            # teardown behind it (decision D2).
            self._steady_state.release_executor()

    def _flush_contained_failures(self) -> None:
        """Persist any contained failure still queued, while the row is still ours.

        Closes the end-of-run window (review fix, 2026-08-23): the steady-state
        tick is the *routine* path to ``runtime_flags``, but a failure contained
        within the last interval before a stop — or queued by the all-failed
        start path, where ``run()`` raises before the tick loop ever starts —
        would otherwise never be written, and the ``-> stopped`` transition
        makes that permanent because the service refuses writes against a
        non-``running`` row by design. Runs in the ``finally``, **before**
        :meth:`_finish_record`, so the row still reads ``running``; after
        :meth:`_stop_heartbeat`, so the steady-state executor cannot race this
        write on the same row. The call is synchronous — the loop has already
        stopped, so there is nothing left to block.

        Guarded per failure, AR42: a DB hiccup here must not replace the run's
        primary outcome. A reclaim stops the flush entirely — the remaining
        facts belong in the successor's log, not its row.
        """
        if self._ownership_lost:
            return
        pending = self._guard.drain_pending()
        if not pending:
            return
        all_failed = bool(self._guard.all_failed)
        for failure in pending:
            try:
                self._record.record_strategy_failure(
                    strategy_id=failure.strategy_id,
                    spec_strategy_id=failure.spec_strategy_id,
                    error_type=failure.error_type,
                    handler=failure.handler,
                    at=failure.at,
                    detail=failure.detail,
                    all_failed=all_failed,
                )
            except SessionReclaimedError:
                self._ownership_lost = True
                return
            except Exception as exc:  # noqa: BLE001 - AR42: must not replace the outcome
                self._log.error(
                    GUARD_FAILED_EVENT,
                    spec_strategy_id=failure.spec_strategy_id,
                    error_type=type(exc).__name__,
                )

    def _finish_record(self) -> None:
        """Mark the session ``stopped`` after ``shutdown()``; policy in :func:`release_record`."""
        self._record_release_failed = release_record(
            self._record,
            self._log,
            trader_id=self._trader_id,
            ownership_lost=self._ownership_lost,
        )
