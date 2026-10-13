"""Unit tests for the pre-connection paper/live safety gate.

The gate is a pure function: it decides whether a connection is permitted from
configuration alone, before any network activity. These tests exercise the full
truth table and require no broker, network, or database.
"""

import ast
from pathlib import Path

import pytest

from src.config import IBKRSettings
from src.core import live_gate
from src.core.live_gate import (
    GateDecision,
    GateFlags,
    GateMode,
    GateRefusalReason,
    evaluate_account_gate,
    evaluate_gate,
    mask_account,
)


def _settings(
    *,
    mode: str = "paper",
    port: int = 7497,
    account: str = "",
    real_money_account: str = "",
) -> IBKRSettings:
    """Build settings with all four gate-relevant fields passed explicitly.

    Init kwargs outrank environment variables in pydantic-settings, so none of
    these four fields can pick up a developer's real environment. Note the limit
    of that guarantee: ``_env_file=None`` disables the dotenv file but NOT
    ``os.environ``, so isolation holds only for fields listed here. Any future
    field the gate starts reading must be added to this signature, or the
    developer's shell silently becomes test input.
    """
    return IBKRSettings(
        _env_file=None,
        ibkr_trading_mode=mode,
        ibkr_port=port,
        tws_account=account,
        ntrader_real_money_account=real_money_account,
    )


def _assert_consistent(decision: GateDecision) -> None:
    """A decision is never half-refused, and only a permit establishes a mode."""
    assert decision.permitted is (decision.refusal is None)
    assert decision.permitted is (decision.mode is not None)


class TestPaperPermits:
    """Settings that satisfy every paper condition permit the connection."""

    @pytest.mark.unit
    @pytest.mark.parametrize("port", [7497, 4002])
    @pytest.mark.parametrize("account", ["", "DU1234567", "DF1234567"])
    def test_paper_configuration_is_permitted(self, port, account):
        """Paper mode + paper port + paper (or absent) account is permitted."""
        # Arrange
        settings = _settings(port=port, account=account)

        # Act
        decision = evaluate_gate(settings, GateFlags())

        # Assert
        assert decision.permitted is True
        assert decision.mode is GateMode.PAPER
        assert decision.refusal is None
        _assert_consistent(decision)

    @pytest.mark.unit
    def test_lowercase_paper_account_prefix_is_permitted(self):
        """The paper prefix check normalizes case before comparing."""
        # Arrange
        settings = _settings(account="du1234567")

        # Act
        decision = evaluate_gate(settings, GateFlags())

        # Assert
        assert decision.permitted is True
        assert decision.mode is GateMode.PAPER
        _assert_consistent(decision)


class TestPaperRefusals:
    """Failing any one paper condition refuses, naming that condition."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("mode", "port", "account", "expected_reason"),
        [
            ("live", 7497, "", GateRefusalReason.NON_PAPER_TRADING_MODE),
            ("paper", 7496, "", GateRefusalReason.NON_PAPER_PORT),
            ("paper", 4001, "", GateRefusalReason.NON_PAPER_PORT),
            ("paper", 9999, "", GateRefusalReason.NON_PAPER_PORT),
            ("paper", 7497, "U1234567", GateRefusalReason.NON_PAPER_ACCOUNT_PREFIX),
        ],
        ids=[
            "live-trading-mode",
            "tws-live-port",
            "gateway-live-port",
            "unrecognised-port-fails-closed",
            "non-paper-account-prefix",
        ],
    )
    def test_failing_condition_refuses_with_its_own_reason(
        self, mode, port, account, expected_reason
    ):
        """Each failing condition produces its own refusal reason."""
        # Arrange
        settings = _settings(mode=mode, port=port, account=account)

        # Act
        decision = evaluate_gate(settings, GateFlags())

        # Assert
        assert decision.permitted is False
        assert decision.refusal is not None
        assert decision.refusal.reason is expected_reason
        assert decision.refusal.message
        _assert_consistent(decision)

    @pytest.mark.unit
    def test_unrecognised_port_refuses_rather_than_defaulting_to_permitted(self):
        """An unknown port is fail-closed, not assumed safe."""
        # Arrange
        settings = _settings(port=1234)

        # Act
        decision = evaluate_gate(settings, GateFlags())

        # Assert
        assert decision.permitted is False
        assert decision.refusal is not None
        assert decision.refusal.reason is GateRefusalReason.NON_PAPER_PORT
        _assert_consistent(decision)


class TestRefusalPrecedence:
    """When several conditions fail at once the reason is deterministic."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("mode", "port", "account", "expected_reason"),
        [
            ("live", 7496, "U1234567", GateRefusalReason.NON_PAPER_TRADING_MODE),
            ("paper", 7496, "U1234567", GateRefusalReason.NON_PAPER_PORT),
        ],
        ids=["mode-outranks-port-and-prefix", "port-outranks-prefix"],
    )
    def test_first_failing_condition_in_documented_order_wins(
        self, mode, port, account, expected_reason
    ):
        """Precedence is mode -> port -> prefix."""
        # Arrange
        settings = _settings(mode=mode, port=port, account=account)

        # Act
        decision = evaluate_gate(settings, GateFlags())

        # Assert
        assert decision.refusal is not None
        assert decision.refusal.reason is expected_reason
        _assert_consistent(decision)


class TestRealMoneyCrossing:
    """Only two matching declarations permit real money; everything else refuses."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("real_money_flag", "env_account", "tws_account", "expected_reason"),
        [
            (True, "", "DU1234567", GateRefusalReason.REAL_MONEY_FLAG_WITHOUT_ENV),
            (True, "", "U1234567", GateRefusalReason.REAL_MONEY_FLAG_WITHOUT_ENV),
            (False, "U1234567", "U1234567", GateRefusalReason.REAL_MONEY_ENV_WITHOUT_FLAG),
            (True, "U1234567", "U7654321", GateRefusalReason.REAL_MONEY_ACCOUNT_MISMATCH),
            (True, "U1234567", "", GateRefusalReason.REAL_MONEY_ACCOUNT_MISMATCH),
            (True, "U1234567", "u1234567", GateRefusalReason.REAL_MONEY_ACCOUNT_MISMATCH),
        ],
        ids=[
            "flag-without-env",
            "flag-without-env-live-account",
            "env-without-flag",
            "declarations-disagree",
            "no-account-to-match",
            "match-is-case-sensitive",
        ],
    )
    def test_incomplete_or_mismatched_declarations_refuse(
        self, real_money_flag, env_account, tws_account, expected_reason
    ):
        """Absence of either declaration is a refusal, not a question."""
        # Arrange
        settings = _settings(account=tws_account, real_money_account=env_account)

        # Act
        decision = evaluate_gate(settings, GateFlags(real_money=real_money_flag))

        # Assert
        assert decision.permitted is False
        assert decision.refusal is not None
        assert decision.refusal.reason is expected_reason
        _assert_consistent(decision)

    @pytest.mark.unit
    def test_stale_env_var_refuses_even_when_configuration_is_paper_clean(self):
        """A leftover NTRADER_REAL_MONEY_ACCOUNT is itself a refusal condition."""
        # Arrange — perfect paper settings, except the real-money env var is set
        settings = _settings(
            mode="paper",
            port=7497,
            account="DU1234567",
            real_money_account="U1234567",
        )

        # Act
        decision = evaluate_gate(settings, GateFlags(real_money=False))

        # Assert
        assert decision.permitted is False
        assert decision.refusal is not None
        assert decision.refusal.reason is GateRefusalReason.REAL_MONEY_ENV_WITHOUT_FLAG
        _assert_consistent(decision)

    @pytest.mark.unit
    def test_both_declarations_matching_exactly_permits_real_money(self):
        """Two matching declarations are the crossing mechanism."""
        # Arrange
        settings = _settings(account="U1234567", real_money_account="U1234567")

        # Act
        decision = evaluate_gate(settings, GateFlags(real_money=True))

        # Assert
        assert decision.permitted is True
        assert decision.mode is GateMode.REAL_MONEY
        assert decision.refusal is None
        _assert_consistent(decision)

    @pytest.mark.unit
    def test_real_money_crossing_does_not_evaluate_paper_conditions(self):
        """A live port and live mode do not block an authorized crossing."""
        # Arrange
        settings = _settings(
            mode="live",
            port=7496,
            account="U1234567",
            real_money_account="U1234567",
        )

        # Act
        decision = evaluate_gate(settings, GateFlags(real_money=True))

        # Assert
        assert decision.permitted is True
        assert decision.mode is GateMode.REAL_MONEY
        _assert_consistent(decision)

    @pytest.mark.unit
    def test_surrounding_whitespace_is_ignored_on_both_declarations(self):
        """Declarations are compared after stripping."""
        # Arrange
        settings = _settings(account="  U1234567  ", real_money_account=" U1234567 ")

        # Act
        decision = evaluate_gate(settings, GateFlags(real_money=True))

        # Assert
        assert decision.permitted is True
        assert decision.mode is GateMode.REAL_MONEY
        _assert_consistent(decision)


class TestAccountMasking:
    """No refusal ever carries a raw account identifier."""

    @pytest.mark.unit
    def test_mismatch_refusal_masks_the_account(self):
        """A mismatch names the account only in masked form."""
        # Arrange
        settings = _settings(account="DU1234567", real_money_account="U7654321")

        # Act
        decision = evaluate_gate(settings, GateFlags(real_money=True))

        # Assert
        assert decision.refusal is not None
        assert "***567" in decision.refusal.message
        assert "DU1234567" not in decision.refusal.message
        assert "DU1234567" not in repr(decision)
        assert "U7654321" not in repr(decision)

    @pytest.mark.unit
    def test_prefix_refusal_masks_the_account(self):
        """A non-paper prefix refusal names the account only in masked form."""
        # Arrange
        settings = _settings(account="U1234567")

        # Act
        decision = evaluate_gate(settings, GateFlags())

        # Assert
        assert decision.refusal is not None
        assert "***567" in decision.refusal.message
        assert "U1234567" not in decision.refusal.message
        assert "U1234567" not in repr(decision)


class TestAccountGateLayerOneDominance:
    """Layer 2 is Layer 1 AND more; it can never rescue a refused configuration."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("mode", "port", "account", "real_money_account", "real_money_flag"),
        [
            ("live", 7497, "DU4076626", "", False),
            ("paper", 7496, "DU4076626", "", False),
            ("paper", 7497, "U1234567", "", False),
            ("paper", 7497, "DU4076626", "", True),
            ("paper", 7497, "DU4076626", "U1234567", False),
            ("paper", 7497, "DU1234567", "U1234567", True),
        ],
        ids=[
            "live-trading-mode",
            "non-paper-port",
            "non-paper-configured-prefix",
            "flag-without-env",
            "env-without-flag",
            "declarations-disagree",
        ],
    )
    def test_layer_one_refusal_is_returned_unchanged(
        self, mode, port, account, real_money_account, real_money_flag
    ):
        """A spotless reported account cannot overturn a static-gate refusal."""
        # Arrange — the reported set is deliberately perfect paper
        settings = _settings(
            mode=mode, port=port, account=account, real_money_account=real_money_account
        )
        flags = GateFlags(real_money=real_money_flag)
        layer_one = evaluate_gate(settings, flags)

        # Act
        decision = evaluate_account_gate(settings, flags, frozenset({"DU4076626"}))

        # Assert
        assert layer_one.permitted is False
        assert decision.permitted is False
        assert decision.refusal is not None
        assert layer_one.refusal is not None
        assert decision.refusal.reason is layer_one.refusal.reason
        assert decision.refusal.message == layer_one.refusal.message
        _assert_consistent(decision)


class TestAccountGatePaperPath:
    """On the paper path every account the gateway names must be a paper account."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "reported",
        [
            frozenset({"DU4076626"}),
            frozenset({"DF1234567"}),
            frozenset({"DU4076626", "DU1234567"}),
            frozenset({"  DU4076626  ", ""}),
        ],
        ids=["single-du", "single-df", "two-paper-accounts", "whitespace-and-blank-ignored"],
    )
    def test_all_paper_accounts_permit(self, reported):
        """A reported set that is entirely paper permits, in PAPER mode."""
        # Arrange
        settings = _settings(account="DU4076626")

        # Act
        decision = evaluate_account_gate(settings, GateFlags(), reported)

        # Assert
        assert decision.permitted is True
        assert decision.mode is GateMode.PAPER
        _assert_consistent(decision)

    @pytest.mark.unit
    def test_one_non_paper_account_refuses_the_whole_connection(self):
        """The configured account is clean; the gateway also manages a real one."""
        # Arrange — exactly the hole Layer 1 and the adapter's own check both miss
        settings = _settings(account="DU4076626")

        # Act
        decision = evaluate_account_gate(
            settings, GateFlags(), frozenset({"DU4076626", "U1234567"})
        )

        # Assert
        assert decision.permitted is False
        assert decision.refusal is not None
        assert decision.refusal.reason is GateRefusalReason.REPORTED_ACCOUNT_NOT_PAPER
        _assert_consistent(decision)

    @pytest.mark.unit
    def test_lowercase_paper_prefix_is_permitted_exactly_as_layer_one_does(self):
        """The prefix test upper-cases, mirroring `_evaluate_paper`."""
        # Arrange
        settings = _settings(account="DU4076626")

        # Act
        decision = evaluate_account_gate(settings, GateFlags(), frozenset({"du4076626"}))

        # Assert
        assert decision.permitted is True
        assert decision.mode is GateMode.PAPER
        _assert_consistent(decision)


class TestAccountGateFailsClosedWithoutEvidence:
    """No evidence is a refusal, never a permit."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "reported",
        [frozenset(), frozenset({""}), frozenset({"   ", ""})],
        ids=["empty-set", "one-blank", "only-whitespace"],
    )
    def test_absent_or_blank_accounts_refuse(self, reported):
        """An empty reported set cannot establish that the account is paper."""
        # Arrange
        settings = _settings(account="DU4076626")

        # Act
        decision = evaluate_account_gate(settings, GateFlags(), reported)

        # Assert
        assert decision.permitted is False
        assert decision.refusal is not None
        assert decision.refusal.reason is GateRefusalReason.ACCOUNT_NOT_REPORTED
        _assert_consistent(decision)

    @pytest.mark.unit
    def test_absent_accounts_refuse_on_the_real_money_path_too(self):
        """The crossing is not exempt from needing evidence."""
        # Arrange
        settings = _settings(account="U1234567", real_money_account="U1234567")

        # Act
        decision = evaluate_account_gate(settings, GateFlags(real_money=True), frozenset())

        # Assert
        assert decision.permitted is False
        assert decision.refusal is not None
        assert decision.refusal.reason is GateRefusalReason.ACCOUNT_NOT_REPORTED
        _assert_consistent(decision)


class TestAccountGateRealMoneyCrossing:
    """The crossing is checked against what the broker says, not against config."""

    @pytest.mark.unit
    def test_authorized_account_reported_by_the_gateway_permits(self):
        """Both declarations agree AND the gateway confirms the account exists."""
        # Arrange
        settings = _settings(account="U1234567", real_money_account="U1234567")

        # Act
        decision = evaluate_account_gate(
            settings, GateFlags(real_money=True), frozenset({"U1234567"})
        )

        # Assert
        assert decision.permitted is True
        assert decision.mode is GateMode.REAL_MONEY
        _assert_consistent(decision)

    @pytest.mark.unit
    def test_authorized_account_absent_from_the_reported_set_refuses(self):
        """Two config values can agree with each other and disagree with the world."""
        # Arrange
        settings = _settings(account="U1234567", real_money_account="U1234567")

        # Act
        decision = evaluate_account_gate(
            settings, GateFlags(real_money=True), frozenset({"U7654321"})
        )

        # Assert
        assert decision.permitted is False
        assert decision.refusal is not None
        assert decision.refusal.reason is GateRefusalReason.REPORTED_ACCOUNT_NOT_AUTHORIZED
        _assert_consistent(decision)

    @pytest.mark.unit
    def test_paper_prefixed_authorization_refuses(self):
        """`--real-money` against a demo account: orders the operator thinks are real."""
        # Arrange — closes the deferred finding recorded against Story 1.1
        settings = _settings(account="DU1234567", real_money_account="DU1234567")

        # Act
        decision = evaluate_account_gate(
            settings, GateFlags(real_money=True), frozenset({"DU1234567"})
        )

        # Assert
        assert decision.permitted is False
        assert decision.refusal is not None
        assert decision.refusal.reason is GateRefusalReason.REPORTED_ACCOUNT_IS_PAPER
        _assert_consistent(decision)

    @pytest.mark.unit
    def test_identity_match_is_case_sensitive_so_folding_cannot_widen_it(self):
        """Case folding here would make MORE accounts match — on this path, permit more."""
        # Arrange
        settings = _settings(account="U1234567", real_money_account="U1234567")

        # Act
        decision = evaluate_account_gate(
            settings, GateFlags(real_money=True), frozenset({"u1234567"})
        )

        # Assert
        assert decision.permitted is False
        assert decision.refusal is not None
        assert decision.refusal.reason is GateRefusalReason.REPORTED_ACCOUNT_NOT_AUTHORIZED
        _assert_consistent(decision)

    @pytest.mark.unit
    def test_a_real_money_gateway_may_also_manage_paper_accounts(self):
        """Only the authorized account is judged on the crossing path."""
        # Arrange
        settings = _settings(account="U1234567", real_money_account="U1234567")

        # Act
        decision = evaluate_account_gate(
            settings, GateFlags(real_money=True), frozenset({"U1234567", "DU4076626"})
        )

        # Assert
        assert decision.permitted is True
        assert decision.mode is GateMode.REAL_MONEY
        _assert_consistent(decision)


class TestAccountGateMasking:
    """No Layer 2 refusal ever carries a raw account identifier (NFR26)."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("account", "real_money_account", "real_money_flag", "reported"),
        [
            ("DU4076626", "", False, frozenset({"DU4076626", "U1234567"})),
            ("U1234567", "U1234567", True, frozenset({"U7654321"})),
            ("DU1234567", "DU1234567", True, frozenset({"DU1234567"})),
            ("DU4076626", "", False, frozenset()),
        ],
        ids=[
            "reported-not-paper",
            "not-authorized",
            "authorization-is-paper",
            "nothing-reported",
        ],
    )
    def test_no_layer_two_refusal_leaks_a_full_account(
        self, account, real_money_account, real_money_flag, reported
    ):
        """Every account named in a refusal appears masked, and never in full."""
        # Arrange
        settings = _settings(account=account, real_money_account=real_money_account)
        raw_accounts = {account, real_money_account, *reported} - {""}

        # Act
        decision = evaluate_account_gate(settings, GateFlags(real_money=real_money_flag), reported)

        # Assert
        assert decision.refusal is not None
        message = decision.refusal.message
        assert message
        for raw in raw_accounts:
            assert raw not in message, f"{raw!r} leaked into {message!r}"
            assert raw not in repr(decision)

    @pytest.mark.unit
    def test_refusal_message_renders_masked_accounts_in_sorted_order(self):
        """A frozenset has no stable iteration order; the message must anyway.

        Asserted as *sorted order*, not as "two calls agree". Two frozensets
        built from the same strings in different insertion orders iterate
        identically within one process — string hashes are fixed — so comparing
        two such calls would pass with the ``sorted()`` removed and pin nothing.
        """
        # Arrange — masks: ***567, ***321, ***999. Sorted: ***321, ***567, ***999
        settings = _settings(account="DU4076626")
        reported = frozenset({"U1234567", "U7654321", "U1230999", "DU4076626"})

        # Act
        decision = evaluate_account_gate(settings, GateFlags(), reported)

        # Assert
        assert decision.refusal is not None
        message = decision.refusal.message
        positions = [message.index(mask) for mask in ("***321", "***567", "***999")]
        assert positions == sorted(positions), f"masked accounts are unsorted in {message!r}"

    @pytest.mark.unit
    def test_accounts_sharing_a_masked_suffix_are_rendered_once(self):
        """Distinct accounts can collide under masking; ``***567, ***567`` helps nobody."""
        # Arrange
        settings = _settings(account="DU4076626")

        # Act
        decision = evaluate_account_gate(
            settings, GateFlags(), frozenset({"U1234567", "X7654567", "DU4076626"})
        )

        # Assert
        assert decision.refusal is not None
        assert decision.refusal.message.count("***567") == 1


class TestMaskAccount:
    """The masking helper is public — Story 1.4 reuses it."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("account", "expected"),
        [
            ("", ""),
            ("A", "***"),
            ("AB", "***"),
            ("ABC", "***"),
            ("DU1234567", "***567"),
            ("U7654321", "***321"),
        ],
    )
    def test_mask_account_keeps_only_the_last_three_characters(self, account, expected):
        """Masking is last-3-characters, with short values fully masked."""
        assert mask_account(account) == expected

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("account", "expected"),
        [
            ("ABCD", "***"),
            ("ABCDE", "***"),
            ("ABCDEF", "***"),
            ("ABCDEFG", "***EFG"),
        ],
        ids=["length-4", "length-5", "length-6-last-fully-masked", "length-7-first-revealing"],
    )
    def test_short_accounts_are_fully_masked_up_to_the_disclosure_boundary(self, account, expected):
        """The tail is revealed only once it is a minority of the identifier.

        Pins the boundary the masking contract is actually defined by: at length
        4 a last-3 reveal would disclose 75% of the value.
        """
        assert mask_account(account) == expected

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("account", "expected"),
        [
            ("  U7654321  ", "***321"),
            ("U1234567\n", "***567"),
            ("   ", ""),
            ("\n\t", ""),
        ],
        ids=["surrounding-spaces", "trailing-newline", "spaces-only", "whitespace-only"],
    )
    def test_mask_account_normalizes_its_own_input(self, account, expected):
        """Story 1.4 masks gateway-reported values that never pass through evaluate_gate.

        A trailing newline would otherwise be injected verbatim into a log line.
        """
        assert mask_account(account) == expected


ALLOWED_RUNTIME_IMPORTS = frozenset({"dataclasses", "enum", "typing"})
RELATIVE_IMPORT_ROOT = "."


def _is_type_checking_test(node: ast.expr) -> bool:
    """True for the `TYPE_CHECKING` / `typing.TYPE_CHECKING` guard expression."""
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    if isinstance(node, ast.Attribute):
        return node.attr == "TYPE_CHECKING"
    return False


def _runtime_import_roots(tree: ast.Module) -> list[str]:
    """Every root package imported at runtime, anywhere in the tree.

    Walks the WHOLE tree, not just `tree.body` — a function-level, try-wrapped,
    or otherwise nested import is precisely how I/O gets added back while the
    top-level import block stays clean. Only imports inside an `if
    TYPE_CHECKING:` block are excluded, since those never execute.

    A relative import yields ``"."``, which no whitelist contains: `node.module`
    is None for `from . import x`, so treating it as an empty root would let it
    slip past a name-based check.
    """
    guarded = {
        id(descendant)
        for node in ast.walk(tree)
        if isinstance(node, ast.If) and _is_type_checking_test(node.test)
        for descendant in ast.walk(node)
    }

    roots: list[str] = []
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.Import):
            roots.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                roots.append(RELATIVE_IMPORT_ROOT)
            else:
                roots.append((node.module or "").split(".")[0])
    return roots


class TestGatePurity:
    """The gate's import graph contains no I/O-capable library."""

    @pytest.mark.unit
    def test_module_imports_nothing_that_can_perform_io(self):
        """Parse the module source — sys.modules is polluted by pytest itself.

        Asserted as a WHITELIST rather than a blacklist: a forbidden-name list
        only catches the libraries someone thought to enumerate, and would wave
        through `os`, `subprocess`, `urllib`, `importlib`, `pathlib`, `aiohttp`,
        and every `src.*` module reachable through `ast.Import`.
        """
        # Arrange
        tree = ast.parse(Path(live_gate.__file__).read_text())

        # Act
        roots = _runtime_import_roots(tree)

        # Assert — no runtime src.* import, so no transitive path to a broker library
        assert roots, "expected the gate to import something"
        unexpected = sorted(set(roots) - ALLOWED_RUNTIME_IMPORTS)
        assert not unexpected, f"gate gained runtime imports: {unexpected}"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "source",
        [
            "import socket",
            "import src.db.session",
            "import src.services.ibkr_client",
            "from src.config import IBKRSettings",
            "from .config import IBKRSettings",
            "from . import config",
            "import os",
            "import importlib",
            "def evaluate_gate():\n    import socket\n",
            "try:\n    import redis\nexcept ImportError:\n    redis = None\n",
            "if True:\n    import httpx\n",
            "class C:\n    def m(self):\n        import asyncpg\n",
        ],
        ids=[
            "top-level-socket",
            "import-src-db",
            "import-src-services",
            "from-src-config",
            "relative-from-config",
            "relative-bare",
            "os-not-in-any-blacklist",
            "importlib-indirection",
            "function-level-import",
            "try-wrapped-import",
            "nested-in-if",
            "method-level-import",
        ],
    )
    def test_the_purity_check_actually_rejects_impure_sources(self, source):
        """The guard must be able to fail — each case defeated the original check.

        Without this, a purity test that silently passes everything is
        indistinguishable from one that works.
        """
        # Arrange & Act
        roots = _runtime_import_roots(ast.parse(source))

        # Assert
        assert set(roots) - ALLOWED_RUNTIME_IMPORTS, f"{source!r} slipped through the check"

    @pytest.mark.unit
    def test_the_purity_check_still_ignores_the_type_checking_guard(self):
        """The TYPE_CHECKING import of IBKRSettings must not count as runtime."""
        # Arrange
        source = (
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from src.config import X\n"
        )

        # Act
        roots = _runtime_import_roots(ast.parse(source))

        # Assert
        assert roots == ["typing"]

    @pytest.mark.unit
    def test_gate_reads_no_environment_variables(self):
        """Configuration reaches the gate as typed settings, never via os.environ."""
        # Arrange
        source = Path(live_gate.__file__).read_text()

        # Act & Assert
        assert "os.environ" not in source
        assert "getenv" not in source
