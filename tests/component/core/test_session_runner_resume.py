"""Story 4.5 — the runner's resume wiring, end to end through ``runner.run()``.

Component tier with a ``TestLiveNode``, reusing Story 4.4's warm-up helpers
(the double's ``start_strategy`` never runs ``on_start``, so a fake history
request stands in for what both built-ins do there). Three behaviours:

- **D-D** — a pre-4.5 engine cache holding the IB adapter's fabricated order is
  refused inside ``node:connect``, *before* ``run_async()``: the framework's
  own pass would otherwise abort the process on it (Story 4.2, 1.4S).
- **D-C (PO ruling B)** — a strategy whose instrument carries a holding no
  strategy owns is contained through the start-failure path; a sibling on
  another instrument still starts; if none can, the start fails closed.
- **D-E / AC #4** — a strategy that restarts holding its own position is named
  (``strategy.resumed``) after ``reconcile.ok`` and before its warm-up
  completes, so its first live decision follows both.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from nautilus_trader.adapters.interactive_brokers.factories import IB_CLIENTS
from nautilus_trader.common.component import is_logging_initialized
from structlog.testing import capture_logs

import src.core.live_session_runner as runner_module
import src.core.live_session_warmup as warmup_module
from src.core.live_session_resume import (
    LEGACY_POSITION_IMPORT,
    RESUME_REFUSED_EVENT,
    RESUMED_BESIDE_EXCESS_EVENT,
    RESUMED_EVENT,
    SESSION_RESUME_REFUSED_EVENT,
    UNOWNED_POSITION,
    ResumeRefusedError,
)
from src.core.live_session_warmup import COMPLETED_EVENT
from src.core.live_startup_reconcile import OK_EVENT as RECONCILE_OK_EVENT
from src.core.live_strategy_guard import START_FAILED_EVENT, NoStrategyStartedError
from src.core.strategy_registry import StrategyRegistry
from src.models.broker_state import BrokerPosition, BrokerState, CashBalance
from src.models.session import SessionSpec, StrategySpec
from tests.component.core.test_session_runner_warmup import (
    PAPER_ACCOUNT,
    RUN_SECONDS,
    SESSION_ID,
    STARTED_AT,
    SpyRecord,
    _History,
    _never_sleeps,
    _permitting_verifier,
    _settings,
    _start_issues_a_history_request,
)
from tests.component.doubles import TestIBAccountsClient, TestLiveNode

pytestmark = pytest.mark.component

AAPL = "AAPL.NASDAQ"
NVDA = "NVDA.NASDAQ"
CROSSOVER_ID = "SMACrossover-000"
#: 2026-09-21T14:00:00Z — a previous process run, before STARTED_AT.
OPENED_BEFORE = 1_789_999_200_000_000_000


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, "this component test constructed a TradingNode"


@pytest.fixture(autouse=True)
def _restore_the_strategy_registry():
    """``test_session_runner_warmup.py``'s — discovery first, then snapshot."""
    StrategyRegistry.discover()
    strategies = StrategyRegistry._strategies.copy()
    aliases = StrategyRegistry._aliases.copy()
    discovered = StrategyRegistry._discovered
    yield
    StrategyRegistry._strategies = strategies
    StrategyRegistry._aliases = aliases
    StrategyRegistry._discovered = discovered


@pytest.fixture(autouse=True)
def _short_deadline(monkeypatch):
    """``ibkr_request_timeout=0`` in ``_settings``, so the margin is the deadline."""
    monkeypatch.setattr(warmup_module, "WARMUP_DEADLINE_MARGIN_SECONDS", 0.3)


@pytest.fixture
def registered_accounts(monkeypatch):
    def _register(settings, accounts=(PAPER_ACCOUNT,)):
        key = (settings.ibkr_host, settings.ibkr_port, settings.ibkr_live_client_id)
        monkeypatch.setitem(IB_CLIENTS, key, TestIBAccountsClient(accounts))

    return _register


@dataclass
class _Position:
    """An open position as the ``reconcile`` phase and the resume check read it."""

    instrument_id: str
    strategy_id: str
    quantity: str
    ts_opened: int = OPENED_BEFORE
    avg_px_open: float = 217.83

    @property
    def id(self) -> str:
        return f"{self.instrument_id}-{self.strategy_id}"

    @property
    def is_long(self) -> bool:
        return Decimal(self.quantity) > 0

    def signed_decimal_qty(self) -> Decimal:
        return Decimal(self.quantity)


def _broker(*held: tuple[str, str]):
    state = BrokerState(
        account="***626",
        positions=tuple(
            BrokerPosition(
                instrument_id=instrument_id,
                quantity=Decimal(quantity),
                average_price=Decimal("217.90"),
                con_id=4815747,
                symbol=instrument_id.split(".")[0],
            )
            for instrument_id, quantity in held
        ),
        cash=(CashBalance(currency="USD", total_cash=Decimal("100000")),),
        retrieved_at=datetime(2026, 9, 22, 13, 25, tzinfo=UTC),
    )

    async def _read(node, *, log):
        return state

    return _read


def _spec(*entries: tuple[str, str]) -> SessionSpec:
    parameters = {"sma_crossover": {"fast_period": 2, "slow_period": 3}, "momentum": {}}
    return SessionSpec(
        strategies=tuple(
            StrategySpec(
                strategy_id=name,
                parameters=parameters[name],
                bar_types=(f"{instrument}-1-MINUTE-LAST-EXTERNAL",),
            )
            for name, instrument in entries
        )
    )


def _assigns_order_id_tags(node: TestLiveNode) -> None:
    """What the real ``Trader.add_strategy`` does to a strategy with no tag
    (``trading/trader.py:405-412``) and the double does not: the id a restored
    position carries (``SMACrossover-000``) is fixed only here — which is why
    the runner names a resume *after* ``add_strategy``."""
    from nautilus_trader.model.identifiers import StrategyId

    add_strategy = node.trader.add_strategy

    def assigning(strategy):
        if strategy.order_id_tag in (None, str(None)):
            tag = f"{len(node.trader.added_strategies):03d}"
            strategy.change_id(StrategyId(f"{strategy.id.value.partition('-')[0]}-{tag}"))
            strategy.change_order_id_tag(tag)
        add_strategy(strategy)

    node.trader.add_strategy = assigning


def _runner(node: TestLiveNode, spec: SessionSpec, reader) -> runner_module.LiveSessionRunner:
    node.run_seconds_from_first_strategy = True
    _assigns_order_id_tags(node)
    return runner_module.LiveSessionRunner(
        _settings(),
        session_id=SESSION_ID,
        spec=spec,
        record=SpyRecord(),
        started_at=STARTED_AT,
        connect_timeout=2.0,
        node_factory=lambda settings_arg, **kwargs: node,
        account_verifier=_permitting_verifier(),
        broker_state_reader=reader,
        client_builder=lambda *a, **k: None,
        sleeper=_never_sleeps,
    )


def _events(logs) -> list[str]:
    return [entry["event"] for entry in logs]


def _phases(logs) -> list[tuple[str, str]]:
    return [(e["phase"], e["status"]) for e in logs if e.get("phase") and e.get("status")]


class TestAPre45CacheIsRefusedBeforeTheFrameworkRuns:
    """D-D / AC #6."""

    def test_the_start_is_refused_inside_node_connect_and_run_async_never_runs(
        self, registered_accounts
    ):
        registered_accounts(_settings())
        node = TestLiveNode(run_seconds=RUN_SECONDS)
        node.cache.all_orders = [
            SimpleNamespace(strategy_id="EXTERNAL", client_order_id=NVDA, instrument_id=NVDA)
        ]
        ran: list[bool] = []
        run_async = node.run_async

        def spy():
            ran.append(True)
            return run_async()

        node.run_async = spy
        runner = _runner(node, _spec(("sma_crossover", NVDA)), _broker())

        with capture_logs() as logs, pytest.raises(ResumeRefusedError) as caught:
            runner.run()

        assert caught.value.reason == LEGACY_POSITION_IMPORT
        assert ran == [], "run_async ran: the framework's pass would abort on this cache"
        assert ("node:connect", "failed") in _phases(logs)
        assert ("reconcile", "started") not in _phases(logs)
        assert SESSION_RESUME_REFUSED_EVENT in _events(logs)
        assert node.trader.added_strategies == []
        assert runner._record.calls[-1] == "mark_stopped", "the row was not released"

    def test_a_clean_cache_reaches_the_framework(self, registered_accounts):
        registered_accounts(_settings())
        node = TestLiveNode(run_seconds=0.01)

        with capture_logs() as logs:
            _runner(node, _spec(("sma_crossover", NVDA)), _broker()).run()

        assert ("node:connect", "ok") in _phases(logs)
        assert SESSION_RESUME_REFUSED_EVENT not in _events(logs)


class TestAnUnownedHoldingContainsOnlyItsStrategy:
    """D-C, PO ruling B."""

    def test_the_strategy_is_contained_and_its_sibling_on_another_instrument_starts(
        self, registered_accounts, monkeypatch
    ):
        registered_accounts(_settings())
        _History({"SMAMomentum": 0.01}).install(monkeypatch)
        node = TestLiveNode(run_seconds=RUN_SECONDS)
        _start_issues_a_history_request(node)
        node.cache.open_positions = [_Position(AAPL, "INTERNAL-DIFF", "10")]
        runner = _runner(
            node, _spec(("sma_crossover", AAPL), ("momentum", NVDA)), _broker((AAPL, "10"))
        )

        with capture_logs() as logs:
            runner.run()

        (refused,) = [e for e in logs if e["event"] == RESUME_REFUSED_EVENT]
        assert refused["spec_strategy_id"] == "sma_crossover"
        assert refused["instrument_id"] == AAPL
        assert (refused["unowned_quantity"], refused["broker_quantity"]) == ("10", "10")
        (failed,) = [e for e in logs if e["event"] == START_FAILED_EVENT]
        assert failed["spec_strategy_id"] == "sma_crossover"
        added = [type(s).__name__ for s in node.trader.added_strategies]
        assert added == ["SMAMomentum"], "the refused strategy was materialised and added"
        assert runner.trader_started
        (failure,) = runner.contained_failures
        assert failure.spec_strategy_id == "sma_crossover"
        # "Never traded or adjusted" is structural: the resume module calls no
        # order method (`LIVE_MODULE_GLOBS`), and the refused strategy was
        # never materialised, so it holds no order method to call.

    def test_an_excess_beside_the_strategys_own_position_resumes_beside_it(
        self, registered_accounts, monkeypatch
    ):
        """PO ruling 2026-09-28 (review Decision 2: A), amended by PR #35's D5a
        (2026-09-30). The strategy holds +10, IBKR +15 (+5 bought by hand, or
        a split's extra shares while stopped): startup reconciliation passes,
        and the strategy resumes beside the excess — named once, not refused —
        exactly as Story 4.7 leaves it running mid-session. Before D5a this
        test pinned a refusal here."""
        registered_accounts(_settings())
        _History({"SMACrossover": 0.01, "SMAMomentum": 0.01}).install(monkeypatch)
        node = TestLiveNode(run_seconds=RUN_SECONDS)
        _start_issues_a_history_request(node)
        node.cache.open_positions = [
            _Position(NVDA, CROSSOVER_ID, "10"),
            _Position(NVDA, "INTERNAL-DIFF", "5"),
        ]
        runner = _runner(
            node, _spec(("sma_crossover", NVDA), ("momentum", AAPL)), _broker((NVDA, "15"))
        )

        with capture_logs() as logs:
            runner.run()

        assert ("reconcile", "ok") in _phases(logs)
        assert RESUME_REFUSED_EVENT not in _events(logs)
        (beside,) = [e for e in logs if e["event"] == RESUMED_BESIDE_EXCESS_EVENT]
        assert (beside["spec_strategy_id"], beside["instrument_id"]) == ("sma_crossover", NVDA)
        assert (beside["unowned_quantity"], beside["strategy_quantity"]) == ("5", "10")
        assert beside["broker_quantity"] == "15"
        assert [type(s).__name__ for s in node.trader.added_strategies] == [
            "SMACrossover",
            "SMAMomentum",
        ]
        assert runner.trader_started
        assert not runner.contained_failures

    def test_a_stale_own_id_is_unowned_and_refuses_the_strategy_that_replaced_it(
        self, registered_accounts, monkeypatch
    ):
        """D4 (PR #35 code review, PO ruling 2026-09-30). A position under
        ``SMACrossover-007`` — an id no entry of this spec resolves to — is not
        this session's; the entry that resolves to ``SMACrossover-000`` would
        otherwise read its own book as flat and enter beside it (FR38)."""
        registered_accounts(_settings())
        _History({"SMAMomentum": 0.01}).install(monkeypatch)
        node = TestLiveNode(run_seconds=RUN_SECONDS)
        _start_issues_a_history_request(node)
        node.cache.open_positions = [_Position(NVDA, "SMACrossover-007", "10")]
        runner = _runner(
            node, _spec(("sma_crossover", NVDA), ("momentum", AAPL)), _broker((NVDA, "10"))
        )

        with capture_logs() as logs:
            runner.run()

        (refused,) = [e for e in logs if e["event"] == RESUME_REFUSED_EVENT]
        assert (refused["spec_strategy_id"], refused["instrument_id"]) == ("sma_crossover", NVDA)
        assert (refused["unowned_quantity"], refused["strategy_quantity"]) == ("10", "0")
        assert [type(s).__name__ for s in node.trader.added_strategies] == ["SMAMomentum"]
        (failure,) = runner.contained_failures
        assert failure.spec_strategy_id == "sma_crossover"

    def test_when_no_strategy_can_start_the_session_fails_closed(self, registered_accounts):
        registered_accounts(_settings())
        node = TestLiveNode(run_seconds=RUN_SECONDS)
        node.cache.open_positions = [_Position(AAPL, "INTERNAL-DIFF", "10")]
        runner = _runner(node, _spec(("sma_crossover", AAPL)), _broker((AAPL, "10")))

        with capture_logs() as logs, pytest.raises(NoStrategyStartedError):
            runner.run()

        assert RESUME_REFUSED_EVENT in _events(logs)
        assert "session.started" not in _events(logs)
        assert node.trader.added_strategies == []
        assert runner._record.calls[-1] == "mark_stopped"


class TestARefusedStrategyNeverRenumbersItsSiblings:
    """Code review 2026-09-28 (HIGH). ``Trader.add_strategy`` gives a strategy
    with no tag ``f"{len(added):03d}"``, so a refusal *before* ``add_strategy``
    shifted every later strategy's id, and orphaned the position its previous
    run left under the old id. Each strategy is now materialised with the tag
    its **spec** resolves for it (``SessionSpec.order_id_tags``)."""

    def test_the_sibling_keeps_its_own_id_and_resumes_its_own_position(
        self, registered_accounts, monkeypatch
    ):
        registered_accounts(_settings())
        _History({"SMACrossover": 0.01}).install(monkeypatch)
        node = TestLiveNode(run_seconds=RUN_SECONDS)
        _start_issues_a_history_request(node)
        node.cache.open_positions = [
            _Position(AAPL, "INTERNAL-DIFF", "10"),  # no strategy owns it: momentum is refused
            _Position(NVDA, "SMACrossover-001", "22"),  # sma_crossover's own, from its last run
        ]
        spec = _spec(("momentum", AAPL), ("sma_crossover", NVDA))
        runner = _runner(node, spec, _broker((AAPL, "10"), (NVDA, "22")))

        with capture_logs() as logs:
            runner.run()

        (refused,) = [e for e in logs if e["event"] == RESUME_REFUSED_EVENT]
        assert refused["spec_strategy_id"] == "momentum"
        (added,) = node.trader.added_strategies
        assert str(added.id) == "SMACrossover-001", "the refusal renumbered its sibling"
        (resumed,) = [e for e in logs if e["event"] == RESUMED_EVENT]
        assert (resumed["strategy_id"], resumed["quantity"]) == ("SMACrossover-001", "22")
        assert runner.trader_started


class TestAResumedStrategyIsNamedBeforeItsFirstDecision:
    """D-E / AC #4 — ``reconcile.ok`` → ``strategy.resumed`` → ``warmup.completed``."""

    def test_the_order_of_the_milestones(self, registered_accounts, monkeypatch):
        registered_accounts(_settings())
        _History({"SMACrossover": 0.01}).install(monkeypatch)
        node = TestLiveNode(run_seconds=RUN_SECONDS)
        _start_issues_a_history_request(node)
        node.cache.open_positions = [_Position(AAPL, CROSSOVER_ID, "22")]
        runner = _runner(node, _spec(("sma_crossover", AAPL)), _broker((AAPL, "22")))

        with capture_logs() as logs:
            runner.run()

        events = _events(logs)
        assert (
            events.index(RECONCILE_OK_EVENT)
            < events.index(RESUMED_EVENT)
            < events.index(COMPLETED_EVENT)
            < events.index("session.started")
        )
        (resumed,) = [e for e in logs if e["event"] == RESUMED_EVENT]
        assert resumed["strategy_id"] == CROSSOVER_ID
        assert resumed["quantity"] == resumed["broker_quantity"] == "22"
        assert resumed["opened_before_this_run"] is True
        assert RESUME_REFUSED_EVENT not in events

    def test_it_is_logged_before_the_strategy_is_started(self, registered_accounts):
        """Structurally before ``on_start`` (which issues the history request):
        the record reads the cache after ``add_strategy`` fixed the id."""
        registered_accounts(_settings())
        node = TestLiveNode(run_seconds=0.01)
        node.cache.open_positions = [_Position(AAPL, CROSSOVER_ID, "22")]
        start_strategy = node.trader.start_strategy
        seen_at_start: list[bool] = []

        with capture_logs() as logs:

            def spy(strategy_id):
                seen_at_start.append(RESUMED_EVENT in _events(logs))
                start_strategy(strategy_id)

            node.trader.start_strategy = spy
            _runner(node, _spec(("sma_crossover", AAPL)), _broker((AAPL, "22"))).run()

        assert seen_at_start == [True]

    def test_a_flat_restart_names_nothing(self, registered_accounts):
        registered_accounts(_settings())

        with capture_logs() as logs:
            _runner(TestLiveNode(run_seconds=0.01), _spec(("sma_crossover", AAPL)), _broker()).run()

        assert RESUMED_EVENT not in _events(logs)
        assert UNOWNED_POSITION not in str(logs)


def test_the_paper_account_is_never_in_a_resume_record(registered_accounts):
    """NFR26: quantities and prices only."""
    registered_accounts(_settings())
    node = TestLiveNode(run_seconds=0.01)
    node.cache.open_positions = [_Position(AAPL, CROSSOVER_ID, "22")]

    with capture_logs() as logs:
        _runner(node, _spec(("sma_crossover", AAPL)), _broker((AAPL, "22"))).run()

    records = [e for e in logs if e["event"] in (RESUMED_EVENT, RESUME_REFUSED_EVENT)]
    assert records and PAPER_ACCOUNT not in str(records)
