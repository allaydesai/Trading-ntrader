"""Epic-1 acceptance conformance: assembling a TradingNode against IBKR paper.

One test per acceptance criterion in Story 1.3 of
``_bmad-output/planning-artifacts/prd-epic1-scope.md``. See
``test_epic1_ac_gate.py`` for why this suite sits alongside the tier suites
rather than replacing them.

Integration tier for a real reason, not by filing convention: three of these
criteria are about what a *process* holds — the Nautilus C logging subsystem is
process-global and single-init, and the IB adapter keeps module-level caches. The
two that depend on a virgin process run it as a subprocess rather than skipping,
because a skipped criterion is an unevidenced criterion.

Nothing here calls ``node.build()`` or ``node.run()``: ``build()`` runs the IB
factories and ``get_cached_ib_client`` opens a socket to the gateway
(NFR32/NFR33).
"""

import ast
import asyncio
import re
import subprocess
import sys
import tomllib
from contextlib import contextmanager
from pathlib import Path

import nautilus_trader
import pytest
from nautilus_trader.adapters.interactive_brokers import factories as ib_factories
from nautilus_trader.adapters.interactive_brokers.common import IB
from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersDataClientConfig,
    InteractiveBrokersExecClientConfig,
)
from nautilus_trader.adapters.interactive_brokers.factories import (
    InteractiveBrokersLiveDataClientFactory,
    InteractiveBrokersLiveExecClientFactory,
)

from src.config import IBKRSettings
from src.core import live_node_builder
from src.core.live_node_builder import (
    GateRefusedError,
    build_trading_node,
    build_trading_node_config,
)
from tests.integration.core.epic1_criteria import criterion

pytestmark = pytest.mark.integration

TRADER_ID = "PAPER-a1b2c3d4"
PAPER_ACCOUNT = "DU4076626"
LIVE_CLIENT_ID = 10
HISTORICAL_CLIENT_ID = 1
PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: Every third-party top-level module the live core is allowed to import. AR3's
#: "no new dependency" is this set staying closed to *undeclared* packages —
#: not to packages the project already depends on. Story 2.2 widened it: `ntrader
#: live create` persists a session through the same SQLAlchemy repository stack
#: `src/db` already uses, and validates its spec through the same Pydantic
#: models `src/models` already uses — both already-declared dependencies
#: (`pyproject.toml`), neither a new one.
PERMITTED_THIRD_PARTY = frozenset(
    {"nautilus_trader", "structlog", "ibapi", "pydantic", "sqlalchemy"}
)

#: The settings the subprocess probes below build a node from, inlined into the
#: probe source so a child interpreter needs nothing from this module.
_PROBE_SETTINGS = """
from src.config import IBKRSettings

def settings():
    return IBKRSettings(
        _env_file=None,
        ibkr_trading_mode="paper",
        ibkr_port=4002,
        ibkr_host="127.0.0.1",
        tws_account="DU4076626",
        ntrader_real_money_account="",
        ibkr_live_client_id=10,
        ibkr_client_id=1,
        ibkr_read_only=True,
        ibkr_connection_timeout=300,
        ibkr_request_timeout=60,
    )
"""


@pytest.fixture(autouse=True)
def _keep_the_shell_out_of_the_settings(monkeypatch):
    """``_env_file=None`` disables the dotenv file but not ``os.environ``."""
    for name in ("IBKR_MARKET_DATA_TYPE", "IBKR_USE_RTH", "IBKR_RATE_LIMIT"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


def _settings(**overrides) -> IBKRSettings:
    fields = {
        "ibkr_host": "127.0.0.1",
        "ibkr_port": 4002,
        "ibkr_client_id": HISTORICAL_CLIENT_ID,
        "ibkr_live_client_id": LIVE_CLIENT_ID,
        "ibkr_trading_mode": "paper",
        "tws_account": PAPER_ACCOUNT,
        "ntrader_real_money_account": "",
        "ibkr_connection_timeout": 300,
        "ibkr_request_timeout": 60,
        "ibkr_market_data_lines": 100,
        "ibkr_rate_limit": 45,
    }
    fields.update(overrides)
    return IBKRSettings(_env_file=None, **fields)


@contextmanager
def _node(**kwargs):
    """Build a node on a loop this helper owns, and take it back down.

    The loop is explicit for the reason ``live_check_driver._drive`` documents:
    ``TradingNode`` otherwise falls back to ``asyncio.get_event_loop()`` and
    manufactures an orphan nobody will run. Disposal is not optional either —
    ``TradingNode.__init__`` has already created the kernel's non-daemon thread
    pool, which hangs the process at interpreter exit if it is never joined.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    node = None
    try:
        node = build_trading_node(_settings(), trader_id=TRADER_ID, loop=loop, **kwargs)
        yield node
    finally:
        if node is not None:
            node.dispose()
        if not loop.is_closed():
            loop.close()
        asyncio.set_event_loop(None)


def _run_probe(body: str, *, timeout: int = 180) -> subprocess.CompletedProcess:
    """Run a probe in a fresh interpreter, where process state is knowable."""
    return subprocess.run(
        [sys.executable, "-c", _PROBE_SETTINGS + body],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=timeout,
    )


def _live_module_sources() -> dict[Path, str]:
    """Every module Epic 1 added to the live path.

    ``src/cli/commands/`` is globbed rather than naming ``live.py`` alone
    (review fix, 2026-08-22). Story 2.6 split ``live_start.py`` out of
    ``live.py`` for the file-size cap, and the hand-named path silently stopped
    covering the code that moved — so a new third-party import there would have
    been invisible to the guard whose entire job is to notice one. A glob
    cannot be outrun by the next split.
    """
    paths = sorted((PROJECT_ROOT / "src" / "core").glob("live_*.py"))
    paths.extend(sorted((PROJECT_ROOT / "src" / "cli" / "commands").glob("live*.py")))
    return {path: path.read_text() for path in paths}


@criterion("1.3a")
def test_the_config_carries_an_ib_data_client_and_an_ib_exec_client():
    """ "...it produces a TradingNodeConfig using InteractiveBrokersDataClientConfig
    and InteractiveBrokersExecClientConfig (FR2, AR2)."
    """
    config = build_trading_node_config(_settings(), trader_id=TRADER_ID)

    assert isinstance(config.data_clients[IB], InteractiveBrokersDataClientConfig)
    assert isinstance(config.exec_clients[IB], InteractiveBrokersExecClientConfig)
    assert str(config.trader_id) == TRADER_ID
    # An exec client at all is what makes the node an execution destination
    # rather than a second data feed (FR2).
    assert config.exec_clients[IB].account_id == PAPER_ACCOUNT


@criterion("1.3b")
def test_both_ib_live_factories_are_registered_on_the_node():
    """ "...registering InteractiveBrokersLiveDataClientFactory and
    InteractiveBrokersLiveExecClientFactory (FR2, AR2)."

    Asserted rather than assumed because Nautilus fails a *missing* registration
    silently — ``node_builder.py`` logs an error and continues — so a dropped or
    transposed line would surface only during a live run.
    """
    with _node() as node:
        builder = node._builder

        assert builder._data_factories[IB] is InteractiveBrokersLiveDataClientFactory
        assert builder._exec_factories[IB] is InteractiveBrokersLiveExecClientFactory


@criterion("1.3c")
def test_the_gate_runs_before_any_client_config_is_constructed(monkeypatch):
    """ "...it calls evaluate_gate() first and raises without constructing any
    client config if the decision is a refusal — so no connecting code is
    reachable past a failed gate (FR9)."

    Both client-config classes are replaced with landmines. The refusal must not
    trip them; the permitted build must, or the landmines would prove nothing.
    """

    def _landmine(*args, **kwargs):
        raise AssertionError("a client config was constructed")

    monkeypatch.setattr(live_node_builder, "InteractiveBrokersDataClientConfig", _landmine)
    monkeypatch.setattr(live_node_builder, "InteractiveBrokersExecClientConfig", _landmine)

    with pytest.raises(GateRefusedError) as refused:
        build_trading_node_config(_settings(ibkr_trading_mode="live"), trader_id=TRADER_ID)
    assert refused.value.refusal.reason.value == "non_paper_trading_mode"

    # The landmines are live — a permitted build reaches them.
    with pytest.raises(AssertionError, match="a client config was constructed"):
        build_trading_node_config(_settings(), trader_id=TRADER_ID)


@criterion("1.3d")
def test_read_only_is_a_process_scoped_declaration_and_never_a_control():
    """ "...ibkr_read_only is set to False scoped to this process only, and never
    read as a safety control anywhere (FR12, NFR27, AR17, AR43)."

    Three claims, three assertions: the flag is turned off for the node's own
    view, the caller's settings object is left alone (so the change cannot leak
    into anything else in the process), and nothing in the live path branches on
    it — the gate is the load-bearing control.
    """
    settings = _settings(ibkr_read_only=True)
    from_read_only = build_trading_node_config(settings, trader_id=TRADER_ID)
    from_writable = build_trading_node_config(_settings(ibkr_read_only=False), trader_id=TRADER_ID)

    # Nothing downstream branches on the operator's value: both produce the
    # same clients. A flag that were load-bearing could not have that property.
    assert from_read_only.data_clients[IB] == from_writable.data_clients[IB]
    assert from_read_only.exec_clients[IB] == from_writable.exec_clients[IB]

    # Scoped to the node's own copy — the caller's object is untouched.
    assert settings.ibkr_read_only is True

    for path, source in _live_module_sources().items():
        for expression in _boolean_test_expressions(ast.parse(source)):
            names = {
                node.attr if isinstance(node, ast.Attribute) else node.id
                for node in ast.walk(expression)
                if isinstance(node, (ast.Attribute, ast.Name))
            }
            assert "ibkr_read_only" not in names, (
                f"{path.name} branches on ibkr_read_only; it is a declaration of intent, "
                "not a safety control — the gate is (NFR27, AR43)"
            )


def _boolean_test_expressions(tree: ast.AST):
    """Every expression the live path evaluates for its truthiness."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            yield node.test
        elif isinstance(node, ast.BoolOp):
            yield from node.values
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            yield node.operand
        elif isinstance(node, ast.comprehension):
            yield from node.ifs


@criterion("1.3e")
def test_the_log_guard_registers_and_coexists_with_a_backtest_engine():
    """ "...its log guard is registered through set_nautilus_log_guard() before any
    other Nautilus component initialises in-process (FR7, AR20) ... and a node
    started in a process that has already run a BacktestEngine observes no
    C-logging double-init panic."

    Both halves need a process where nothing has claimed the C logging subsystem,
    and by the time this module is imported the worker may already have. Fresh
    interpreters make the precondition true instead of skipping when it is not.
    """
    node_first = _run_probe(
        """
import asyncio
from nautilus_trader.common.component import is_logging_initialized
from src.core.live_node_builder import build_trading_node
from src.utils.logging import get_nautilus_log_guard

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
assert not is_logging_initialized(), "the probe process was not virgin"
assert get_nautilus_log_guard() is None

node = build_trading_node(settings(), trader_id="PAPER-a1b2c3d4", loop=loop)
guard = node.kernel.get_log_guard()
assert guard is not None, "the node created C logging but registered no guard"
assert get_nautilus_log_guard() is guard, "set_nautilus_log_guard stored something else"
node.dispose()
print("GUARD_REGISTERED")
"""
    )
    assert node_first.returncode == 0, f"node-first probe failed:\n{node_first.stderr}"
    assert "GUARD_REGISTERED" in node_first.stdout

    engine_first = _run_probe(
        """
import asyncio
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.common.component import is_logging_initialized
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from src.core.live_node_builder import build_trading_node
from src.utils.logging import get_nautilus_log_guard

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
engine = BacktestEngine(
    config=BacktestEngineConfig(
        trader_id="BACKTESTER-001", logging=LoggingConfig(log_level="ERROR")
    )
)
assert is_logging_initialized(), "the engine did not claim C logging"

node = build_trading_node(settings(), trader_id="PAPER-a1b2c3d4", loop=loop)
# NautilusKernel guards its own init: with the subsystem already owned, the
# second component gets None rather than a second guard, and nothing is stored.
assert node.kernel.get_log_guard() is None
assert get_nautilus_log_guard() is None
node.dispose()
engine.dispose()
print("NO_DOUBLE_INIT")
"""
    )
    assert engine_first.returncode == 0, (
        "a TradingNode built after a BacktestEngine did not survive:\n"
        f"stdout:\n{engine_first.stdout}\nstderr:\n{engine_first.stderr}"
    )
    assert "NO_DOUBLE_INIT" in engine_first.stdout


@criterion("1.3f")
def test_both_clients_connect_on_the_live_client_id():
    """ "...both use ibkr_live_client_id, not ibkr_client_id (FR5)."""
    config = build_trading_node_config(_settings(), trader_id=TRADER_ID)

    assert config.data_clients[IB].ibg_client_id == LIVE_CLIENT_ID
    assert config.exec_clients[IB].ibg_client_id == LIVE_CLIENT_ID
    assert LIVE_CLIENT_ID != HISTORICAL_CLIENT_ID, "the fixture stopped testing anything"


@criterion("1.3g")
def test_shutdown_leaves_nothing_behind_and_a_second_node_still_builds():
    """ "...the process exits without leaking a running event loop or an unclosed
    connection, and a second node can be built in a fresh process without
    inheriting BacktestEngine's single-use constraint."

    A subprocess because the claim is about what a *process* is left holding, and
    this module has already built nodes by the time it runs. A leaked non-daemon
    thread pool or an unclosed loop hangs at interpreter exit rather than
    raising, so the timeout is the assertion for "the process exits".
    """
    probe = _run_probe(
        """
import asyncio
from src.core.live_node_builder import build_trading_node

def build_and_dispose(trader_id):
    # The loop is owned explicitly rather than via asyncio.run(): dispose()
    # calls loop.stop() whenever it finds the loop running.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        build_trading_node(settings(), trader_id=trader_id, loop=loop).dispose()
    finally:
        loop.close()
        asyncio.set_event_loop(None)
    return loop

first = build_and_dispose("PAPER-first001")
second = build_and_dispose("PAPER-second02")

assert first is not second, "the second build reused the first loop"
assert first.is_closed() and second.is_closed(), "an event loop was left open"
assert not first.is_running() and not second.is_running()
print("SECOND_NODE_BUILT")
"""
    )

    assert probe.returncode == 0, (
        f"the probe process did not exit cleanly (rc={probe.returncode})\n"
        f"stdout:\n{probe.stdout}\nstderr:\n{probe.stderr}"
    )
    assert "SECOND_NODE_BUILT" in probe.stdout


@criterion("1.3h")
def test_no_new_dependency_was_added_for_the_live_path():
    """ "...no new dependency has been added — pyproject.toml and uv.lock are
    unchanged, since the IB live adapter and Redis cache backend already ship
    with the installed nautilus-trader 1.220.0 (AR3)."

    Stated as a property rather than as a diff, so it holds for any future edit:
    every third-party module the live path imports must already be supplied by a
    dependency the project declared before this epic.
    """
    assert nautilus_trader.__version__ == "1.220.0"

    # The adapter is part of that distribution, not a separate install.
    distribution_root = Path(nautilus_trader.__file__).resolve().parent
    assert Path(ib_factories.__file__).resolve().is_relative_to(distribution_root)

    imported: set[str] = set()
    for source in _live_module_sources().values():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])

    third_party = imported - _STDLIB_AND_FIRST_PARTY
    assert third_party <= PERMITTED_THIRD_PARTY | {"click", "rich"}, (
        f"the live path imports an undeclared third-party module: {sorted(third_party)}"
    )

    # Read as TOML, not as text: `ibapi` also appears under [tool.mypy.overrides],
    # where it says nothing about what the project installs.
    manifest = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    runtime = manifest["project"]["dependencies"]
    distributions = {re.split(r"[\[<>=!~; ]", spec, maxsplit=1)[0].lower() for spec in runtime}

    assert any(spec.startswith("nautilus-trader[ib]") for spec in runtime), (
        "the IB extra is not declared, so the adapter is not a supplied dependency"
    )
    # `ibapi` has no requirement of its own and must not acquire one: it arrives
    # through that extra, which is exactly what "no new dependency" means here.
    assert "ibapi" not in distributions
    for distribution in ("structlog", "click", "rich", "pydantic", "sqlalchemy"):
        assert distribution in distributions, f"{distribution} is not a declared dependency"


#: Modules the live path may import without them being dependencies at all.
_STDLIB_AND_FIRST_PARTY = frozenset(
    {
        "asyncio",
        "collections",
        # Story 2.5's `src/core/live_session_phases.py`. AR39's phase pair
        # (`status=started` then `ok`/`failed`, or `failed` and re-raise) is a
        # `@contextlib.contextmanager`, which is what makes "a failure stops
        # the sequence" a property of the language rather than of eight
        # try/except blocks a later edit could get wrong in one place.
        "contextlib",
        # Story 2.5 code review (2026-08-21): `StartupHeartbeat` in
        # `live_session_steady_state.py` is a thread because the long startup
        # phases run while the runner's loop is not running — an asyncio task
        # would not tick through exactly the staleness window it exists to
        # cover.
        "threading",
        "dataclasses",
        "datetime",
        "enum",
        "math",
        "os",
        # Story 2.4's Redis reachability preflight (`src/core/live_cache.py`).
        # Deliberately stdlib: nautilus-trader's Redis client is Rust-side and
        # exposes no reachability probe, and there is no Python Redis client in
        # this project — so a raw socket is what keeps AR3 ("zero new
        # dependencies") true while still bounding a connect that otherwise
        # hangs forever.
        "socket",
        # Story 2.6's `src/core/live_session_signals.py`: the process's own
        # signal disposition (SIGINT/SIGTERM/SIGABRT) and the uncatchable
        # force exit on a second stop signal. Both stdlib; neither was
        # imported anywhere on the live path before this story.
        "signal",
        "sys",
        # Story 2.6 review (2026-08-22), same module. `itertools.count` is how
        # the signal handler claims its branch atomically — a `self._count += 1`
        # read-modify-write can be re-entered by a second signal between its
        # load and its store, which turned the operator's force-exit Ctrl-C into
        # a second graceful stop. `logging.shutdown()` flushes the stdlib
        # `RotatingFileHandler` behind structlog before `os._exit` bypasses
        # every handler, so the `session.force_exit` record actually reaches
        # `logs/ntrader.log`. Both stdlib; added by hand, with the reason, for
        # the same discipline Story 2.4 applied to `socket` — do NOT swap this
        # allowlist for `sys.stdlib_module_names`, which would wave the next
        # import through in silence.
        "itertools",
        "logging",
        # Story 2.6 review (2026-08-23), decision D2, in
        # `src/core/live_session_steady_state.py`: the heartbeat's record write
        # runs on that object's OWN `concurrent.futures.ThreadPoolExecutor`
        # rather than through `asyncio.to_thread`, which resolves to the loop's
        # default executor — the one `TradingNode.__init__` replaces with the
        # kernel's and `dispose()` joins with `wait=True`. Measured: that join
        # blocked the teardown 59.81s on a wedged write; on a private pool,
        # 0.00s. `functools.partial` binds the write's keyword arguments, which
        # `run_in_executor` (unlike `to_thread`) does not accept. Both stdlib.
        "concurrent",
        "functools",
        # Story 2.7's `src/core/live_strategy_guard.py`, both stdlib and both
        # added by hand with the reason, the same discipline Story 2.4 applied
        # to `socket` and Story 2.6 to `signal`/`sys` — do NOT swap this
        # allowlist for `sys.stdlib_module_names`, which Story 2.4's review
        # rejected explicitly because an auto-derived list waves the next
        # import through in silence.
        #
        # `traceback`: AC #1 requires the contained failure's traceback in the
        # log, and it must be an EXPLICIT rendered field rather than
        # `exc_info=True` — measured, `structlog.testing.capture_logs` records
        # only `exc_info: True` and never a string, so the redaction NFR26
        # needs could be neither applied nor asserted.
        #
        # `re`: AC #8's `redact_accounts`. `live_gate.mask_account` is a
        # WHOLE-VALUE masker (measured:
        # `mask_account("Error 321: account DU4076626 is not managed")` ->
        # `'***ged'`), so redacting free text in place needs a per-token
        # substitution, which needs a pattern. The story anticipated
        # `traceback` only; `re` is the second name this guard caught, which is
        # the guard doing its job.
        "re",
        "traceback",
        "src",
        "time",
        "typing",
        "uuid",
    }
)
