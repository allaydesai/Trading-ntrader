"""Client-order-ID determinism pins (Story 3.2, Task 6, AC #2).

These pins are about **Nautilus's own generator** operating under this
repo's `trader_id`, not about `live_order_path` — they get their own suite
rather than living beside the suppression-wrap tests.

Component tier: constructs a real `Trader` (with real `DataEngine`/
`RiskEngine`/`ExecutionEngine`), which — measured directly, the same
discipline every live suite in this repo applies — does **not** touch
Nautilus's C logging subsystem. Never a `TradingNode`/`NautilusKernel`, which
does.
"""

import ast
import re
from pathlib import Path

import pytest
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common import Environment
from nautilus_trader.common.component import MessageBus, TestClock, is_logging_initialized
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.data.engine import DataEngine, DataEngineConfig
from nautilus_trader.execution.engine import ExecEngineConfig, ExecutionEngine
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.risk.engine import RiskEngine, RiskEngineConfig
from nautilus_trader.trading.trader import Trader

from src.core.live_session_node import materialise_strategy
from src.models.session import StrategySpec

pytestmark = pytest.mark.component

PROJECT_ROOT = Path(__file__).resolve().parents[3]


#: Real observed live shape (`epics.md`/story record):
#: `O-20260828-193805-621bb88c-000-4` — trader tag, strategy tag, counter.
def _client_order_id_pattern(trader_tag: str) -> str:
    return rf"^O-\d{{8}}-\d{{6}}-{trader_tag}-\d{{3}}-\d+$"


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """A real `Trader` + engines are constructed here — measured not to touch
    C logging, but this fixture is the machine-enforced version of that claim.
    """
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — a real Trader must not require it. If "
        "Nautilus changes that, this suite belongs in tests/integration/ under --forked."
    )


def _new_trader(trader_id: TraderId) -> Trader:
    clock = TestClock()
    msgbus = MessageBus(trader_id=trader_id, clock=clock)
    cache = Cache(database=None)
    portfolio = Portfolio(msgbus, cache, clock)
    data_engine = DataEngine(msgbus=msgbus, cache=cache, clock=clock, config=DataEngineConfig())
    risk_engine = RiskEngine(
        portfolio=portfolio, msgbus=msgbus, cache=cache, clock=clock, config=RiskEngineConfig()
    )
    exec_engine = ExecutionEngine(
        msgbus=msgbus, cache=cache, clock=clock, config=ExecEngineConfig()
    )
    return Trader(
        trader_id=trader_id,
        instance_id=UUID4(),
        msgbus=msgbus,
        cache=cache,
        portfolio=portfolio,
        data_engine=data_engine,
        risk_engine=risk_engine,
        exec_engine=exec_engine,
        clock=clock,
        environment=Environment.BACKTEST,
    )


def _sma_crossover_spec(bar_type: str) -> StrategySpec:
    return StrategySpec(strategy_id="sma_crossover", parameters={}, bar_types=(bar_type,))


def _submit(strategy):
    """Ask the strategy's own `order_factory` for one client order ID."""
    order = strategy.order_factory.market(
        instrument_id=strategy.instrument_id,
        order_side=OrderSide.BUY,
        quantity=Quantity.from_int(10),
    )
    return str(order.client_order_id)


class TestClientOrderIdFormat:
    """Task 6.1 — the deterministic generator's output format, under this
    repo's own `trader_id`.

    Construction is deliberately **two independently materialised**
    `sma_crossover` `StrategySpec`s added to one real `Trader` in order — NOT
    a single two-entry `SessionSpec` (`SessionSpec._reject_duplicate_strategies`
    refuses same-strategy duplicates at create) and NOT crossover+momentum
    (momentum's registry default pins tag `"002"`, `sma_momentum.py:179`, so
    it would not exercise the *positional* auto-assignment this test is for).
    """

    TRADER_ID = TraderId("PAPER-0e8f1c2a")

    def test_the_format_matches_the_real_observed_live_shape(self):
        trader = _new_trader(self.TRADER_ID)
        strategy = materialise_strategy(_sma_crossover_spec("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"))
        trader.add_strategy(strategy)

        client_order_id = _submit(strategy)

        pattern = _client_order_id_pattern("0e8f1c2a")
        assert re.match(pattern, client_order_id), client_order_id

    def test_the_trader_tag_is_the_text_after_the_last_hyphen(self):
        """`TraderId.get_tag()` — why `derive_trader_id` uses `session_id.hex[:8]`."""
        trader = _new_trader(self.TRADER_ID)
        strategy = materialise_strategy(_sma_crossover_spec("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"))
        trader.add_strategy(strategy)

        client_order_id = _submit(strategy)

        assert "-0e8f1c2a-" in client_order_id


class TestOrderIdTagPositionalAssignment:
    """Task 6.3 — determinism across restarts: the auto-assigned
    `order_id_tag` is registration-order-derived
    (`trading/trader.py:406-412`: `f"{len(order_id_tags):03d}"`), so two
    independently materialised specs in a fixed order always land on the
    same tags.
    """

    TRADER_ID = TraderId("PAPER-0e8f1c2a")

    def test_two_strategies_get_positional_tags_000_and_001(self):
        trader = _new_trader(self.TRADER_ID)
        first = materialise_strategy(_sma_crossover_spec("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"))
        second = materialise_strategy(_sma_crossover_spec("MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL"))

        trader.add_strategy(first)
        trader.add_strategy(second)

        assert str(first.id) == "SMACrossover-000"
        assert str(second.id) == "SMACrossover-001"

    def test_tags_are_stable_across_a_simulated_restart(self):
        """Two independent `Trader`s under the SAME `trader_id`, the same
        registration order, freshly materialised strategies each time — the
        shape a process restart actually produces. Tags must not shuffle.
        """
        first_run_tags = []
        second_run_tags = []
        for tags in (first_run_tags, second_run_tags):
            trader = _new_trader(self.TRADER_ID)
            a = materialise_strategy(_sma_crossover_spec("AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"))
            b = materialise_strategy(_sma_crossover_spec("MSFT.NASDAQ-1-MINUTE-LAST-EXTERNAL"))
            trader.add_strategy(a)
            trader.add_strategy(b)
            tags.append(str(a.id))
            tags.append(str(b.id))

        assert first_run_tags == second_run_tags == ["SMACrossover-000", "SMACrossover-001"]


class TestUuidAndHyphenFlagsAreUnused:
    """Task 6.2 — the canary. `use_uuid_client_order_ids` /
    `use_hyphens_in_client_order_ids` are per-`StrategyConfig` options
    (`trading/config.py:70-72`); a future flip to UUID ids is non-deterministic
    and NFR6-hostile. This must go red the day either name appears in `src/`.
    """

    FLAG_NAMES = ("use_uuid_client_order_ids", "use_hyphens_in_client_order_ids")

    def _all_identifiers(self, path: Path) -> set[str]:
        return self._identifiers_in(path.read_text(encoding="utf-8"))

    def _identifiers_in(self, source: str) -> set[str]:
        """Split out from :meth:`_all_identifiers` by the 2026-08-30 review so
        the non-vacuity probe below can drive the **real** scanner instead of
        re-implementing it.
        """
        tree = ast.parse(source)
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.keyword) and node.arg:
                names.add(node.arg)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.add(node.value)
        return names

    def test_neither_flag_is_referenced_anywhere_in_src(self):
        offenders = []
        scanned = 0
        for path in (PROJECT_ROOT / "src").rglob("*.py"):
            scanned += 1
            identifiers = self._all_identifiers(path)
            hits = identifiers & set(self.FLAG_NAMES)
            if hits:
                offenders.append((str(path.relative_to(PROJECT_ROOT)), sorted(hits)))
        # Review fix, 2026-08-30: an empty `offenders` list was the whole
        # assertion, so a wrong `PROJECT_ROOT` (a file move, a packaging
        # change) would make `rglob` yield nothing and leave this permanently
        # green while scanning zero files.
        assert scanned > 50, f"the scan only reached {scanned} files — PROJECT_ROOT is wrong"
        assert not offenders, f"non-default client-order-id flags referenced: {offenders}"

    @pytest.mark.parametrize("flag", FLAG_NAMES)
    def test_the_scan_would_catch_a_planted_reference(self, flag):
        """Review fix, 2026-08-30. This used to re-implement the AST walk
        inline and assert that CPython's `ast` records keyword names — so it
        passed if `_all_identifiers` were deleted, returned `set()`, or were
        inverted. The one test that exists to prove the scan is non-vacuous
        was the one test that never touched it.

        Driven from `FLAG_NAMES` itself, and through all four node shapes the
        scanner claims to cover, so weakening any branch of it goes red here.
        """
        assert flag in self._identifiers_in(f"SMAConfig({flag}=True)"), "ast.keyword"
        assert flag in self._identifiers_in(f"{flag} = True"), "ast.Name"
        assert flag in self._identifiers_in(f"config.{flag}"), "ast.Attribute"
        assert flag in self._identifiers_in(f'setattr(config, "{flag}", True)'), "ast.Constant"

    def test_the_scan_finds_nothing_in_source_that_does_not_mention_the_flags(self):
        """The other half of non-vacuity: the scanner must also be capable of
        returning a miss, or `test_neither_flag_is_referenced_anywhere_in_src`
        proves nothing by passing.
        """
        identifiers = self._identifiers_in("def f(x):\n    return x + 1\n")

        assert not identifiers & set(self.FLAG_NAMES)

    def test_the_defaults_this_repo_relies_on_have_not_drifted(self):
        """Non-vacuity for the *reason* the scan matters: confirms the
        defaults this repo relies on are still what they were measured as.

        Measured directly (not assumed): ``use_uuid_client_order_ids``
        defaults ``False`` — the NFR6-hostile one, staying off is what keeps
        IDs deterministic. ``use_hyphens_in_client_order_ids`` defaults
        ``True`` — hyphens are what the real observed live format
        (``O-20260828-193805-621bb88c-000-4``) already has, so this is the
        wanted default, not a gap. Neither is overridden anywhere in ``src/``.
        """
        from nautilus_trader.trading.config import StrategyConfig

        defaults = StrategyConfig()
        assert defaults.use_uuid_client_order_ids is False
        assert defaults.use_hyphens_in_client_order_ids is True
