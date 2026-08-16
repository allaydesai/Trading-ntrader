"""Component tests for the one-shot connectivity-check driver (Story 1.7).

Component tier: this module imports the IB adapter through
``src.core.live_check_driver``. Nothing here constructs a real ``TradingNode`` or
connects to a broker (NFR32/NFR34) — the driver takes its two broker-facing
seams as keyword arguments precisely so this suite can drive every branch,
including the ones an operator would only ever hit against a dead gateway.
"""

import asyncio

import pytest
from nautilus_trader.adapters.interactive_brokers.factories import IB_CLIENTS
from nautilus_trader.common.component import is_logging_initialized
from structlog.testing import capture_logs

from src.config import IBKRSettings
from src.core.live_account_gate import verify_connected_account
from src.core.live_check import BrokerUnreachableError, LiveCheckOutcome
from src.core.live_check_driver import (
    CHECK_TRADER_ID,
    LiveCheckError,
    run_live_check,
)
from src.core.live_check_node import current_event_loop
from src.core.live_gate import GateDecision, GateFlags, GateMode, GateRefusalReason, build_refusal
from src.core.live_market_data import LiveMarketDataError
from src.core.live_node_builder import GateRefusedError, LiveNodeConfigError
from tests.component.doubles import TestBarObserver, TestIBAccountsClient, TestLiveNode

pytestmark = pytest.mark.component

AAPL_1MIN = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"
MSFT_1MIN = "MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL"
PAPER_ACCOUNT = "DU4076626"


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Machine-enforce the claim this file's tier placement rests on.

    Mirrors ``tests/component/core/test_live_node_builder.py``. The assertion is
    on the *delta*, not the absolute state: under ``-n auto`` this file shares a
    worker process with the rest of the component tier, and something else in
    that tier does initialise C logging. What this file must never do is
    *change* the state — which is exactly what would happen if a test here ever
    constructed a real node.
    """
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — the driver's tests must never construct a "
        "real TradingNode. Move it to tests/integration/ and run it under --forked."
    )


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Keep the shell out of the fields the driver and its collaborators read.

    ``_env_file=None`` disables the dotenv file but not ``os.environ``, and
    ``resolve_live_market_data_type`` branches on ``model_fields_set`` — so an
    exported value would change what this suite is actually testing.
    ``IB_MAX_CONNECTION_ATTEMPTS`` is cleared because the driver ``setdefault``s
    it and a leftover value would hide that.
    """
    for name in (
        "IBKR_RATE_LIMIT",
        "IBKR_MARKET_DATA_TYPE",
        "IBKR_USE_RTH",
        "IB_MAX_CONNECTION_ATTEMPTS",
    ):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


def _settings(
    *,
    mode: str = "paper",
    port: int = 7497,
    account: str = PAPER_ACCOUNT,
    real_money_account: str = "",
) -> IBKRSettings:
    """Build settings with every field the driver's collaborators read.

    ``IBKRSettings`` validates ``ibkr_client_id`` against ``ibkr_live_client_id``
    (``validate_client_ids_distinct``), so both are passed even though the driver
    only reads the live one.
    """
    return IBKRSettings(
        _env_file=None,
        ibkr_host="127.0.0.1",
        ibkr_port=port,
        ibkr_client_id=1,
        ibkr_live_client_id=10,
        ibkr_trading_mode=mode,
        tws_account=account,
        ntrader_real_money_account=real_money_account,
        ibkr_rate_limit=45,
        ibkr_market_data_lines=100,
    )


@pytest.fixture
def registered_accounts(monkeypatch):
    """Put an account-naming client in the adapter's cache, and take it back out.

    ``IB_CLIENTS`` is a module-level global that leaks across the shared,
    parallel component tier, so insertion must be undone in teardown —
    ``monkeypatch.setitem``/``delitem`` is the least error-prone form.
    """

    def _register(settings: IBKRSettings, accounts=(PAPER_ACCOUNT,)):
        key = (settings.ibkr_host, settings.ibkr_port, settings.ibkr_live_client_id)
        monkeypatch.setitem(IB_CLIENTS, key, TestIBAccountsClient(accounts))

    return _register


def _permitting_verifier(decision_mode: GateMode = GateMode.PAPER):
    """Stand in for ``verify_connected_account``'s permitting path."""

    async def _verify(node, settings, **kwargs) -> GateDecision:
        return GateDecision(permitted=True, mode=decision_mode)

    return _verify


def _refusing_verifier(reason: GateRefusalReason, message: str = "reported account is not paper"):
    """Stand in for its refusing path — which raises, exactly as Layer 2 does."""

    async def _verify(node, settings, **kwargs) -> GateDecision:
        refusal = build_refusal(reason, message)
        assert refusal.refusal is not None
        raise GateRefusedError(refusal.refusal)

    return _verify


def _node_factory(node: TestLiveNode, *, calls: list | None = None):
    """A factory returning a prepared double, optionally recording its calls."""

    def _factory(settings, **kwargs):
        if calls is not None:
            calls.append(kwargs)
        return node

    return _factory


def _raising_factory(exc: BaseException, *, calls: list | None = None):
    def _factory(settings, **kwargs):
        if calls is not None:
            calls.append(kwargs)
        raise exc

    return _factory


def _run(
    node: TestLiveNode | None = None,
    *,
    settings: IBKRSettings | None = None,
    bar_types=(AAPL_1MIN,),
    observe_seconds: float = 0.0,
    connect_timeout: float = 2.0,
    require_bars: bool = False,
    verifier=None,
    factory=None,
):
    """Drive ``run_live_check`` with doubles, defaulting everything sensible."""
    return run_live_check(
        settings if settings is not None else _settings(),
        bar_types=bar_types,
        observe_seconds=observe_seconds,
        connect_timeout=connect_timeout,
        require_bars=require_bars,
        node_factory=factory if factory is not None else _node_factory(node),  # type: ignore[arg-type]
        account_verifier=verifier if verifier is not None else _permitting_verifier(),
    )


class TestHappyPath:
    """AC #1 — gate, connect, verify, subscribe, report, disconnect, exit 0."""

    def test_a_complete_check_reports_ok_and_exit_zero(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        observer = TestBarObserver([AAPL_1MIN], counts={AAPL_1MIN: 2})
        node = TestLiveNode(
            actors=[observer],
            instrument_ids=["AAPL.NASDAQ"],
            connects_after=1,
        )

        report = _run(node, settings=settings)

        assert report.outcome is LiveCheckOutcome.OK
        assert report.exit_code == 0
        assert report.bars_received == 2
        assert report.counts_by_bar_type == ((AAPL_1MIN, 2),)
        assert report.mode is GateMode.PAPER
        assert report.elapsed_seconds >= 0.0

    def test_the_reported_account_is_masked(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(actors=[TestBarObserver([AAPL_1MIN])], instrument_ids=["AAPL.NASDAQ"])

        report = _run(node, settings=settings)

        assert report.accounts == "***626"
        assert PAPER_ACCOUNT not in report.accounts

    def test_the_node_is_stopped_and_disposed(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(actors=[TestBarObserver([AAPL_1MIN])], instrument_ids=["AAPL.NASDAQ"])

        report = _run(node, settings=settings)

        assert node.built is True
        assert node.stopped is True
        assert node.disposed is True
        assert report.shutdown_problems == ()

    def test_the_factory_receives_the_observer_and_an_explicit_loop(self, registered_accounts):
        """``build_trading_node`` otherwise manufactures an orphan loop."""
        settings = _settings()
        registered_accounts(settings)
        calls: list = []
        node = TestLiveNode(actors=[TestBarObserver([AAPL_1MIN])], instrument_ids=["AAPL.NASDAQ"])

        _run(node, settings=settings, factory=_node_factory(node, calls=calls))

        assert len(calls) == 1
        assert calls[0]["trader_id"] == CHECK_TRADER_ID
        assert calls[0]["bar_observer"] is not None
        assert isinstance(calls[0]["loop"], asyncio.AbstractEventLoop)
        assert tuple(calls[0]["bar_types"]) == (AAPL_1MIN,)

    def test_the_build_is_bounded_so_an_unreachable_gateway_cannot_hang_it(
        self, registered_accounts, monkeypatch
    ):
        """The adapter reconnects indefinitely unless this is set (client.py:138-139)."""
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(actors=[TestBarObserver([AAPL_1MIN])], instrument_ids=["AAPL.NASDAQ"])

        _run(node, settings=settings)

        import os

        assert os.environ.get("IB_MAX_CONNECTION_ATTEMPTS") not in (None, "", "0")


class TestGateRefusal:
    """AC #2 — both gate layers map to exit 3, with nothing constructed on Layer 1."""

    def test_layer_one_refusal_never_builds_a_node(self):
        calls: list = []

        report = _run(
            settings=_settings(port=4001),
            factory=_raising_factory(AssertionError("built a node after a refusal"), calls=calls),
        )

        assert report.outcome is LiveCheckOutcome.GATE_REFUSED
        assert report.exit_code == 3
        assert report.refusal_reason is GateRefusalReason.NON_PAPER_PORT
        assert calls == [], "a refused configuration must not reach the node factory"

    def test_layer_one_refusal_carries_the_gates_own_message(self):
        report = _run(
            settings=_settings(mode="live"), factory=_raising_factory(AssertionError("no"))
        )

        assert "IBKR_TRADING_MODE" in report.message

    def test_layer_two_refusal_is_also_exit_three(self, registered_accounts):
        """Story 1.4 chose one exception class so this needs no second branch."""
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(actors=[TestBarObserver([AAPL_1MIN])], instrument_ids=["AAPL.NASDAQ"])

        report = _run(
            node,
            settings=settings,
            verifier=_refusing_verifier(GateRefusalReason.REPORTED_ACCOUNT_NOT_PAPER),
        )

        assert report.outcome is LiveCheckOutcome.GATE_REFUSED
        assert report.exit_code == 3
        assert report.refusal_reason is GateRefusalReason.REPORTED_ACCOUNT_NOT_PAPER

    def test_a_layer_two_refusal_still_disposes_the_node(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(actors=[TestBarObserver([AAPL_1MIN])], instrument_ids=["AAPL.NASDAQ"])

        _run(
            node,
            settings=settings,
            verifier=_refusing_verifier(GateRefusalReason.ACCOUNT_NOT_REPORTED),
        )

        assert node.disposed is True

    def test_the_verifier_is_handed_the_accounts_the_driver_will_display(self, registered_accounts):
        """One read, judged and rendered — not a second, later read of a mutating set."""
        settings = _settings()
        registered_accounts(settings)
        seen: dict = {}

        async def _verify(node, settings_arg, **kwargs) -> GateDecision:
            seen.update(kwargs)
            return GateDecision(permitted=True, mode=GateMode.PAPER)

        node = TestLiveNode(actors=[TestBarObserver([AAPL_1MIN])], instrument_ids=["AAPL.NASDAQ"])
        _run(node, settings=settings, verifier=_verify)

        assert seen["reported_accounts"] == frozenset({PAPER_ACCOUNT})
        assert seen["cli_flags"] == GateFlags()


class TestBrokerUnreachable:
    """AC #3 — connectivity failure is exit 4, distinct from a refusal."""

    def test_engines_that_never_connect_exit_four(self):
        node = TestLiveNode(actors=[TestBarObserver([AAPL_1MIN])], connects_after=-1)

        report = _run(node, connect_timeout=0.1)

        assert report.outcome is LiveCheckOutcome.BROKER_UNREACHABLE
        assert report.exit_code == 4

    def test_the_unreachable_message_names_where_it_looked(self):
        node = TestLiveNode(actors=[TestBarObserver([AAPL_1MIN])], connects_after=-1)

        report = _run(node, connect_timeout=0.1)

        assert "127.0.0.1" in report.message
        assert "7497" in report.message

    def test_the_connect_wait_is_bounded_by_the_timeout(self):
        node = TestLiveNode(actors=[TestBarObserver([AAPL_1MIN])], connects_after=-1)

        report = _run(node, connect_timeout=0.1)

        assert report.elapsed_seconds < 5.0

    def test_the_build_spends_the_same_budget_as_the_connect_wait(self):
        """One budget, not two. Additive budgets took 115s to say "not listening".

        ``node.build()`` is where the adapter's own connect attempt happens, so
        a deadline started after it would be spent in series with the build.
        Here the build alone consumes the whole timeout, and the connect wait
        must therefore add essentially nothing.
        """
        node = TestLiveNode(
            actors=[TestBarObserver([AAPL_1MIN])],
            connects_after=-1,
            build_delay_seconds=0.3,
        )

        report = _run(node, connect_timeout=0.2)

        assert report.outcome is LiveCheckOutcome.BROKER_UNREACHABLE
        assert report.elapsed_seconds < 1.0

    def test_a_refused_socket_at_build_time_is_a_connectivity_failure(self):
        node = TestLiveNode(
            actors=[TestBarObserver([AAPL_1MIN])],
            raise_on_build=ConnectionRefusedError("connection refused"),
        )

        report = _run(node, connect_timeout=0.1)

        assert report.outcome is LiveCheckOutcome.BROKER_UNREACHABLE
        assert report.exit_code == 4

    def test_a_node_that_stops_before_connecting_is_unreachable(self):
        """``kernel.start_async()`` logs and returns rather than raising."""
        node = TestLiveNode(
            actors=[TestBarObserver([AAPL_1MIN])],
            connects_after=-1,
            run_forever=False,
        )

        report = _run(node, connect_timeout=2.0)

        assert report.outcome is LiveCheckOutcome.BROKER_UNREACHABLE
        assert report.exit_code == 4


class TestConfigErrors:
    """A bad configuration is exit 1 — neither a refusal nor a connectivity failure."""

    def test_node_config_error_is_exit_one(self):
        report = _run(factory=_raising_factory(LiveNodeConfigError("TWS_ACCOUNT is not set")))

        assert report.outcome is LiveCheckOutcome.CONFIG_ERROR
        assert report.exit_code == 1
        assert "TWS_ACCOUNT" in report.message

    def test_market_data_error_is_exit_one(self):
        report = _run(factory=_raising_factory(LiveMarketDataError("not REALTIME")))

        assert report.outcome is LiveCheckOutcome.CONFIG_ERROR
        assert report.exit_code == 1

    def test_an_unparseable_bar_type_is_a_config_error(self):
        """Caught before a node exists — ``build_bar_observer_config`` validates."""
        report = _run(bar_types=("not-a-bar-type",), factory=_raising_factory(AssertionError("no")))

        assert report.outcome is LiveCheckOutcome.CONFIG_ERROR
        assert report.exit_code == 1

    def test_an_unexpected_exception_is_a_generic_error(self):
        report = _run(factory=_raising_factory(RuntimeError("something else entirely")))

        assert report.outcome is LiveCheckOutcome.ERROR
        assert report.exit_code == 1


class TestShutdownDiscipline:
    """Story 1.6's review lesson: everything after the node exists is in a finally."""

    def test_a_build_failure_still_disposes(self):
        node = TestLiveNode(
            actors=[TestBarObserver([AAPL_1MIN])],
            raise_on_build=LiveNodeConfigError("boom"),
        )

        _run(node)

        assert node.disposed is True

    def test_the_event_loop_is_always_closed(self):
        """Story 1.3's shutdown AC: no running loop is left behind, even on failure.

        Asserted against the loop the driver actually created — captured from the
        ``loop=`` keyword it hands its factory — because that is the object whose
        leak would matter. The previous form of this test asserted
        ``get_event_loop().is_closed() or True``, a tautology that passed no
        matter what the driver did, so the AC was unevidenced.
        """
        calls: list = []
        node = TestLiveNode(
            actors=[TestBarObserver([AAPL_1MIN])],
            raise_on_build=LiveNodeConfigError("boom"),
        )

        _run(factory=_node_factory(node, calls=calls))

        driver_loop = calls[0]["loop"]
        assert isinstance(driver_loop, asyncio.AbstractEventLoop)
        assert driver_loop.is_closed(), (
            "the driver left its own event loop open after a failed build. The kernel's "
            "non-daemon ThreadPoolExecutor is then joined at interpreter exit, which hangs "
            "the process rather than failing it"
        )
        assert not driver_loop.is_running()
        # And the thread is not left holding that closed loop: the next
        # `build_trading_node(loop=None)` in this process would otherwise adopt it.
        assert current_event_loop() is not driver_loop
        assert node.disposed is True

    def test_the_event_loop_is_closed_on_the_success_path_too(self, registered_accounts):
        """ "Always" includes the path where nothing went wrong."""
        settings = _settings()
        registered_accounts(settings)
        calls: list = []
        node = TestLiveNode(
            actors=[TestBarObserver([AAPL_1MIN])],
            instrument_ids=["AAPL.NASDAQ"],
            connects_after=1,
        )

        report = _run(settings=settings, factory=_node_factory(node, calls=calls))

        assert report.outcome is LiveCheckOutcome.OK
        driver_loop = calls[0]["loop"]
        assert driver_loop.is_closed()
        assert current_event_loop() is not driver_loop
        assert node.disposed is True

    def test_a_dirty_dispose_is_reported_not_raised(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(
            actors=[TestBarObserver([AAPL_1MIN])],
            instrument_ids=["AAPL.NASDAQ"],
            raise_on_dispose=RuntimeError("dispose exploded"),
        )

        report = _run(node, settings=settings)

        assert report.outcome is LiveCheckOutcome.OK
        assert any("dispose" in problem for problem in report.shutdown_problems)

    def test_a_dirty_stop_does_not_mask_the_primary_outcome(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(
            actors=[TestBarObserver([AAPL_1MIN])],
            instrument_ids=["AAPL.NASDAQ"],
            raise_on_stop=RuntimeError("stop exploded"),
        )

        report = _run(node, settings=settings)

        assert report.outcome is LiveCheckOutcome.OK
        assert any("stop" in problem for problem in report.shutdown_problems)
        assert node.disposed is True


class TestStructuredLogging:
    """AC #4 — the check streams structured ``structlog`` events, not prose.

    The rendered *report* is Rich (the CLI prints it), consistent with the other
    command groups; what streams while the check runs is structlog. This asserts
    the streaming half, which is the half FR48 is about.
    """

    def test_the_check_emits_structured_events_with_separate_fields(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(
            actors=[TestBarObserver([AAPL_1MIN], counts={AAPL_1MIN: 2})],
            instrument_ids=["AAPL.NASDAQ"],
            connects_after=1,
        )

        with capture_logs() as captured:
            report = _run(node, settings=settings)

        assert report.outcome is LiveCheckOutcome.OK
        events = {entry["event"]: entry for entry in captured}
        assert {"gate.static", "live_check.building", "live_check.observing"} <= set(events)

        # Structured means the values are fields, not interpolated into the
        # message — a prose logger would render one string and nothing to filter on.
        building = events["live_check.building"]
        assert building["host"] == settings.ibkr_host
        assert building["port"] == settings.ibkr_port
        assert building["client_id"] == settings.ibkr_live_client_id
        assert building["trader_id"] == CHECK_TRADER_ID
        assert building["log_level"] == "info"

        assert events["gate.static"]["phase"] == "gate:static"
        assert events["gate.static"]["status"] == "ok"

    def test_the_live_client_id_is_the_one_that_reaches_the_logs(self, registered_accounts):
        """Story 1.2's isolation, observable where an operator would check it."""
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(actors=[TestBarObserver([AAPL_1MIN])], connects_after=1)

        with capture_logs() as captured:
            _run(node, settings=settings)

        building = next(e for e in captured if e["event"] == "live_check.building")
        assert building["client_id"] == 10
        assert building["client_id"] != settings.ibkr_client_id

    def test_no_streamed_event_carries_a_full_account_identifier(self, registered_accounts):
        """NFR26 holds for the structured stream, not just the rendered report."""
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(
            actors=[TestBarObserver([AAPL_1MIN])],
            instrument_ids=["AAPL.NASDAQ"],
            connects_after=1,
        )

        with capture_logs() as captured:
            _run(node, settings=settings)

        assert captured, "nothing was captured, so this proves nothing"
        for entry in captured:
            rendered = repr(entry)
            assert PAPER_ACCOUNT not in rendered, (
                f"a streamed event leaked the full account identifier: {entry}"
            )


class TestBarsAndInstruments:
    """Zero bars is not a connectivity failure — but it is never invisible."""

    def test_zero_bars_still_exits_zero_by_default(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(actors=[TestBarObserver([AAPL_1MIN])], instrument_ids=["AAPL.NASDAQ"])

        report = _run(node, settings=settings)

        assert report.exit_code == 0
        assert report.bars_received == 0
        assert "no bars" in report.message.lower()

    def test_require_bars_turns_zero_bars_into_a_failure(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(actors=[TestBarObserver([AAPL_1MIN])], instrument_ids=["AAPL.NASDAQ"])

        report = _run(node, settings=settings, require_bars=True)

        assert report.outcome is LiveCheckOutcome.ERROR
        assert report.exit_code == 1

    def test_require_bars_is_satisfied_by_any_bar(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(
            actors=[TestBarObserver([AAPL_1MIN], counts={AAPL_1MIN: 1})],
            instrument_ids=["AAPL.NASDAQ"],
        )

        report = _run(node, settings=settings, require_bars=True)

        assert report.exit_code == 0

    def test_a_suspected_delayed_feed_fails_even_with_bars(self, registered_accounts):
        """Reporting ok would certify a session running on 15-minute-old prices."""
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(
            actors=[TestBarObserver([AAPL_1MIN], counts={AAPL_1MIN: 5}, delayed_data=True)],
            instrument_ids=["AAPL.NASDAQ"],
        )

        report = _run(node, settings=settings)

        assert report.outcome is LiveCheckOutcome.ERROR
        assert report.exit_code == 1
        assert "delayed" in report.message.lower()
        assert report.bars_received == 5

    def test_an_instrument_the_gateway_never_qualified_is_reported(self, registered_accounts):
        """``load_ids_with_return_async`` skips it; nothing raises and nothing reports."""
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(
            actors=[TestBarObserver([AAPL_1MIN, MSFT_1MIN])],
            instrument_ids=["AAPL.NASDAQ"],
        )

        report = _run(node, settings=settings, bar_types=(AAPL_1MIN, MSFT_1MIN))

        assert report.instruments_requested == ("AAPL.NASDAQ", "MSFT.NASDAQ")
        assert report.instruments_loaded == ("AAPL.NASDAQ",)
        assert report.instruments_missing == ("MSFT.NASDAQ",)

    def test_a_fully_loaded_subscription_set_reports_no_shortfall(self, registered_accounts):
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(
            actors=[TestBarObserver([AAPL_1MIN])],
            instrument_ids=["AAPL.NASDAQ"],
        )

        report = _run(node, settings=settings)

        assert report.instruments_missing == ()


class TestSessionDidNotDoItsJob:
    """The gateway was reached, but the check could not get what it came for."""

    def test_a_node_with_no_observer_fails_rather_than_reporting_zero_bars(self):
        """The kernel instantiates the observer from the node config; if the
        `isinstance` scan finds none, the check has nothing to observe with and
        must say so — reporting `ok, 0 bars` would be a lie.
        """
        node = TestLiveNode(actors=[], instrument_ids=["AAPL.NASDAQ"])

        report = _run(node)

        assert report.outcome is LiveCheckOutcome.ERROR
        assert report.exit_code == 1
        assert "LiveBarObserver" in report.message
        assert node.disposed is True

    def test_a_node_that_stops_mid_window_is_reported_as_itself(self, registered_accounts):
        """Not as "no bars" — that is how the observer's delayed-feed shutdown,
        or any mid-run failure, would otherwise be misfiled as a quiet market.
        """
        settings = _settings()
        registered_accounts(settings)
        node = TestLiveNode(
            actors=[TestBarObserver([AAPL_1MIN])],
            instrument_ids=["AAPL.NASDAQ"],
            run_seconds=0.05,
        )

        report = _run(node, settings=settings, observe_seconds=2.0)

        assert report.outcome is LiveCheckOutcome.ERROR
        assert report.exit_code == 1
        assert "stopped running" in report.message
        assert node.disposed is True


class TestExceptionNameCoupling:
    """``live_check.classify_failure`` keys on class names; pin them here.

    This is the test that makes the string map safe. Without it a rename would
    silently reclassify a gate refusal as a generic error — turning exit 3 into
    exit 1 on the one path FR11 exists for.
    """

    @pytest.mark.parametrize(
        ("klass", "expected_name"),
        [
            (GateRefusedError, "GateRefusedError"),
            (LiveNodeConfigError, "LiveNodeConfigError"),
            (LiveMarketDataError, "LiveMarketDataError"),
            (BrokerUnreachableError, "BrokerUnreachableError"),
        ],
    )
    def test_exception_class_names_are_what_live_check_keys_on(self, klass, expected_name):
        assert klass.__name__ == expected_name

    def test_the_check_error_classifies_as_a_generic_error(self):
        from src.core.live_check import classify_failure

        assert classify_failure(LiveCheckError("boom")) is LiveCheckOutcome.ERROR


class TestNoOrderPath:
    """AC #6 — Epic 1 has no order-submission path, by design."""

    def test_the_driver_never_starts_a_strategy_or_submits_an_order(self):
        from pathlib import Path

        from src.core import live_check_driver

        source = Path(live_check_driver.__file__).read_text(encoding="utf-8")
        for forbidden in ("submit_order", "OrderFactory", "MarketOrder", "add_strategy"):
            assert forbidden not in source, f"{forbidden} must not appear in the check driver"

    def test_the_real_account_verifier_is_the_default_seam(self):
        """The injected default is Story 1.4's function, not a local reimplementation."""
        import inspect

        signature = inspect.signature(run_live_check)
        assert signature.parameters["account_verifier"].default is verify_connected_account
