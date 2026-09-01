"""Component tests for ``LiveSessionRunner``'s startup sequence (Story 2.5).

The filename is the one ``architecture.md:549`` fixes for this suite.

Component tier: this module imports ``nautilus_trader.config`` and the runner,
and drives a ``TestLiveNode`` double. **Nothing here constructs a real
``TradingNode``** — ``NautilusKernel.__init__`` claims the C logging subsystem
and this tier runs ``-n auto``, unforked.

What is under test is AR39's *ordering*, and the ordering assertions are on the
**ordered list** of ``(phase, status)`` pairs. A test asserting "all eight
phases appear" passes against a runner that emits them in the wrong order, or
emits them all up front, which is precisely the defect AC #2 exists to prevent.

Two of the eight phases are logged by modules the runner does not own:
``gate:static``'s terminal record comes from ``live_check.preflight_gate`` and
**both** of ``gate:account``'s come from
``live_account_gate.verify_connected_account``. The ordering test therefore uses
the **real** account verifier against a registered fake gateway, so the record
list it asserts is the one an operator would actually see. Failure-injection
tests use stub verifiers, where the phase's own records are not the subject.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from nautilus_trader.adapters.interactive_brokers.factories import IB_CLIENTS
from nautilus_trader.common.component import is_logging_initialized
from nautilus_trader.config import LoggingConfig
from structlog.testing import capture_logs

from src.config import IBKRSettings
from src.core.live_account_gate import verify_connected_account
from src.core.live_cache import RedisUnreachableError
from src.core.live_check import BrokerUnreachableError
from src.core.live_gate import GateDecision, GateMode, GateRefusalReason, build_refusal
from src.core.live_node_builder import GateRefusedError
from src.core.live_session_node import (
    DEFAULT_SESSION_CONNECT_TIMEOUT_SECONDS,
    SESSION_CONNECTION_ATTEMPTS,
    SESSION_LOGGING,
)
from src.core.live_session_phases import PHASE_SEQUENCE
from src.core.live_session_runner import LiveSessionRunner
from src.models.session import SessionSpec, StrategySpec
from tests.component.doubles import TestIBAccountsClient, TestLiveNode

pytestmark = pytest.mark.component

SESSION_ID = UUID("33333333-3333-3333-3333-333333333333")
STARTED_AT = datetime(2026, 8, 19, 14, 30, 0, tzinfo=timezone.utc)
PAPER_ACCOUNT = "DU4076626"
AAPL_1MIN = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
AAPL_INSTRUMENT = "AAPL.NASDAQ"


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Machine-enforce the claim this file's tier placement rests on.

    Copied verbatim from ``tests/component/core/test_live_node_builder.py``.
    The assertion is on the *delta*, not the absolute state: under ``-n auto``
    this file shares a worker process with the rest of the component tier, and
    something else in that tier does initialise C logging. What this file must
    never do is *change* the state — which is exactly what would happen if a
    test here ever constructed a real ``TradingNode``.
    """
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — the runner's tests must never construct a "
        "real TradingNode. Move it to tests/integration/ and run it under --forked."
    )


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Keep the shell out of the fields the runner's collaborators read."""
    for name in (
        "IBKR_RATE_LIMIT",
        "IBKR_MARKET_DATA_TYPE",
        "IBKR_USE_RTH",
        "IB_MAX_CONNECTION_ATTEMPTS",
    ):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


def _settings(*, mode: str = "paper", port: int = 4002, account: str = PAPER_ACCOUNT):
    return IBKRSettings(
        _env_file=None,
        ibkr_host="127.0.0.1",
        ibkr_port=port,
        ibkr_client_id=1,
        ibkr_live_client_id=10,
        ibkr_trading_mode=mode,
        tws_account=account,
        ntrader_real_money_account="",
        ibkr_rate_limit=45,
        ibkr_market_data_lines=100,
    )


def _spec(*bar_types: str) -> SessionSpec:
    return SessionSpec(
        strategies=(
            StrategySpec(
                strategy_id="sma_crossover",
                parameters={},
                bar_types=bar_types or (AAPL_1MIN,),
            ),
        )
    )


class FakeClock:
    """The repo idiom — an injected aware-``datetime`` clock, no ``freezegun``."""

    def __init__(self, start: datetime = STARTED_AT) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class SpyRecord:
    """A ``SessionRecordPort`` that records *when* each call happened.

    One spy, not two mocks: AC #10's guarantee is an **order** —
    ``mark_stopped`` after ``shutdown`` — and two independent mocks cannot
    express an order between them.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.activity: list[tuple[datetime, datetime | None]] = []
        self.failures: list[str] = []
        self.raises: BaseException | None = None

    def record_activity(self, *, at: datetime, bar_seen_at: datetime | None = None) -> None:
        self.calls.append("record_activity")
        self.activity.append((at, bar_seen_at))
        if self.raises is not None:
            raise self.raises

    def mark_stopped(self) -> None:
        self.calls.append("mark_stopped")

    def record_strategy_failure(
        self,
        *,
        strategy_id: str,
        spec_strategy_id: str,
        error_type: str,
        handler: str,
        at: datetime,
        detail: str | None = None,
        all_failed: bool = False,
    ) -> None:
        """Story 2.7's third port method. Duck-typed here, but kept complete so
        the double stays an honest `SessionRecordPort` rather than one that
        happens to satisfy the calls this file makes today.
        """
        self.calls.append("record_strategy_failure")
        self.failures.append(spec_strategy_id)


def _permitting_verifier(mode: GateMode = GateMode.PAPER):
    async def _verify(node, settings, **kwargs) -> GateDecision:
        return GateDecision(permitted=True, mode=mode)

    return _verify


def _refusing_verifier(reason=GateRefusalReason.REPORTED_ACCOUNT_NOT_PAPER):
    async def _verify(node, settings, **kwargs) -> GateDecision:
        decision = build_refusal(reason, "reported account is not paper")
        assert decision.refusal is not None
        raise GateRefusedError(decision.refusal)

    return _verify


@pytest.fixture
def registered_accounts(monkeypatch):
    """Put an account-naming client in the adapter's process-global cache."""

    def _register(settings: IBKRSettings, accounts=(PAPER_ACCOUNT,)):
        key = (settings.ibkr_host, settings.ibkr_port, settings.ibkr_live_client_id)
        monkeypatch.setitem(IB_CLIENTS, key, TestIBAccountsClient(accounts))

    return _register


def _runner(node: TestLiveNode, *, settings=None, record=None, **overrides) -> LiveSessionRunner:
    """Build a runner whose node factory returns ``node`` and records its kwargs."""
    factory_calls: list[dict] = []

    def _factory(settings_arg, **kwargs):
        factory_calls.append({"settings": settings_arg, **kwargs})
        return node

    options = {
        "session_id": SESSION_ID,
        "spec": _spec(),
        "record": record if record is not None else SpyRecord(),
        "started_at": STARTED_AT,
        "connect_timeout": 2.0,
        "node_factory": _factory,
        "account_verifier": _permitting_verifier(),
        "client_builder": lambda *a, **k: None,
        "sleeper": _never_sleeps,
    }
    options.update(overrides)
    runner = LiveSessionRunner(settings if settings is not None else _settings(), **options)
    runner.factory_calls = factory_calls  # type: ignore[attr-defined]
    return runner


async def _never_sleeps(seconds: float) -> None:
    """A sleeper that never returns, so the heartbeat never ticks in these tests."""
    await asyncio.Event().wait()


def _pairs(logs) -> list[tuple[str, str]]:
    return [
        (entry["phase"], entry["status"])
        for entry in logs
        if entry.get("phase") and entry.get("status")
    ]


class TestTheOrderedPhaseSequence:
    """AC #2 — eight phases, this order, ``started`` then ``ok`` for each."""

    def test_a_clean_start_logs_all_sixteen_records_in_sequence_order(self, registered_accounts):
        """The real account verifier, so ``gate:account``'s own pair is real."""
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(instrument_ids=[AAPL_INSTRUMENT], run_seconds=0.01)
        runner = _runner(node, settings=settings, account_verifier=verify_connected_account)

        with capture_logs() as logs:
            runner.run()

        expected = [(name, status) for name in PHASE_SEQUENCE for status in ("started", "ok")]
        assert _pairs(logs) == expected

    def test_the_phases_are_separately_named_and_separately_patchable_methods(self):
        """A contract, not a style choice — the landmine test below needs it."""
        for name in (
            "_phase_gate_static",
            "_phase_node_build",
            "_phase_node_connect",
            "_phase_gate_account",
            "_phase_reconcile",
            "_phase_warmup",
            "_phase_subscribe",
            "_phase_trading",
        ):
            assert callable(getattr(LiveSessionRunner, name))


class TestAFailureStopsTheSequence:
    """AC #2's second half: no later phase runs after a failure.

    The landmine technique from ``tests/integration/core/test_epic1_ac_node.py``
    — monkeypatch the later phases with raisers, then prove the landmines are
    live by showing a clean run trips them.
    """

    LATER_PHASES = (
        "_phase_node_build",
        "_phase_node_connect",
        "_phase_gate_account",
        "_phase_reconcile",
        "_phase_warmup",
        "_phase_subscribe",
        "_phase_trading",
    )

    @staticmethod
    def _landmine(*args, **kwargs):
        raise AssertionError("a later phase ran after a failure")

    def test_a_gate_static_refusal_runs_no_later_phase(self, monkeypatch):
        for name in self.LATER_PHASES:
            monkeypatch.setattr(LiveSessionRunner, name, self._landmine)
        node = TestLiveNode()
        runner = _runner(node, settings=_settings(mode="live"))

        with capture_logs() as logs:
            with pytest.raises(GateRefusedError):
                runner.run()

        assert _pairs(logs) == [("gate:static", "started"), ("gate:static", "failed")]

    def test_the_landmines_are_live(self, monkeypatch, registered_accounts):
        """Without this, the test above would pass against a runner that never
        reached any phase at all.
        """
        settings = _settings()
        registered_accounts(settings)
        for name in self.LATER_PHASES:
            monkeypatch.setattr(LiveSessionRunner, name, self._landmine)
        runner = _runner(TestLiveNode(), settings=settings)

        with pytest.raises(AssertionError, match="a later phase ran"):
            runner.run()

    @pytest.mark.parametrize(
        "failing_phase",
        ["_phase_node_build", "_phase_node_connect", "_phase_subscribe", "_phase_trading"],
    )
    def test_a_failure_in_any_phase_leaves_every_later_phase_unrun(
        self, monkeypatch, registered_accounts, failing_phase
    ):
        settings = _settings()
        registered_accounts(settings)
        index = PHASE_SEQUENCE.index(
            {
                "_phase_node_build": "node:build",
                "_phase_node_connect": "node:connect",
                "_phase_subscribe": "subscribe",
                "_phase_trading": "trading",
            }[failing_phase]
        )
        for name in self.LATER_PHASES[self.LATER_PHASES.index(failing_phase) + 1 :]:
            monkeypatch.setattr(LiveSessionRunner, name, self._landmine)

        def _boom(self_, *args, **kwargs):
            raise RuntimeError("phase blew up")

        monkeypatch.setattr(LiveSessionRunner, failing_phase, _boom)
        runner = _runner(TestLiveNode(), settings=settings)

        with capture_logs() as logs:
            with pytest.raises(RuntimeError, match="phase blew up"):
                runner.run()

        emitted = {name for name, _ in _pairs(logs)}
        # The replaced method logs nothing at all — it *is* the phase — so what
        # this proves is the AC's own words: no later phase runs.
        for later in PHASE_SEQUENCE[index:]:
            assert later not in emitted, f"{later} ran after {PHASE_SEQUENCE[index]} failed"
        # And the phase immediately before it did complete, so the sequence
        # genuinely reached the failure rather than stopping earlier.
        assert (PHASE_SEQUENCE[index - 1], "ok") in _pairs(logs)


class TestTheEpicFourPlaceholders:
    """AC #3 — ``reconcile`` and ``warmup`` log their phase and do nothing else."""

    def test_they_log_started_then_ok(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node, settings=settings)

        with capture_logs() as logs:
            runner.run()

        pairs = _pairs(logs)
        assert ("reconcile", "started") in pairs and ("reconcile", "ok") in pairs
        assert ("warmup", "started") in pairs and ("warmup", "ok") in pairs

    def test_they_make_no_call_on_the_node(self, registered_accounts):
        """A placeholder that "helpfully" did something would defeat the point:
        ``deferred-work.md:542-548`` warns verbatim that a runner calling
        ``confirm_state_reestablished`` straight after observing a live socket
        *"would satisfy the type signature while defeating the design"*.

        Strengthened at the 2026-08-21 review: every attribute access on the
        node is recorded, not a sampled tuple of counters — a placeholder that
        touched *anything* on the node goes red here, not only the four
        surfaces the original tuple happened to watch.
        """
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        accesses: list[str] = []

        class _RecordingNode:
            def __getattr__(self, name):
                accesses.append(name)
                return getattr(node, name)

        runner = _runner(node, settings=settings)
        runner._node = _RecordingNode()  # the state `node:connect` left behind

        _ = runner._node.trader  # prove the spy is live before trusting it
        assert accesses == ["trader"]
        accesses.clear()

        runner._phase_reconcile()
        runner._phase_warmup()

        assert accesses == []

    def test_neither_emits_ar41s_warmup_completed(self, registered_accounts):
        """Nothing warmed, so claiming it did would be a false record."""
        settings = _settings()
        registered_accounts(settings)
        runner = _runner(TestLiveNode(run_seconds=0.01), settings=settings)

        with capture_logs() as logs:
            runner.run()

        assert not [entry for entry in logs if entry["event"] == "warmup.completed"]


class TestNothingIsRegisteredWhenTheAccountGateDecides:
    """AC #8 — the ordering is a property of the control flow, not of a poll."""

    def test_no_strategy_is_registered_when_the_account_gate_decides(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        observed: dict = {}

        async def _verify(node_arg, settings_arg, **kwargs):
            observed["strategies"] = list(node_arg.trader.strategies())
            observed["actors"] = list(node_arg.trader.actors())
            return GateDecision(permitted=True, mode=GateMode.PAPER)

        runner = _runner(node, settings=settings, account_verifier=_verify)

        runner.run()

        assert observed["strategies"] == []
        assert observed["actors"] == []

    def test_the_real_gate_would_refuse_a_strategy_started_before_it(self, registered_accounts):
        """The guard is non-vacuous for the first time: Epic 1 configured zero
        strategies permanently, so this branch was never reachable.
        """
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        node.trader.strategy_state_override = {"SMACrossover-000": "RUNNING"}
        runner = _runner(node, settings=settings, account_verifier=verify_connected_account)

        with pytest.raises(GateRefusedError) as refused:
            runner.run()

        assert (
            refused.value.refusal.reason is GateRefusalReason.STRATEGY_STARTED_BEFORE_ACCOUNT_GATE
        )

    def test_the_node_is_built_with_a_controller(self, registered_accounts):
        """Without one, ``add_strategy``/``add_actor`` silently return on a
        running trader and the session trades nothing for a whole day.
        """
        from src.core.live_session_controller import build_session_controller_config

        settings = _settings()
        registered_accounts(settings)
        runner = _runner(TestLiveNode(run_seconds=0.01), settings=settings)

        runner.run()

        assert runner.factory_calls[0]["controller"] == build_session_controller_config()

    def test_the_node_is_built_with_no_declarative_bar_observer(self, registered_accounts):
        """The observer is registered at ``subscribe``, after the gate — which
        is what makes "zero non-controller actors at the gate" true.
        """
        settings = _settings()
        registered_accounts(settings)
        runner = _runner(TestLiveNode(run_seconds=0.01), settings=settings)

        runner.run()

        assert runner.factory_calls[0]["bar_observer"] is None

    def test_the_runner_tracks_has_started_as_its_own_fact(self, registered_accounts):
        """Epic 1 retro Action Item #5: ``READY`` is reachable after a reset, so
        no ``ComponentState`` name can answer "has this trader started?".
        """
        settings = _settings()
        registered_accounts(settings)
        runner = _runner(TestLiveNode(run_seconds=0.01), settings=settings)

        assert runner.trader_started is False
        runner.run()
        assert runner.trader_started is True

    def test_no_component_state_name_appears_in_the_runners_source(self):
        import inspect

        from src.core import live_session_runner

        source = inspect.getsource(live_session_runner)
        for state in ("PRE_INITIALIZED", "STARTING", "RUNNING", "RESETTING", "DEGRADED"):
            assert f'"{state}"' not in source


class TestTheConnectWaitIsBounded:
    """AC #10 — a failure must land inside a bounded wall-clock budget.

    A test asserting only ``pytest.raises(BrokerUnreachableError)`` passes
    against an implementation that takes four minutes.
    """

    def test_a_node_that_never_connects_gives_up_inside_the_budget(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(connects_after=-1)
        runner = _runner(node, settings=settings, connect_timeout=0.2)

        started = time.monotonic()
        with pytest.raises(BrokerUnreachableError):
            runner.run()
        elapsed = time.monotonic() - started

        assert elapsed < 3.0, f"the connect wait took {elapsed:.1f}s against a 0.2s budget"

    def test_a_slow_build_is_spent_from_the_same_budget(self, registered_accounts):
        """The deadline is taken **before** ``build()``. An unreachable gateway
        took **115 seconds** to say so when the two budgets were additive —
        measured live in Story 1.7.
        """
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(connects_after=-1, build_delay_seconds=0.4)
        runner = _runner(node, settings=settings, connect_timeout=0.3)

        started = time.monotonic()
        with pytest.raises(BrokerUnreachableError):
            runner.run()
        elapsed = time.monotonic() - started

        assert elapsed < 2.0, f"build and connect budgets were additive ({elapsed:.1f}s)"

    def test_a_trader_that_never_starts_is_a_connect_failure(self, registered_accounts):
        """Reconciliation failure and portfolio-init timeout both end
        ``start_async`` early **without** completing the run task, so the
        deadline is the only signal — and ``check_connected()`` is already True.
        """
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(connects_after=0, trader_starts=False)
        runner = _runner(node, settings=settings, connect_timeout=0.2)

        with pytest.raises(BrokerUnreachableError) as unreachable:
            runner.run()

        message = str(unreachable.value)
        assert "reconcil" in message.lower()
        assert "portfolio" in message.lower()

    def test_the_deadline_message_does_not_blame_the_gateway_alone(self, registered_accounts):
        """``live_check_node.await_connected``'s *"is IB Gateway or TWS
        running?"* would send an operator to restart a healthy gateway for a
        reconciliation problem.
        """
        settings = _settings()
        registered_accounts(settings)
        runner = _runner(TestLiveNode(connects_after=-1), settings=settings, connect_timeout=0.2)

        with pytest.raises(BrokerUnreachableError) as unreachable:
            runner.run()

        assert "is IB Gateway or TWS running" not in str(unreachable.value)

    def test_a_node_that_dies_during_connect_is_surfaced(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(connects_after=-1, run_forever=False)
        runner = _runner(node, settings=settings, connect_timeout=5.0)

        started = time.monotonic()
        with pytest.raises(BrokerUnreachableError, match="stopped running"):
            runner.run()

        assert time.monotonic() - started < 2.0


class TestFailuresPropagateAndTheNodeGoesDownFirst:
    """AC #10 — and ``mark_stopped`` comes **after** teardown, never before."""

    @pytest.mark.parametrize(
        "exception",
        [
            RedisUnreachableError("redis is down"),
            BrokerUnreachableError("gateway is down"),
        ],
    )
    def test_a_node_factory_failure_propagates_unchanged(self, registered_accounts, exception):
        settings = _settings()
        registered_accounts(settings)

        def _factory(*args, **kwargs):
            raise exception

        runner = _runner(TestLiveNode(), settings=settings, node_factory=_factory)

        with pytest.raises(type(exception)):
            runner.run()

    def test_a_gate_account_refusal_propagates_and_the_node_is_torn_down(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode()
        runner = _runner(node, settings=settings, account_verifier=_refusing_verifier())

        with pytest.raises(GateRefusedError):
            runner.run()

        assert node.disposed is True

    @pytest.mark.parametrize(
        "exception",
        [
            RedisUnreachableError("redis is down"),
            BrokerUnreachableError("gateway is down"),
        ],
    )
    def test_mark_stopped_is_called_exactly_once_and_after_shutdown(
        self, registered_accounts, exception
    ):
        settings = _settings()
        registered_accounts(settings)
        record = SpyRecord()
        node = TestLiveNode()
        disposal_marker: list[str] = []
        original_dispose = node.dispose

        def _dispose():
            record.calls.append("dispose")
            disposal_marker.append("dispose")
            original_dispose()

        node.dispose = _dispose  # type: ignore[method-assign]

        def _verify_raises(*args, **kwargs):
            raise exception

        runner = _runner(node, settings=settings, record=record, account_verifier=_verify_raises)

        with pytest.raises(type(exception)):
            runner.run()

        assert record.calls.count("mark_stopped") == 1
        assert record.calls.index("dispose") < record.calls.index("mark_stopped")

    def test_a_gate_refusal_before_any_node_exists_still_marks_stopped(self):
        """AC #10: *"leaves the session in a state a later start can accept"* —
        including when the failure happens before a node was ever built.
        """
        record = SpyRecord()
        runner = _runner(TestLiveNode(), settings=_settings(mode="live"), record=record)

        with pytest.raises(GateRefusedError):
            runner.run()

        assert record.calls == ["mark_stopped"]


class TestNodeConfiguration:
    """AC #9 and the session's own budgets."""

    def test_the_runner_passes_its_own_logging_config_when_none_is_injected(
        self, registered_accounts
    ):
        settings = _settings()
        registered_accounts(settings)
        runner = _runner(TestLiveNode(run_seconds=0.01), settings=settings)

        runner.run()

        assert runner.factory_calls[0]["logging"] is SESSION_LOGGING

    def test_an_injected_logging_config_wins(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        injected = LoggingConfig(log_level="DEBUG")
        runner = _runner(TestLiveNode(run_seconds=0.01), settings=settings, logging=injected)

        runner.run()

        assert runner.factory_calls[0]["logging"] is injected

    @pytest.mark.parametrize(
        ("field", "expected"),
        [
            ("log_level", "INFO"),
            ("log_level_file", None),
            ("log_colors", True),
            ("use_pyo3", False),
        ],
    )
    def test_the_default_logging_config_is_explicit_field_by_field(self, field, expected):
        """Without this, AC #9's ``logging`` half has no values to assert and
        ships as the ``None`` it forbids. ``log_level_file=None`` means **no
        Nautilus file sink**: this repo's file logging is structlog's
        (``logs/ntrader.log``), and a second Rust-side writer would duplicate
        every line.
        """
        assert getattr(SESSION_LOGGING, field) == expected

    def test_bypass_logging_stays_false(self):
        """``kernel.py:253-257`` raises ``InvalidConfiguration`` for ``True`` in
        a LIVE environment — it is not a way to silence the node.
        """
        assert SESSION_LOGGING.bypass_logging is False

    def test_the_session_connect_budget_exceeds_nautilus_own_three_waits(self):
        """``node:connect`` now waits for a post-condition of all three."""
        from src.core import live_node_builder

        assert DEFAULT_SESSION_CONNECT_TIMEOUT_SECONDS > (
            live_node_builder.NODE_TIMEOUT_CONNECTION
            + live_node_builder.NODE_TIMEOUT_RECONCILIATION
            + live_node_builder.NODE_TIMEOUT_PORTFOLIO
        )

    def test_the_session_connect_budget_is_not_the_checks(self):
        """A node that connects in 40s and reconciles in 25s would be called
        unreachable on a healthy gateway if the two shared a number.
        """
        from src.cli.commands.live import DEFAULT_CONNECT_TIMEOUT_SECONDS

        assert DEFAULT_SESSION_CONNECT_TIMEOUT_SECONDS != DEFAULT_CONNECT_TIMEOUT_SECONDS

    def test_the_session_retry_budget_outlasts_a_gateway_restart(self, registered_accounts):
        """A *check* deliberately uses one attempt; a 6.5-hour session should
        not be defeated by a gateway that is mid-restart when it starts.
        """
        from src.core.live_check_node import BUILD_CONNECTION_ATTEMPTS

        assert int(SESSION_CONNECTION_ATTEMPTS) > int(BUILD_CONNECTION_ATTEMPTS)

    def test_the_retry_budget_reaches_build_clients(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        seen: list[dict] = []
        runner = _runner(
            TestLiveNode(run_seconds=0.01),
            settings=settings,
            client_builder=lambda node, s, **kwargs: seen.append(kwargs),
        )

        runner.run()

        assert seen[0]["max_connection_attempts"] == SESSION_CONNECTION_ATTEMPTS

    def test_the_trader_id_is_derived_from_the_session_id(self, registered_accounts):
        from src.core.live_trader_id import derive_trader_id

        settings = _settings()
        registered_accounts(settings)
        runner = _runner(TestLiveNode(run_seconds=0.01), settings=settings)

        runner.run()

        assert runner.factory_calls[0]["trader_id"] == derive_trader_id(SESSION_ID)

    def test_the_runner_never_declares_cli_flags(self, registered_accounts):
        """``GateFlags()`` is the gate's safe default; a session declares nothing."""
        settings = _settings()
        registered_accounts(settings)
        runner = _runner(TestLiveNode(run_seconds=0.01), settings=settings)

        runner.run()

        assert "cli_flags" not in runner.factory_calls[0]


class TestTheRunnerOwnsItsLoop:
    """*Judgment call #1*: not ``asyncio.run``. ``TradingNode.dispose()`` calls
    ``loop.stop()`` whenever it finds the loop running, which is fatal from a
    coroutine executing on that loop — Story 1.3 lost a live run to exactly that.
    """

    def test_run_is_synchronous(self):
        import inspect

        assert not inspect.iscoroutinefunction(LiveSessionRunner.run)

    def test_the_previous_event_loop_is_restored(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        previous = asyncio.new_event_loop()
        asyncio.set_event_loop(previous)
        try:
            _runner(TestLiveNode(run_seconds=0.01), settings=settings).run()
            assert asyncio.get_event_loop_policy().get_event_loop() is previous
        finally:
            asyncio.set_event_loop(None)
            previous.close()

    def test_the_runners_own_loop_is_closed_afterwards(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node, settings=settings)

        runner.run()

        assert runner.factory_calls[0]["loop"].is_closed()

    def test_the_module_never_calls_asyncio_run(self):
        import inspect

        from src.core import live_session_runner

        assert "asyncio.run(" not in inspect.getsource(live_session_runner)


class TestSubscribeAndTradingRegisterRealThings:
    """Task 5 — the two phases that put components on a running trader."""

    def test_subscribe_registers_a_bar_observer_built_from_the_specs_bar_types(
        self, registered_accounts
    ):
        from src.core.live_bar_observer import LiveBarObserver

        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        runner = _runner(node, settings=settings)

        runner.run()

        assert len(node.trader.added_actors) == 1
        observer = node.trader.added_actors[0]
        assert isinstance(observer, LiveBarObserver)
        assert node.trader.started_actors == [observer.id]

    def test_the_observer_config_comes_from_build_bar_observer_config(self, registered_accounts):
        """So the 45 req/s pacing discipline is sourced once (NFR15)."""
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)

        _runner(node, settings=settings).run()

        observer = node.trader.added_actors[0]
        assert observer.config.requests_per_second == settings.ibkr_rate_limit
        assert observer.config.bar_types == (AAPL_1MIN,)

    def test_subscribe_watches_the_bar_topic_on_the_message_bus(self, registered_accounts):
        """Extended by Story 3.2, Task 5.1: the order path adds its own bar
        subscription (latency anchoring) and an order-events subscription,
        both alongside — never instead of — the steady state's original one.
        """
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)

        runner = _runner(node, settings=settings)
        runner.run()

        topics = [topic for topic, _ in node.trader.subscriptions]
        assert topics == ["data.bars.*", "data.bars.*", "events.order*"]
        assert runner._order_observer is not None
        handlers = [handler for _, handler in node.trader.subscriptions]
        assert handlers[0] == runner._steady_state.note_bar
        assert handlers[1] == runner._order_observer.note_bar
        assert handlers[2] == runner._order_observer.handle_order_event

    def test_note_bar_is_subscribed_before_any_strategy_is_added(self, registered_accounts):
        """Pre-verified finding #12's ordering, pinned (review fix, 2026-08-23).

        ``last_bar_at`` keeps advancing even when every strategy is dead
        *because* the runner's ``note_bar`` subscribes at the ``subscribe``
        phase — before any strategy subscribes at ``trading``, and the bus
        dispatches equal-priority subscribers in subscription order. The named
        mutation is moving the ``subscribe`` call into ``_phase_trading`` after
        ``add_strategy``; this interleaved timeline goes red on exactly that.
        The bus-side half (dispatch order == subscription order) is pinned
        against a real ``MessageBus`` in the test below.
        """
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        timeline: list[str] = []
        trader = node.trader
        original_subscribe, original_add = trader.subscribe, trader.add_strategy
        trader.subscribe = lambda topic, handler: (
            timeline.append("subscribe"),
            original_subscribe(topic, handler),
        )[-1]
        trader.add_strategy = lambda strategy: (
            timeline.append("add_strategy"),
            original_add(strategy),
        )[-1]

        _runner(node, settings=settings).run()

        assert "subscribe" in timeline and "add_strategy" in timeline
        assert timeline.index("subscribe") < timeline.index("add_strategy")
        # Story 3.2: not just the FIRST subscribe — every subscribe call the
        # runner makes (steady state's bar topic, the order path's bar topic,
        # the order path's events.order* topic) must land before the first
        # `add_strategy`. A weaker check would miss a bug that put the new
        # order-path subscriptions AFTER trading started.
        first_add_strategy = timeline.index("add_strategy")
        assert timeline[:first_add_strategy].count("subscribe") == 3, timeline

    def test_a_real_bus_dispatches_equal_priority_subscribers_in_subscription_order(self):
        """The bus-side half of finding #12, against a real ``MessageBus``:
        the measured dispatch order is ``['observer', 'note_bar',
        <strategies in registration order>]`` because ``publish_c`` walks
        equal-priority subscribers in the order they subscribed. A Nautilus
        upgrade that changes this silently breaks AC #4's rationale — this is
        where the repo finds out. (Constructing a ``MessageBus`` does not
        initialise C logging — the same measured fact
        ``test_session_runner_strategy_failure.py`` rests on.)
        """
        from nautilus_trader.common.component import MessageBus, TestClock
        from nautilus_trader.model.identifiers import TraderId

        msgbus = MessageBus(trader_id=TraderId("TESTER-000"), clock=TestClock())
        order: list[str] = []
        for name in ("observer", "note_bar", "sma_crossover", "momentum"):
            msgbus.subscribe("data.bars.*", lambda _msg, name=name: order.append(name))

        msgbus.publish("data.bars.AAPL", object())

        assert order == ["observer", "note_bar", "sma_crossover", "momentum"]

    def test_an_unqualified_contract_is_reported_at_warning(self, registered_accounts):
        """IBKR *skips* a contract it will not qualify; nothing raises."""
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(instrument_ids=[], run_seconds=0.01)

        with capture_logs() as logs:
            _runner(node, settings=settings).run()

        shortfall = [entry for entry in logs if entry["event"] == "session.instruments"]
        assert len(shortfall) == 1
        assert shortfall[0]["log_level"] == "warning"
        assert shortfall[0]["missing"] == [AAPL_INSTRUMENT]

    def test_a_qualified_contract_produces_no_shortfall_warning(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(instrument_ids=[AAPL_INSTRUMENT], run_seconds=0.01)

        with capture_logs() as logs:
            _runner(node, settings=settings).run()

        assert not [entry for entry in logs if entry["event"] == "session.instruments"]

    def test_trading_registers_and_starts_every_strategy_in_spec_order(self, registered_accounts):
        """Two strategies, deliberately (2026-08-21 review): with one entry the
        ordering half of this assertion is vacuous — a runner that reversed a
        multi-strategy list would have passed.
        """
        msft_1min = "MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL"
        spec = SessionSpec(
            strategies=(
                StrategySpec(strategy_id="sma_crossover", parameters={}, bar_types=(AAPL_1MIN,)),
                StrategySpec(strategy_id="momentum", parameters={}, bar_types=(msft_1min,)),
            )
        )
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)

        _runner(node, settings=settings, spec=spec).run()

        assert [str(s.config.bar_type) for s in node.trader.added_strategies] == [
            AAPL_1MIN,
            msft_1min,
        ]
        assert node.trader.started_strategies == [s.id for s in node.trader.added_strategies]

    def test_session_started_is_emitted_at_the_trading_phase(self, registered_accounts):
        """AR41 enumerates ``session.started`` but nothing owned it until now."""
        settings = _settings()
        registered_accounts(settings)

        with capture_logs() as logs:
            _runner(TestLiveNode(run_seconds=0.01), settings=settings).run()

        started = [entry for entry in logs if entry["event"] == "session.started"]
        assert len(started) == 1
        assert started[0]["strategies"] == ["sma_crossover"]


class TestStrategyMaterialisation:
    """Task 5's one genuinely unknown shape: ``SMAParameters`` carries neither
    ``instrument_id`` nor ``bar_type`` while ``SMAConfig`` requires both.
    """

    def test_a_stored_spec_round_trips_into_a_configured_sma_crossover(self):
        from nautilus_trader.model.data import BarType

        from src.core.live_session_node import materialise_strategy
        from src.core.strategies.sma_crossover import SMACrossover

        spec = _spec()
        stored = spec.to_stored()
        restored = SessionSpec.from_stored(stored)

        strategy = materialise_strategy(restored.strategies[0])

        assert isinstance(strategy, SMACrossover)
        assert strategy.config.bar_type == BarType.from_str(AAPL_1MIN)
        assert str(strategy.config.instrument_id) == AAPL_INSTRUMENT

    def test_the_frozen_parameters_are_used_verbatim_not_rebuilt(self):
        """FR14/FR53: re-running ``build_strategy_params`` would let today's
        settings silently change a multi-week forward test.
        """
        from src.core.live_session_node import materialise_strategy

        spec = SessionSpec(
            strategies=(
                StrategySpec(
                    strategy_id="sma_crossover",
                    parameters={"fast_period": 3, "slow_period": 7},
                    bar_types=(AAPL_1MIN,),
                ),
            )
        )

        strategy = materialise_strategy(spec.strategies[0])

        assert strategy.config.fast_period == 3
        assert strategy.config.slow_period == 7

    def test_the_runner_never_calls_build_strategy_params(self):
        """Structural, not a grep. Both modules *explain at length* why they do
        not call it, so a source-text check would trip on its own prose — the
        trap Stories 2.1 and 2.3 each recorded hitting.
        """
        import ast
        from pathlib import Path

        from src.core import live_session_node, live_session_runner

        for module in (live_session_runner, live_session_node):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            called = {
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            }
            assert "build_strategy_params" not in called, module.__name__
            # Non-vacuity: the scan really does see the calls in this module.
            assert called, f"the AST scan found no attribute calls in {module.__name__}"

    def test_the_materialisation_guard_can_fail(self):
        """Proof the AST scan above is not vacuous, on a planted probe."""
        import ast

        probe = ast.parse("StrategyLoader.build_strategy_params(a, b, c)")
        called = {
            node.func.attr
            for node in ast.walk(probe)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }

        assert "build_strategy_params" in called

    def test_a_two_bar_type_strategy_is_refused_at_gate_static(self, registered_accounts):
        """*Judgment call #4* — before any socket opens, and naming the strategy."""
        from src.core.live_market_data import LiveMarketDataError

        settings = _settings()
        registered_accounts(settings)
        spec = SessionSpec(
            strategies=(
                StrategySpec(
                    strategy_id="sma_crossover",
                    parameters={},
                    bar_types=(AAPL_1MIN, "MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL"),
                ),
            )
        )
        node = TestLiveNode()
        runner = _runner(node, settings=settings, spec=spec)

        with capture_logs() as logs:
            with pytest.raises(LiveMarketDataError, match="sma_crossover"):
                runner.run()

        assert _pairs(logs) == [("gate:static", "started"), ("gate:static", "failed")]
        assert node.built is False

    def test_the_naive_flatten_is_never_written(self):
        """``spec.subscription_bar_types`` exists so this story would not write
        the cross-strategy flatten the node builder rejects.
        """
        import inspect

        from src.core import live_session_runner

        source = inspect.getsource(live_session_runner)
        assert "subscription_bar_types" in source
        assert "for bt in" not in source


class TestSessionIdReachesEveryRecord:
    """AC #4 — and specifically the ``contextvars`` half, which needs a test
    that can actually fail.

    ⚠️ ``structlog.testing.capture_logs`` **cannot** be used here. It does
    ``processors.clear(); processors.append(cap)``, which removes
    ``merge_contextvars`` for the duration — so a test written with it fails
    even against a perfectly working binding, and the natural "fix" is to
    delete the binding. ``src/utils/logging.py:50`` installs
    ``merge_contextvars`` as the first shared processor, so this test installs
    the same first processor and captures after it.
    """

    @pytest.fixture
    def rendered(self, monkeypatch):
        """Capture through a chain whose first processor is ``merge_contextvars``.

        ``live_check``'s module-level logger is replaced with a **fresh,
        unbound** one built after the reconfigure. That is not a shortcut past
        the thing under test — the replacement carries no ``session_id`` of its
        own, so contextvars is still the only route by which one can appear.
        It is needed because this repo configures structlog with
        ``cache_logger_on_first_use=True``: under ``-n auto`` some earlier test
        in this worker has already bound that proxy against the old processor
        chain, and reconfiguring afterwards cannot reach it. Standalone the
        test passed without this; in a full run it silently observed nothing,
        which is the "test that cannot fail" shape review catches every time.
        """
        import structlog

        from src.core import live_check

        entries: list[dict] = []

        def _capture(logger, name, event_dict):
            entries.append(dict(event_dict))
            raise structlog.DropEvent

        original = structlog.get_config()
        structlog.configure(
            processors=[structlog.contextvars.merge_contextvars, _capture],
            logger_factory=structlog.ReturnLoggerFactory(),
            wrapper_class=structlog.make_filtering_bound_logger(0),
            cache_logger_on_first_use=False,
        )
        monkeypatch.setattr(live_check, "logger", structlog.get_logger("src.core.live_check"))
        try:
            yield entries
        finally:
            structlog.configure(**original)

    def test_a_record_from_a_module_the_runner_never_bound_carries_session_id(
        self, rendered, registered_accounts
    ):
        settings = _settings()
        registered_accounts(settings)

        _runner(TestLiveNode(run_seconds=0.01), settings=settings).run()

        # `live_check.preflight_gate` emits this; the runner hands that module
        # no logger of any kind, so only the contextvars binding can put the
        # session id on it.
        foreign = [entry for entry in rendered if entry["event"] == "gate.static"]
        assert foreign, "the static gate emitted nothing to assert against"
        assert all(entry.get("session_id") == str(SESSION_ID) for entry in foreign)

    def test_the_capture_sees_nothing_without_the_binding(self, rendered, registered_accounts):
        """Mutation proof, permanent rather than manual: with the binding
        removed, the very same capture yields a record **without** a
        ``session_id`` — so the assertion above is not passing on something
        else's contextvars.
        """
        import structlog

        from src.core.live_check import preflight_gate

        settings = _settings()
        registered_accounts(settings)
        structlog.contextvars.clear_contextvars()

        preflight_gate(settings, None)

        foreign = [entry for entry in rendered if entry["event"] == "gate.static"]
        assert foreign
        assert all("session_id" not in entry for entry in foreign)

    def test_the_binding_is_removed_when_the_runner_returns(self, rendered, registered_accounts):
        """A leaked binding would stamp every later command with a dead id."""
        import structlog

        settings = _settings()
        registered_accounts(settings)

        _runner(TestLiveNode(run_seconds=0.01), settings=settings).run()

        assert "session_id" not in structlog.contextvars.get_contextvars()

    def test_the_binding_is_removed_even_when_the_run_fails(self, rendered):
        import structlog

        runner = _runner(TestLiveNode(), settings=_settings(mode="live"))

        with pytest.raises(GateRefusedError):
            runner.run()

        assert "session_id" not in structlog.contextvars.get_contextvars()


class TestImportPurity:
    """AC #6 — ``LiveSessionRunner`` imports no SQLAlchemy, in both forms.

    ⚠️ The polarity is **inverted** from
    ``test_session_service.py::TestImportPurity``: ``nautilus_trader`` is
    permitted here and the database is not. And do not copy
    ``test_live_connection_monitor.py``'s forbidden tuple — it also forbids
    ``src.services``, which this module could not satisfy… and in fact must not
    need. The runner takes the *port*, not the adapter; if it ever imports
    ``src.services.*``, the design has drifted.
    """

    FORBIDDEN = ("sqlalchemy", "psycopg2", "asyncpg", "pg8000", "src.db", "src.services")

    MODULES = (
        "src.core.live_session_runner",
        "src.core.live_session_node",
        "src.core.live_session_steady_state",
        # Story 2.7. The guard is `src/core`, is reached from the runner, and
        # builds the record that crosses into `src/services` — so it is exactly
        # the shape AR38 governs. Added here in the same commit that created it,
        # because this list is hand-maintained: Story 2.6's file-size split moved
        # `live_start.py` out of `live.py` and it silently escaped two guards.
        "src.core.live_strategy_guard",
        # Story 2.8. Not reached from the runner at all — the opposite
        # direction, the CLI's read side — but it lives in the `live_*` family
        # AR38's discipline covers, and staying provably free of `src.db`/
        # `src.services` is exactly what lets it be unit-tested with no
        # database (AC #9).
        "src.core.live_session_health",
        # Story 3.2. Reached from the runner (installed on every started
        # strategy, subscribed in `_phase_subscribe`) and, like the guard,
        # framework-free by design — no `src.db`/`src.services` to stay clear
        # of in the first place, but the same hand-maintained-list discipline
        # applies: add on creation, not on next discovery.
        "src.core.live_order_path",
        # 2026-09-01. Imported by `live_node_builder` and executed during
        # `node.build()`. Same discipline: added on creation, not on next
        # discovery.
        "src.core.live_exec_avg_px",
    )

    @pytest.mark.parametrize("module_name", MODULES)
    def test_top_level_imports_contain_no_database_library(self, module_name):
        import ast
        import importlib
        from pathlib import Path

        module = importlib.import_module(module_name)
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        offenders = [name for name in imported if name.startswith(self.FORBIDDEN)]
        assert offenders == [], f"{module_name} must stay database-free, found: {offenders}"

    def test_importing_the_runner_loads_no_database_library(self):
        """The AST form alone is blind to *transitive* loading — a ``src.*``
        import that itself drags SQLAlchemy in passes it and fails the real
        requirement. Story 2.1's review proved that the hard way.
        """
        import subprocess
        import sys
        from pathlib import Path

        from src.core import live_session_runner

        code = (
            "import sys, src.core.live_session_runner;"
            "print(','.join(sorted(m for m in "
            "('sqlalchemy', 'psycopg2', 'asyncpg', 'pg8000') if m in sys.modules)))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(live_session_runner.__file__).parents[2],
            capture_output=True,
            text=True,
            check=True,
        )

        assert result.stdout.strip() == ""

    def test_the_runner_never_names_a_repository_or_a_session_factory(self):
        """A structural backstop: the port takes no ``session_id`` for database
        purposes, so these names have no business appearing at all.
        """
        import inspect

        from src.core import live_session_runner

        source = inspect.getsource(live_session_runner)
        for forbidden in ("get_sync_session", "SyncTradingSessionRepository", "SessionService"):
            assert forbidden not in source


class TestBeingReclaimedOutFromUnderYourself:
    """Task 6's one exception to AR42 — and its effect on the teardown."""

    def _reclaiming_record(self):
        from src.core.live_session_record import SessionReclaimedError

        class _Record(SpyRecord):
            def record_activity(self, *, at, bar_seen_at=None):
                self.calls.append("record_activity")
                raise SessionReclaimedError("session 'alpha' was reclaimed by another process")

        return _Record()

    async def _immediate(self, seconds: float) -> None:
        """A sleeper that returns at once, so the first tick lands immediately."""

    def test_a_reclaim_propagates_out_of_run(self, registered_accounts):
        from src.core.live_session_record import SessionReclaimedError

        settings = _settings()
        registered_accounts(settings)
        runner = _runner(
            TestLiveNode(),
            settings=settings,
            record=self._reclaiming_record(),
            sleeper=self._immediate,
        )

        with pytest.raises(SessionReclaimedError):
            runner.run()

    def test_a_reclaim_leaves_the_successors_row_alone(self, registered_accounts):
        """``mark_stopped()`` is skipped entirely: the row belongs to the other
        process now, and moving it to ``stopped`` would kill a live session.
        """
        from src.core.live_session_record import SessionReclaimedError

        settings = _settings()
        registered_accounts(settings)
        record = self._reclaiming_record()
        runner = _runner(TestLiveNode(), settings=settings, record=record, sleeper=self._immediate)

        with pytest.raises(SessionReclaimedError):
            runner.run()

        assert "mark_stopped" not in record.calls

    def test_a_reclaim_still_tears_the_node_down(self, registered_accounts):
        """Skipping the *record* write must not skip the *node* teardown."""
        from src.core.live_session_record import SessionReclaimedError

        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode()
        runner = _runner(
            node,
            settings=settings,
            record=self._reclaiming_record(),
            sleeper=self._immediate,
        )

        with pytest.raises(SessionReclaimedError):
            runner.run()

        assert node.disposed is True

    def test_a_reclaim_is_logged_at_error_by_name(self, registered_accounts):
        from src.core.live_session_record import SessionReclaimedError

        settings = _settings()
        registered_accounts(settings)
        runner = _runner(
            TestLiveNode(),
            settings=settings,
            record=self._reclaiming_record(),
            sleeper=self._immediate,
        )

        with capture_logs() as logs:
            with pytest.raises(SessionReclaimedError):
                runner.run()

        reclaimed = [
            entry for entry in logs if entry["event"] == "session.reclaimed_by_another_process"
        ]
        assert len(reclaimed) == 1
        assert reclaimed[0]["log_level"] == "error"

    def test_an_ordinary_write_failure_does_not_stop_the_session(self, registered_accounts):
        """AR42: a database hiccup must never kill a trading session."""
        settings = _settings()
        registered_accounts(settings)
        record = SpyRecord()
        record.raises = RuntimeError("connection pool exhausted")
        node = TestLiveNode(run_seconds=0.05)
        runner = _runner(node, settings=settings, record=record, sleeper=self._immediate)

        runner.run()

        assert record.calls.count("record_activity") > 1
        assert record.calls[-1] == "mark_stopped"


class TestStoryTwoFiveExceptionNameCoupling:
    """``live_check`` keys its exit-code map on class **names**; pin the four
    Story 2.5 added, the way ``test_live_check_driver.py`` pins Story 1.7's.

    Without this a rename silently reclassifies a session-state conflict as a
    generic error and drops its message — and the message is the whole point of
    having added the name.
    """

    @pytest.mark.parametrize(
        ("klass", "expected_name"),
        [
            (RedisUnreachableError, "RedisUnreachableError"),
            (
                pytest.importorskip("src.db.exceptions").InvalidSessionTransition,
                "InvalidSessionTransition",
            ),
            (
                pytest.importorskip("src.db.exceptions").RecordNotFoundError,
                "RecordNotFoundError",
            ),
            (
                pytest.importorskip("src.core.live_session_record").SessionReclaimedError,
                "SessionReclaimedError",
            ),
        ],
    )
    def test_the_real_class_still_carries_the_name_the_map_uses(self, klass, expected_name):
        assert klass.__name__ == expected_name

    @pytest.mark.parametrize(
        ("klass", "expected_code"),
        [
            (RedisUnreachableError, 1),
            (pytest.importorskip("src.db.exceptions").InvalidSessionTransition, 1),
            (pytest.importorskip("src.db.exceptions").RecordNotFoundError, 1),
            (GateRefusedError, 3),
            (BrokerUnreachableError, 4),
        ],
    )
    def test_the_real_class_maps_to_the_documented_exit_code(self, klass, expected_code):
        from src.core.live_check import EXIT_CODES, classify_failure

        if klass is GateRefusedError:
            instance = klass(build_refusal(GateRefusalReason.NON_PAPER_PORT, "no").refusal)
        else:
            instance = klass("boom")

        assert EXIT_CODES[classify_failure(instance)] == expected_code

    def test_the_messages_of_the_four_reach_the_operator(self):
        from src.core.live_check import failure_message
        from src.core.live_session_record import SessionReclaimedError
        from src.db.exceptions import InvalidSessionTransition, RecordNotFoundError

        for klass in (
            RedisUnreachableError,
            InvalidSessionTransition,
            RecordNotFoundError,
            SessionReclaimedError,
        ):
            assert failure_message(klass("the actionable detail")) == "the actionable detail"


class TestTeardownOrderAtEachFailuresOwnPhase:
    """Review strengthening (2026-08-21): Task 4 requires each typed failure
    injected **at its own phase**, with dispose-before-``mark_stopped``
    asserted on a single spy. The account-verifier seam alone cannot prove the
    earlier phases' teardown ordering, and ``GateRefusedError`` previously had
    no order assertion at all.
    """

    def _spying_node(self, record: SpyRecord, **kwargs) -> TestLiveNode:
        node = TestLiveNode(**kwargs)
        original_dispose = node.dispose

        def _dispose():
            record.calls.append("dispose")
            original_dispose()

        node.dispose = _dispose  # type: ignore[method-assign]
        return node

    def test_a_redis_refusal_at_node_build_marks_stopped_exactly_once(self, registered_accounts):
        """No node exists to dispose when the factory itself refuses — the row
        release is the whole of the teardown, and it must still happen.
        """
        settings = _settings()
        registered_accounts(settings)
        record = SpyRecord()

        def _factory(*args, **kwargs):
            raise RedisUnreachableError("redis is down at 127.0.0.1:6399")

        runner = _runner(TestLiveNode(), settings=settings, record=record, node_factory=_factory)

        with pytest.raises(RedisUnreachableError):
            runner.run()

        assert record.calls.count("mark_stopped") == 1
        assert record.calls[-1] == "mark_stopped"

    def test_a_broker_timeout_at_node_connect_tears_down_then_marks_stopped(
        self, registered_accounts
    ):
        """``trader_starts=False`` is the "connected, but the trader never
        started" shape — the failure genuinely raised at ``node:connect``.
        """
        settings = _settings()
        registered_accounts(settings)
        record = SpyRecord()
        node = self._spying_node(record, trader_starts=False)
        runner = _runner(node, settings=settings, record=record, connect_timeout=0.2)

        with pytest.raises(BrokerUnreachableError):
            runner.run()

        assert record.calls.count("mark_stopped") == 1
        assert record.calls.index("dispose") < record.calls.index("mark_stopped")

    def test_a_gate_account_refusal_tears_down_then_marks_stopped(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        record = SpyRecord()
        node = self._spying_node(record)
        runner = _runner(
            node, settings=settings, record=record, account_verifier=_refusing_verifier()
        )

        with pytest.raises(GateRefusedError):
            runner.run()

        assert record.calls.count("mark_stopped") == 1
        assert record.calls.index("dispose") < record.calls.index("mark_stopped")


class TestTheStartupHeartbeatCoversThePhases:
    """Review fix (2026-08-21): the phases outlast the 90s staleness threshold,
    so a writer keeps the row fresh from before the first phase until the
    steady loop takes over — see ``StartupHeartbeat`` for the full argument.
    """

    def test_the_writer_is_running_before_the_node_is_built(self, registered_accounts):
        """The build is the longest silent window (its synchronous connect
        attempt alone can spend the whole budget) — the writer must already be
        alive when it starts.
        """
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(run_seconds=0.01)
        alive_at_build: list[bool] = []

        def _factory(settings_arg, **kwargs):
            writer = runner._startup_heartbeat
            alive_at_build.append(writer is not None and writer.running)
            return node

        runner = _runner(node, settings=settings, node_factory=_factory)
        runner.run()

        assert alive_at_build == [True]

    def test_the_writer_is_stopped_by_the_time_run_returns(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        runner = _runner(TestLiveNode(run_seconds=0.01), settings=settings)

        runner.run()

        assert runner._startup_heartbeat is not None
        assert not runner._startup_heartbeat.running

    def test_a_reclaim_observed_during_startup_stops_the_start_before_any_strategy(
        self, registered_accounts, monkeypatch
    ):
        """A session taken during the phases must never trade — and the row
        belongs to the successor, so ``mark_stopped`` must not run either.
        Deterministic: the writer is stubbed with the state a real one leaves
        behind after observing the reclaim; ``TestStartupHeartbeat`` (in
        ``test_session_steady_state.py``) proves the real writer produces it.
        """
        from src.core import live_session_runner as runner_module
        from src.core.live_session_record import SessionReclaimedError

        class _DispossessedWriter:
            def __init__(self, **kwargs) -> None:
                self.reclaim = SessionReclaimedError("session 'alpha' was reclaimed")
                self.running = False

            def start(self) -> None:
                return None

            def stop(self, **kwargs) -> None:
                return None

        monkeypatch.setattr(runner_module, "StartupHeartbeat", _DispossessedWriter)
        settings = _settings()
        registered_accounts(settings)
        record = SpyRecord()
        node = TestLiveNode()
        runner = _runner(node, settings=settings, record=record)

        with capture_logs() as logs, pytest.raises(SessionReclaimedError):
            runner.run()

        assert node.trader.added_strategies == []
        assert node.trader.started_strategies == []
        assert "mark_stopped" not in record.calls
        assert node.disposed is True
        assert ("trading", "failed") in _pairs(logs)


class TestASwallowedReclaimStillSkipsTheRelease:
    """Review fix (2026-08-21): an in-flight write can surface its reclaim only
    at cancellation time, where ``gather(return_exceptions=True)`` would
    silently discard it — the join must fold it into the ownership flag before
    ``_finish_record`` reads it.
    """

    def test_a_reclaim_completed_but_unretrieved_marks_ownership_lost(self):
        from src.core.live_session_record import SessionReclaimedError

        record = SpyRecord()
        runner = _runner(TestLiveNode(), record=record)
        loop = asyncio.new_event_loop()
        try:

            async def _reclaims():
                raise SessionReclaimedError("session 'alpha' was reclaimed")

            task = loop.create_task(_reclaims())
            loop.run_until_complete(asyncio.sleep(0.01))
            runner._heartbeat = task

            runner._stop_heartbeat(loop)
        finally:
            loop.close()

        assert runner._ownership_lost is True

        runner._finish_record()
        assert "mark_stopped" not in record.calls

    def test_an_ordinary_heartbeat_failure_does_not_mark_ownership_lost(self):
        record = SpyRecord()
        runner = _runner(TestLiveNode(), record=record)
        loop = asyncio.new_event_loop()
        try:

            async def _fails():
                raise RuntimeError("db went away")

            task = loop.create_task(_fails())
            loop.run_until_complete(asyncio.sleep(0.01))
            runner._heartbeat = task

            runner._stop_heartbeat(loop)
        finally:
            loop.close()

        assert runner._ownership_lost is False

        runner._finish_record()
        assert record.calls == ["mark_stopped"]
