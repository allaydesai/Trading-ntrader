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

    Init kwargs outrank environment variables in pydantic-settings, so a
    developer's real environment can never leak into a test.
    """
    return IBKRSettings(
        _env_file=None,
        ibkr_trading_mode=mode,
        ibkr_port=port,
        tws_account=account,
        ntrader_real_money_account=real_money_account,
    )


def _assert_consistent(decision: GateDecision) -> None:
    """A decision is never half-refused."""
    assert decision.permitted is (decision.refusal is None)


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


class TestGatePurity:
    """The gate's import graph contains no I/O-capable library."""

    @pytest.mark.unit
    def test_module_imports_nothing_that_can_perform_io(self):
        """Parse the module source — sys.modules is polluted by pytest itself."""
        # Arrange
        forbidden = {
            "nautilus_trader",
            "sqlalchemy",
            "ibapi",
            "httpx",
            "requests",
            "redis",
            "psycopg2",
            "asyncpg",
            "socket",
        }
        tree = ast.parse(Path(live_gate.__file__).read_text())

        # Act & Assert — top-level nodes only, which skips `if TYPE_CHECKING:`
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in forbidden
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                assert root not in forbidden
                assert root != "src"  # the gate needs NO runtime src.* import

    @pytest.mark.unit
    def test_gate_reads_no_environment_variables(self):
        """Configuration reaches the gate as typed settings, never via os.environ."""
        # Arrange
        source = Path(live_gate.__file__).read_text()

        # Act & Assert
        assert "os.environ" not in source
        assert "getenv" not in source
