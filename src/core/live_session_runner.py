"""Run one paper-trading session in the foreground, in AR39's phase order.

Owns: ``LiveSessionRunner`` — the event loop a session lives on, the eight
ordered startup phases, the serve loop that is the rest of the session's life,
and the single teardown path every failure and every stop goes through (AR38).

Does not own: the phase *vocabulary* (``live_session_phases``), the node-facing
mechanics it sits on (``live_session_node``), the steady-state tick
(``live_session_steady_state``), node assembly (``live_node_builder``), the
static gate (``live_check``) or the account gate (``live_account_gate``) — each
of which logs its own half of the phase it owns — the database
(``src/services/session_record.py``, reached only through AR32's port), signals
and ``stop`` (Story 2.6), strategy-failure containment (Story 2.7),
``status``/``list`` (Story 2.8), reconciliation and warm-up (Epic 4, present
here as explicit no-op placeholders), or orders and trades (Epic 3).

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

**Two orderings are load-bearing and easy to undo by accident.**

1. *The connect deadline starts before the node factory call.* The build is
   where the adapter's own connect attempt happens, so a deadline started after
   it spends both budgets in series — an unreachable gateway took **115
   seconds** to say so when they were additive, measured live in Story 1.7.
2. *The ``finally`` cancels and awaits the heartbeat before ``shutdown``.*
   ``asyncio.to_thread`` resolves to the loop's default executor, which
   ``TradingNode.__init__`` replaces with the kernel's own pool
   (``kernel.py:268-270``) and ``dispose()`` then joins with ``wait=True``. See
   :meth:`LiveSessionRunner._stop_heartbeat`.

**What ``session_id`` does and does not reach.** Bound two ways — this
runner's logger and ``structlog.contextvars`` — so every structlog record,
including ``live_node_builder``'s and ``live_bar_observer``'s, carries it. It
does **not** reach Nautilus's own stdout: that logger is Rust-side; there the
correlation is the ``trader_id`` prefix on every ``TRADER_ID.COMPONENT_ID`` line.

Known, accepted limits, stated rather than implied: a clean phase log does
**not** mean reconciliation happened (``reconcile`` and ``warmup`` do nothing
at all in this epic); a heartbeat proves a process is writing, not that it is
trading; nothing withholds trading permission, because
``confirm_state_reestablished`` is deliberately never called until Epic 4 has a
real reconciliation to follow (*Judgment call #6*); and stopping does not yet
leave positions alone, because ``sma_crossover.on_stop()`` still calls
``close_all_positions()`` — Story 3.1's to remove.
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
    UNEXPLAINED_ACCOUNT_REFUSAL,
    await_trader_started,
    materialise_strategy,
    refusal_from_report,
    report_instrument_shortfall,
    validate_spec_is_materialisable,
)
from src.core.live_session_phases import PHASE_EVENT, phase
from src.core.live_session_record import SessionReclaimedError, SessionRecordPort
from src.core.live_session_steady_state import (
    DEFAULT_NO_BARS_AFTER_SECONDS,
    ConnectionReader,
    SessionSteadyState,
    StartupHeartbeat,
    join_heartbeat,
    release_record,
)
from src.core.live_trader_id import derive_trader_id
from src.models.session import DEFAULT_HEARTBEAT_INTERVAL_SECONDS, SessionSpec

#: How long the whole of `node:build` + `node:connect` may take. Strictly
#: greater than Nautilus's own three pre-`trader.start()` waits
#: (60 + 30 + 10), because `node:connect` now waits for a post-condition of all
#: three. **Deliberately not** the check's `DEFAULT_CONNECT_TIMEOUT_SECONDS`
#: (60.0): a check waits only for `check_connected()`, so a node that connects
#: in 40s and reconciles in 25s would be called unreachable on a perfectly
#: healthy gateway. That constant also cannot be imported — it lives in
#: `src/cli/commands/live.py`, which imports this module (circular) and imports
#: SQLAlchemy (fails this module's own purity guard).
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

ClientBuilder = Callable[..., None]


def _utc_now() -> datetime:
    """The repo's house clock idiom."""
    return datetime.now(timezone.utc)


class LiveSessionRunner:
    """Drive one session through AR39's startup sequence and then serve it.

    Args:
        settings: Loaded **IBKR** settings — the narrowest type that does the
            job, matching ``build_trading_node``. There is no ``settings.redis``
            here and no ``settings.ibkr``; the CLI narrows before constructing.
        session_id: The session's UUID business key. Used for the ``trader_id``
            derivation and the log binding only — never a database lookup,
            which is the port's business.
        spec: The frozen specification, read back through ``from_stored``.
        record: AR32's record port, already bound to this session and to the
            instant its ``-> running`` transition stamped.
        started_at: That same instant, for the log line only — the port holds
            the authoritative copy.
        cache: The Redis engine cache. ``None`` runs the session on an
            in-memory Nautilus cache, throwing away AR10's per-session
            namespace and FR19's "a restarted process rejoins its own state" —
            the CLI always passes one.
        logging: Nautilus's logging config; defaults to :data:`SESSION_LOGGING`.
        connect_timeout: The whole build-and-connect budget.
        heartbeat_interval_seconds: AR32's cadence.
        no_bars_after_seconds: The first-bar watchdog window.
        time_source: Aware ``datetime`` clock, injected so a test drives the
            stamped values exactly.
        sleeper: How the heartbeat waits one interval, injected so a test drives
            780 ticks without waiting 6.5 hours.
        node_factory, account_verifier, client_builder, connection_reader:
            The four broker-facing seams. They exist for the reason
            ``live_check_driver.py:124-125`` states: *"so every branch is
            reachable in tests without a broker (NFR32)."*
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

    @property
    def trader_started(self) -> bool:
        """Whether the ``trading`` phase completed, as the runner's own fact —
        never inferred from a ``ComponentState`` name: ``READY`` is reachable
        again after a reset (Epic 1 retro Action Item #5).
        """
        return self._trader_started

    def run(self) -> None:
        """Start the session and serve it until the node stops. Blocking.

        Raises:
            GateRefusedError: Either gate refused (exit 3).
            BrokerUnreachableError: The broker never reported connected, or the
                trader never started, inside the budget (exit 4).
            RedisUnreachableError: The engine cache is unusable (exit 1).
            SessionReclaimedError: Another process took the session mid-run.
            Exception: Whatever a phase raised, unchanged.
        """
        bind_contextvars(session_id=str(self._session_id))
        previous_loop = current_event_loop()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        # Review fix (2026-08-21): the phases outlast the 90s staleness
        # threshold — see `StartupHeartbeat`. Handed over in `_serve`.
        self._startup_heartbeat = StartupHeartbeat(
            record=self._record,
            log=self._log,
            time_source=self._time_source,
            interval_seconds=self._heartbeat_interval_seconds,
        )
        self._startup_heartbeat.start()
        try:
            self._phase_gate_static()
            self._phase_node_build()
            self._phase_node_connect()
            loop.run_until_complete(self._phase_gate_account())
            self._phase_reconcile()
            self._phase_warmup()
            self._phase_subscribe()
            self._phase_trading()
            loop.run_until_complete(self._serve())
        except SessionReclaimedError:
            # Recorded before the `finally` runs, because it is what decides
            # whether the teardown may touch the row at all. Re-raised
            # unchanged: a session taken away mid-run has not succeeded.
            self._ownership_lost = True
            raise
        finally:
            self._stop_heartbeat(loop)
            problems = shutdown(self._node, self._run_task, loop)
            if problems:
                self._log.warning("session.shutdown_problems", problems=problems)
            self._finish_record()
            restore_event_loop(previous_loop)
            unbind_contextvars("session_id")

    # The eight AR39 phases, in PHASE_SEQUENCE order. Separately named and
    # separately patchable is a CONTRACT, not a style choice: the landmine test
    # monkeypatches the later phases to prove no phase runs after a failure,
    # and Story 2.6 attaches its stop path to a single named teardown. Eight
    # inline `with phase(...)` blocks inside `run()` would satisfy every other
    # constraint here and leave that test unwritable.

    def _phase_gate_static(self) -> None:
        """Layer 1, plus the config-time checks that cost no socket.

        This phase's records come from **two** owners: ``preflight_gate``
        emits only the terminal one (verified — ``grep -n 'status="started"'
        src/core/*.py`` returns one hit, in ``live_account_gate``), so the
        ``started`` record is emitted here and the ``failed`` record only when
        the failure is *ours* rather than the gate's.

        ⚠️ ``preflight_gate`` **refuses by returning**, not by raising
        (``live_check.py:222`` is ``-> LiveCheckReport | None``). Skipping the
        conversion below would let the sequence walk on to ``node:build``,
        producing a log in which ``gate:static status=failed`` is followed by
        ``node:build status=started`` — exactly what AC #2 forbids.
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
        """
        with phase(self._log, "node:build"):
            self._deadline = time.monotonic() + self._connect_timeout
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
            self._client_builder(
                self._node,
                self._settings,
                trader_id=self._trader_id,
                max_connection_attempts=SESSION_CONNECTION_ATTEMPTS,
            )

    def _phase_node_connect(self) -> None:
        """Start the node and wait for a post-condition of the whole startup."""
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
            # The default implementation raises, but nothing in the
            # `-> GateDecision` type says it must, and a returned refusal
            # treated as a pass would start strategies on an unverified
            # account. Same fail-closed reading as `live_check_driver`.
            raise GateRefusedError(decision.refusal or UNEXPLAINED_ACCOUNT_REFUSAL)

    def _phase_reconcile(self) -> None:
        """Epic 4 (FR35, AR25). A no-op placeholder that logs and returns.

        Deliberately does nothing. ``deferred-work.md:542-548`` warns verbatim
        that a runner calling ``confirm_state_reestablished`` straight after
        observing a live socket *"would satisfy the type signature while
        defeating the design"*.
        """
        with phase(self._log, "reconcile"):
            pass

    def _phase_warmup(self) -> None:
        """Epic 4. A no-op placeholder; emits no ``warmup.completed`` (AR41's
        event for a real warm-up), because nothing warmed.
        """
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
            for strategy_spec in self._spec.strategies:
                strategy = materialise_strategy(strategy_spec)
                self._node.trader.add_strategy(strategy)
                self._node.trader.start_strategy(strategy.id)
            self._trader_started = True
            self._log.info(
                "session.started",
                trader_id=self._trader_id,
                strategies=[s.strategy_id for s in self._spec.strategies],
                started_at=self._started_at.isoformat(),
            )

    # ------------------------------------------------------------------
    # The rest of the session's life, and the pieces the phases lean on.
    # ------------------------------------------------------------------

    async def _serve(self) -> None:
        """Wait for the node to stop, or for the session to be taken away.

        Awaiting the run task is the *only* thing that should end a session:
        ``run_async()`` sits on an ``asyncio.gather`` over the engine queue
        tasks (``live/node.py:343-370``), so it returns when the node stops and
        raises if the node died. A ``while True: await asyncio.sleep(...)``
        loop would keep the process alive after a dead node — the failure
        ``live_check_driver._raise_if_node_died`` exists to prevent — and would
        give Story 2.6 nothing to stop. The heartbeat is waited on *alongside*
        it rather than fired and forgotten, because its one fatal outcome (this
        process no longer owns the session) has to reach the caller rather than
        sit unretrieved on a task nobody inspects until the ``finally``.
        """
        assert self._run_task is not None and self._steady_state is not None
        if self._startup_heartbeat is not None:
            self._startup_heartbeat.stop()
        self._heartbeat = asyncio.create_task(self._steady_state.run())
        done, _ = await asyncio.wait(
            {self._run_task, self._heartbeat}, return_when=asyncio.FIRST_COMPLETED
        )
        # `done` is unordered: retrieve the heartbeat first, so a reclaim is
        # never shadowed by the run task's outcome (review fix, 2026-08-21).
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
        )

    def _stop_heartbeat(self, loop: asyncio.AbstractEventLoop) -> None:
        """Stop both heartbeat writers, before anything else is torn down.

        See the module docstring's second load-bearing ordering; the policy
        lives in :func:`join_heartbeat`. Either writer may have observed the
        reclaim — fold that into ``_ownership_lost`` before
        ``_finish_record`` reads it.
        """
        if self._startup_heartbeat is not None:
            self._startup_heartbeat.stop()
            if self._startup_heartbeat.reclaim is not None:
                self._ownership_lost = True
        if join_heartbeat(self._heartbeat, loop, self._log):
            self._ownership_lost = True

    def _finish_record(self) -> None:
        """Mark the session ``stopped`` after ``shutdown()``; policy in :func:`release_record`."""
        release_record(
            self._record,
            self._log,
            trader_id=self._trader_id,
            ownership_lost=self._ownership_lost,
        )
