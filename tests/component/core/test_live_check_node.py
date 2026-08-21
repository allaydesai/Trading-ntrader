"""Component tests for the node plumbing's retry budget (Stories 1.7, 2.5).

⚠️ **NEW file, where Story 2.5's Files table says MOD.** ``live_check_node.py``
never had a suite of its own — its behaviour was covered indirectly through
``test_live_check_driver.py``. Story 2.5 adds a parameter to ``build_clients``
whose whole purpose is that a *session* and a *check* choose different budgets,
and that difference deserves a test that names it.

Component tier: this module imports the IB adapter transitively through
``live_check_node``. Nothing here constructs a real ``TradingNode``.
"""

import pytest
from nautilus_trader.common.component import is_logging_initialized

from src.config import IBKRSettings
from src.core.live_check_node import (
    BUILD_CONNECTION_ATTEMPTS,
    bounded_connection_attempts,
    build_clients,
)

pytestmark = pytest.mark.component


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Machine-enforce this file's tier placement. Mirrors its siblings."""
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state — nothing here may "
        "construct a real TradingNode."
    )


@pytest.fixture(autouse=True)
def _clear_the_attempt_budget(monkeypatch):
    """``build_clients`` writes a process-wide env var; a leftover value hides it."""
    monkeypatch.delenv("IB_MAX_CONNECTION_ATTEMPTS", raising=False)


def _settings() -> IBKRSettings:
    return IBKRSettings(
        _env_file=None,
        ibkr_trading_mode="paper",
        ibkr_port=4002,
        ibkr_host="127.0.0.1",
        tws_account="DU4076626",
        ibkr_live_client_id=10,
        ibkr_client_id=1,
    )


class _SpyNode:
    """Only ``build()`` is reached; the env var is read before it."""

    def __init__(self) -> None:
        self.built = 0

    def build(self) -> None:
        self.built += 1


class TestTheSessionCanChooseItsOwnAttemptBudget:
    """Story 2.5: a session should outlast a gateway restart; a check should not.

    ``BUILD_CONNECTION_ATTEMPTS = "1"`` is documented in ``live_check_node`` as
    *a check policy, explicitly not a session policy*: *"A check exists to
    report what it found, not to outlast a gateway restart."* A 6.5-hour
    session wants more than one attempt at start, and this parameter is how it
    asks for one without either command reaching into the other's constant.
    """

    def test_the_default_is_still_the_checks_own_budget(self, monkeypatch):
        """``live_check_driver`` passes nothing and must be unaffected."""
        node = _SpyNode()

        build_clients(node, _settings(), trader_id="PAPER-a1b2c3d4")

        import os

        assert os.environ["IB_MAX_CONNECTION_ATTEMPTS"] == BUILD_CONNECTION_ATTEMPTS
        assert node.built == 1

    def test_an_explicit_budget_reaches_the_environment_the_adapter_reads(self):
        node = _SpyNode()

        build_clients(node, _settings(), trader_id="PAPER-a1b2c3d4", max_connection_attempts="3")

        import os

        assert os.environ["IB_MAX_CONNECTION_ATTEMPTS"] == "3"

    def test_the_parameter_is_keyword_only_and_defaulted(self):
        """A required parameter would break ``live_check_driver.py:212`` at
        runtime without changing the ``NodeFactory`` alias it is called through.
        """
        import inspect

        parameter = inspect.signature(build_clients).parameters["max_connection_attempts"]

        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default == BUILD_CONNECTION_ATTEMPTS

    def test_an_operators_own_valid_choice_still_wins_over_either_default(self, monkeypatch):
        """``bounded_connection_attempts`` honours a positive exported value.

        Passing a session budget must not take that away — an operator who
        exported a number chose it deliberately.
        """
        monkeypatch.setenv("IB_MAX_CONNECTION_ATTEMPTS", "7")
        node = _SpyNode()

        build_clients(node, _settings(), trader_id="PAPER-a1b2c3d4", max_connection_attempts="3")

        import os

        assert os.environ["IB_MAX_CONNECTION_ATTEMPTS"] == "7"

    @pytest.mark.parametrize("hostile", ["0", "", "  ", "-1", "not-a-number"])
    def test_a_value_that_bounds_nothing_is_replaced_by_the_callers_budget(
        self, monkeypatch, hostile
    ):
        """``0`` means *infinite* to the adapter, so ``setdefault`` is not enough."""
        monkeypatch.setenv("IB_MAX_CONNECTION_ATTEMPTS", hostile)
        node = _SpyNode()

        build_clients(node, _settings(), trader_id="PAPER-a1b2c3d4", max_connection_attempts="3")

        import os

        assert os.environ["IB_MAX_CONNECTION_ATTEMPTS"] == "3"


class TestBoundedConnectionAttempts:
    """The helper both budgets route through, tested directly."""

    def test_it_falls_back_to_the_callers_budget_when_nothing_is_exported(self):
        assert bounded_connection_attempts(fallback="3") == "3"

    def test_it_defaults_to_the_checks_budget(self):
        assert bounded_connection_attempts() == BUILD_CONNECTION_ATTEMPTS
