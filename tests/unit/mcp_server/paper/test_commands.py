"""Paper commands: the exact text Allay runs to start a session for a frozen candidate (S7.1)."""

from decimal import Decimal
from pathlib import Path

import pytest

from src.db.models.trading_session import TradingSession
from src.mcp_server.errors import ToolFailure
from src.mcp_server.paper.commands import (
    MAX_NAME,
    check_name,
    default_name,
    live_bar_type,
    param_value,
    render_commands,
)

pytestmark = pytest.mark.unit

RUN = "c3c3edbe-0000-4000-8000-000000000001"


def _render(**overrides):
    fields = dict(
        repo_root=Path("/Users/me/dev/Trading-ntrader"),
        name="crsi-qqq-v1-paper",
        strategy="connors_rsi_mean_rev",
        bar_type="QQQ.NASDAQ-1-DAY-LAST-EXTERNAL",
        params={"rsi_period": 2, "trade_size": "1000", "buy_threshold": 10.0, "flag": True},
        compare_to=RUN,
    )
    return render_commands(**{**fields, **overrides})


def test_the_commands_check_create_start_and_watch_in_order():
    commands, warnings = _render()
    assert [c["step"] for c in commands] == ["cd", "check", "create", "start", "status"]
    assert commands[0]["command"] == "cd /Users/me/dev/Trading-ntrader"
    cli = "uv run python -m src.cli.main live"
    check = commands[1]["command"]
    assert check == f"{cli} check --bar-type QQQ.NASDAQ-1-DAY-LAST-EXTERNAL --observe-seconds 0"
    create = commands[2]["command"]
    assert create.startswith(f"{cli} create --name crsi-qqq-v1-paper --strategy ")
    assert "--bar-type QQQ.NASDAQ-1-DAY-LAST-EXTERNAL" in create
    assert create.endswith(f"--compare-to {RUN}")
    for token in ("rsi_period=2", "trade_size=1000", "buy_threshold=10.0", "flag=true"):
        assert f"--param {token}" in create
    assert commands[3]["command"] == f"{cli} start crsi-qqq-v1-paper"
    assert commands[4]["command"] == f"{cli} status crsi-qqq-v1-paper"
    assert warnings == []


def test_every_argument_is_shell_quoted():
    commands, _ = _render(name="my run", params={"label": "a b;rm"})
    create = commands[2]["command"]
    assert "--name 'my run'" in create
    assert "--param 'label=a b;rm'" in create
    assert commands[3]["command"].endswith("start 'my run'")


def test_param_values_render_as_the_cli_parses_them():
    assert param_value(True) == "true" and param_value(False) == "false"
    assert param_value(Decimal("1000.50")) == "1000.50"
    assert param_value(3) == "3" and param_value(0.25) == "0.25"
    assert param_value(None) is None
    with pytest.raises(ToolFailure) as caught:
        param_value([1, 2])
    assert caught.value.code == "param_not_expressible"


def test_a_none_param_is_left_out_with_a_warning():
    commands, warnings = _render(params={"rsi_period": 2, "stop_loss": None})
    assert "stop_loss" not in commands[2]["command"]
    assert warnings and "stop_loss" in warnings[0]


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
def test_a_blank_or_long_name_is_refused(name):
    with pytest.raises(ToolFailure) as caught:
        check_name(name, taken=set())
    assert caught.value.code == "invalid_session_name"


def test_a_taken_name_is_refused_and_a_default_name_avoids_it():
    with pytest.raises(ToolFailure) as caught:
        check_name("crsi-qqq-v1-paper", taken={"crsi-qqq-v1-paper"})
    assert caught.value.code == "session_name_taken"
    assert default_name("crsi-qqq", 1, taken=set()) == "crsi-qqq-v1-paper"
    taken = {"crsi-qqq-v1-paper", "crsi-qqq-v1-paper-2"}
    assert default_name("crsi-qqq", 1, taken=taken) == "crsi-qqq-v1-paper-3"


def test_the_live_bar_type_is_the_instrument_and_the_runs_bar_spec_from_external_data():
    assert live_bar_type("QQQ.NASDAQ", "1-DAY-LAST") == "QQQ.NASDAQ-1-DAY-LAST-EXTERNAL"
    assert live_bar_type("SPY.ARCA", "5-MINUTE") == "SPY.ARCA-5-MINUTE-LAST-EXTERNAL"


def test_the_name_limit_is_the_session_table_column():
    assert TradingSession.__table__.c.name.type.length == MAX_NAME
