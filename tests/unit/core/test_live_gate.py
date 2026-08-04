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
