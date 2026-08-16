"""Epic-1 acceptance conformance: the safety gate and the client-id reservation.

One test per acceptance criterion in Stories 1.1 and 1.2 of
``_bmad-output/planning-artifacts/prd-epic1-scope.md``, each marked with the
criterion it evidences so the run's terminal summary reports coverage
criterion-by-criterion (see ``tests/conftest.py``).

**Why this suite exists alongside the tier suites.** The behaviour here is also
covered, in far more depth, by ``tests/unit/core/test_live_gate.py`` and
``tests/unit/test_ibkr_config.py``. Those suites prove the code is right; this
one proves the *product* criteria are met, states each one in the PRD's own
terms, and — through the manifest in ``epic1_criteria.py`` — fails when a
criterion loses its evidence. Depth belongs in the tier suites; nothing here
should grow a truth table.

Nothing in this file contacts a broker, a network or a database (NFR32/NFR34).
"""

import ast
import builtins
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.config import HISTORICAL_CLIENT_ID_ROTATION_SPAN, IBKRSettings
from src.core.live_gate import (
    GateDecision,
    GateFlags,
    GateMode,
    GateRefusalReason,
    evaluate_gate,
    mask_account,
)
from tests.integration.core.epic1_criteria import criterion

pytestmark = pytest.mark.integration

PAPER_ACCOUNT = "DU4076626"
REAL_ACCOUNT = "U1234567"
PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: Layer 1's own refusal reasons — the truth table Story 1.1 is about. The
#: remaining members of the enum belong to Layer 2 (Story 1.4).
LAYER_ONE_REASONS = (
    GateRefusalReason.NON_PAPER_TRADING_MODE,
    GateRefusalReason.NON_PAPER_PORT,
    GateRefusalReason.NON_PAPER_ACCOUNT_PREFIX,
    GateRefusalReason.REAL_MONEY_FLAG_WITHOUT_ENV,
    GateRefusalReason.REAL_MONEY_ENV_WITHOUT_FLAG,
    GateRefusalReason.REAL_MONEY_ACCOUNT_MISMATCH,
)

#: Top-level modules whose presence would mean the gate can reach the outside
#: world, directly or through anything it imports.
IO_CAPABLE_MODULES = frozenset(
    {
        "asyncio",
        "asyncpg",
        "httpx",
        "ibapi",
        "nautilus_trader",
        "psycopg2",
        "pydantic",
        "requests",
        "socket",
        "sqlalchemy",
        "structlog",
    }
)


@pytest.fixture(autouse=True)
def _keep_the_shell_out_of_the_settings(monkeypatch):
    """``_env_file=None`` disables the dotenv file but not ``os.environ``."""
    for name in ("IBKR_CLIENT_ID", "IBKR_LIVE_CLIENT_ID", "IBKR_MARKET_DATA_TYPE"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


def _settings(**overrides) -> IBKRSettings:
    """Settings with every gate-relevant field pinned by an init kwarg."""
    fields = {
        "ibkr_host": "127.0.0.1",
        "ibkr_port": 4002,
        "ibkr_client_id": 1,
        "ibkr_live_client_id": 10,
        "ibkr_trading_mode": "paper",
        "tws_account": PAPER_ACCOUNT,
        "ntrader_real_money_account": "",
    }
    fields.update(overrides)
    return IBKRSettings(_env_file=None, **fields)


# --- Story 1.1: refuse a real-money account before anything connects ---------


@criterion("1.1a")
def test_the_gate_returns_a_decision_without_opening_a_socket_or_a_file(monkeypatch):
    """ "...returns a GateDecision without opening a socket, reading a file, or
    querying a database (AR16)."

    Enforced rather than inspected: the primitives are replaced with landmines
    for the duration of the call, so any I/O the gate reached for — now or after
    a future edit — raises instead of passing unnoticed.
    """
    # Arrange — settings are built first; constructing them is not what is under test.
    cases = [
        (_settings(), GateFlags()),
        (_settings(ibkr_trading_mode="live"), GateFlags()),
        (_settings(ibkr_port=7496), GateFlags()),
        (_settings(tws_account=REAL_ACCOUNT), GateFlags()),
        (_settings(ntrader_real_money_account=REAL_ACCOUNT), GateFlags(real_money=True)),
    ]

    def _landmine(*args, **kwargs):
        raise AssertionError("evaluate_gate performed I/O")

    monkeypatch.setattr(socket, "socket", _landmine)
    monkeypatch.setattr(socket, "create_connection", _landmine)
    monkeypatch.setattr(builtins, "open", _landmine)
    monkeypatch.setattr(builtins, "input", _landmine)

    # Act / Assert — and the permitted/refusal invariant holds on every branch.
    for settings, flags in cases:
        decision = evaluate_gate(settings, flags)
        assert isinstance(decision, GateDecision)
        assert decision.permitted is (decision.refusal is None)


@criterion("1.1b")
def test_importing_the_gate_pulls_in_no_framework_or_io_library():
    """ "...the module imports nothing from nautilus_trader, SQLAlchemy, or any
    I/O library."

    Asserted in a fresh interpreter against ``sys.modules``, which is the whole
    transitive closure — a source-level import scan would miss a library reached
    through ``src.config`` or through a helper three modules down.
    """
    probe = (
        "import sys\n"
        "import src.core.live_gate\n"
        f"banned = {sorted(IO_CAPABLE_MODULES)!r}\n"
        "seen = sorted({n.split('.')[0] for n in sys.modules}.intersection(banned))\n"
        "print('IMPORTED:' + ','.join(seen))\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        timeout=120,
    )

    assert result.returncode == 0, f"the purity probe failed:\n{result.stderr}"
    reported = [line for line in result.stdout.splitlines() if line.startswith("IMPORTED:")]
    assert reported == ["IMPORTED:"], (
        "importing src.core.live_gate pulled in an I/O-capable library: "
        f"{reported or result.stdout.strip()}"
    )


@criterion("1.1c")
def test_paper_mode_a_paper_port_and_a_paper_account_permit():
    """ "...ibkr_trading_mode == 'paper', ibkr_port in {7497, 4002}, and
    tws_account is empty or begins with DU/DF ... the decision permits."
    """
    for port in (7497, 4002):
        for account in ("", PAPER_ACCOUNT, "DF1234567", "du4076626"):
            decision = evaluate_gate(_settings(ibkr_port=port, tws_account=account), GateFlags())

            assert decision.permitted, f"port={port} account={account!r} was refused"
            assert decision.mode is GateMode.PAPER
            assert decision.refusal is None


@criterion("1.1d")
def test_each_failing_condition_refuses_and_names_itself():
    """ "...settings failing any one of those conditions ... a refusal carrying a
    GateRefusal reason naming the specific failing condition."
    """
    cases = {
        GateRefusalReason.NON_PAPER_TRADING_MODE: (
            _settings(ibkr_trading_mode="live"),
            "IBKR_TRADING_MODE",
        ),
        GateRefusalReason.NON_PAPER_PORT: (_settings(ibkr_port=7496), "IBKR_PORT"),
        GateRefusalReason.NON_PAPER_ACCOUNT_PREFIX: (
            _settings(tws_account=REAL_ACCOUNT),
            "TWS_ACCOUNT",
        ),
    }

    for reason, (settings, named_field) in cases.items():
        decision = evaluate_gate(settings, GateFlags())

        assert not decision.permitted
        assert decision.mode is None, "a refused attempt must not report an established mode"
        assert decision.refusal is not None
        assert decision.refusal.reason is reason
        assert named_field in decision.refusal.message


@criterion("1.1e")
def test_an_unrecognised_port_refuses_rather_than_defaulting_to_permitted():
    """ "...an unrecognised port refuses rather than defaults to permitted
    (fail-closed)."

    The live ports (7496/4001) are the obvious case and are covered above; these
    are ports the gate has never been told anything about, which is where a
    default-permit would hide.
    """
    for port in (1, 4003, 5555, 7498, 65535):
        decision = evaluate_gate(_settings(ibkr_port=port), GateFlags())

        assert not decision.permitted, f"port {port} was permitted by default"
        assert decision.refusal is not None
        assert decision.refusal.reason is GateRefusalReason.NON_PAPER_PORT


@criterion("1.1f")
def test_real_money_requires_both_declarations_matching_exactly(monkeypatch):
    """ "...--real-money set but NTRADER_REAL_MONEY_ACCOUNT unset, or the env var
    set but the CLI flag absent, or both present but not matching exactly ...
    every one of those combinations refuses ... and only both-present-and-
    exactly-matching permits (FR10, AR15) ... no interactive prompt exists."
    """
    # Arrange — an interactive prompt is a refusal's substitute, so forbid one.
    monkeypatch.setattr(builtins, "input", lambda *a, **k: pytest.fail("the gate prompted"))

    refusals = {
        # flag without env
        (REAL_ACCOUNT, "", True): GateRefusalReason.REAL_MONEY_FLAG_WITHOUT_ENV,
        # env without flag
        (REAL_ACCOUNT, REAL_ACCOUNT, False): GateRefusalReason.REAL_MONEY_ENV_WITHOUT_FLAG,
        # both present, disagreeing
        (REAL_ACCOUNT, "U7654321", True): GateRefusalReason.REAL_MONEY_ACCOUNT_MISMATCH,
        # both present, differing only in case — matching is exact, not folded
        (REAL_ACCOUNT, REAL_ACCOUNT.lower(), True): GateRefusalReason.REAL_MONEY_ACCOUNT_MISMATCH,
        # authorized, but no target account configured at all
        ("", REAL_ACCOUNT, True): GateRefusalReason.REAL_MONEY_ACCOUNT_MISMATCH,
    }

    for (account, env_account, flag), reason in refusals.items():
        decision = evaluate_gate(
            _settings(tws_account=account, ntrader_real_money_account=env_account),
            GateFlags(real_money=flag),
        )

        assert not decision.permitted, f"{account=} {env_account=} {flag=} was permitted"
        assert decision.refusal is not None
        assert decision.refusal.reason is reason

    # Only both-present-and-exactly-matching crosses.
    permitted = evaluate_gate(
        _settings(tws_account=REAL_ACCOUNT, ntrader_real_money_account=REAL_ACCOUNT),
        GateFlags(real_money=True),
    )
    assert permitted.permitted
    assert permitted.mode is GateMode.REAL_MONEY

    # And consent is a genuine bool, never merely something truthy: a `"false"`
    # arriving from a config file must not read as a declaration.
    truthy_but_not_true = evaluate_gate(_settings(), GateFlags(real_money="false"))  # type: ignore[arg-type]
    assert truthy_but_not_true.permitted
    assert truthy_but_not_true.mode is GateMode.PAPER


@criterion("1.1g")
def test_account_identifiers_are_masked_to_their_last_three_characters():
    """ "...any account identifier in it is masked to its last 3 characters
    (NFR26)."
    """
    on_the_paper_path = evaluate_gate(_settings(tws_account=REAL_ACCOUNT), GateFlags())
    on_the_crossing_path = evaluate_gate(
        _settings(ntrader_real_money_account=REAL_ACCOUNT), GateFlags()
    )

    for decision in (on_the_paper_path, on_the_crossing_path):
        assert decision.refusal is not None
        message = decision.refusal.message
        assert REAL_ACCOUNT not in message, f"a full account leaked into: {message}"
        assert "***567" in message

    # A short identifier discloses nothing at all rather than most of itself.
    assert mask_account("DU1234") == "***"
    assert mask_account("") == ""


@criterion("1.1h")
def test_the_unit_truth_table_suite_exists_and_needs_no_broker():
    """ "...when tests/unit/core/test_live_gate.py runs, it covers the full truth
    table including every refusal path, and requires no broker, network, or
    database (NFR34, NFR32)."

    The criterion is about the test suite, so the suite is what is asserted on:
    that it is there, that every Layer-1 refusal reason appears in it, and that
    it imports nothing that could reach a broker.
    """
    suite = PROJECT_ROOT / "tests" / "unit" / "core" / "test_live_gate.py"
    assert suite.is_file(), f"{suite} is missing — Story 1.1's named evidence does not exist"

    source = suite.read_text()
    missing = [reason.name for reason in LAYER_ONE_REASONS if reason.name not in source]
    assert not missing, f"the truth-table suite never exercises {missing}"

    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

    assert not imported & {"nautilus_trader", "sqlalchemy", "asyncpg", "psycopg2", "socket"}, (
        f"the gate's unit suite reaches for a broker or database library: {sorted(imported)}"
    )


# --- Story 1.2: isolate the live client id from the historical data client ---


@criterion("1.2a")
def test_the_live_client_id_defaults_to_ten_and_loads_from_the_environment(monkeypatch):
    """ "...a new ibkr_live_client_id field exists with default 10, configurable
    via env, while ibkr_client_id keeps its default of 1 (FR5, AR18)."

    The defaults are read off the model rather than off an instance, so an
    exported variable cannot make this pass or fail for the wrong reason.
    """
    assert IBKRSettings.model_fields["ibkr_live_client_id"].default == 10
    assert IBKRSettings.model_fields["ibkr_client_id"].default == 1

    monkeypatch.setenv("IBKR_LIVE_CLIENT_ID", "20")
    assert IBKRSettings(_env_file=None, ibkr_client_id=1).ibkr_live_client_id == 20


@criterion("1.2b")
def test_colliding_client_ids_are_rejected_at_construction(monkeypatch):
    """ "...a Pydantic validation error is raised at construction naming both
    fields ... by a model validator, so it fires regardless of which field was
    overridden."

    Collision, not just equality: the historical client rotates its id upward on
    connect retries, so the two allocations must not intersect at all.
    """
    collisions = (
        {"ibkr_client_id": 10, "ibkr_live_client_id": 10},  # equal, live overridden
        {"ibkr_client_id": 1, "ibkr_live_client_id": 1},  # equal, historical overridden
        {"ibkr_client_id": 5, "ibkr_live_client_id": 10},  # rotation range reaches the live id
        {"ibkr_client_id": 1, "ibkr_live_client_id": 6},  # ...at the far end of the range
    )

    for fields in collisions:
        with pytest.raises(ValidationError) as raised:
            IBKRSettings(_env_file=None, **fields)

        message = str(raised.value)
        assert "ibkr_client_id" in message and "ibkr_live_client_id" in message, (
            f"{fields} was rejected without naming both fields: {message}"
        )

    # The same collision arriving from the environment is rejected identically —
    # a model validator cannot be bypassed by which source supplied the value.
    monkeypatch.setenv("IBKR_LIVE_CLIENT_ID", "3")
    with pytest.raises(ValidationError):
        IBKRSettings(_env_file=None)


@criterion("1.2c")
def test_the_field_documentation_states_the_client_id_reservation():
    """ "...they state the allocation: historical = ibkr_client_id, live session
    = ibkr_live_client_id, on-demand reconcile = ibkr_live_client_id + 1 (AR34)."
    """
    live = IBKRSettings.model_fields["ibkr_live_client_id"].description or ""
    historical = IBKRSettings.model_fields["ibkr_client_id"].description or ""

    for phrase in ("historical", "live session", "reconcile", "ibkr_live_client_id + 1"):
        assert phrase in live, f"the live client-id field never mentions {phrase!r}"

    # The historical field carries the other half: that it rotates, and by how
    # much — the number the reservation is actually sized against.
    assert "rotates" in historical
    assert str(HISTORICAL_CLIENT_ID_ROTATION_SPAN) in historical
