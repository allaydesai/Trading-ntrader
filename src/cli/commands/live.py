"""CLI command group: live paper-trading operations (Story 1.7).

Ships `ntrader live check` — one command that proves the safety gate holds and
the broker connection works, before there is any session or any order to worry
about. Epic 2 adds `create`/`start`/`status`/`list`/`reconcile`/`seal` to this
same group (architecture D8); this story deliberately stubs none of them.

Thin by contract: parse options, hand typed settings to the driver, render the
report, exit on its code. Every decision — the gate, the sequence, the exit-code
table — lives in `src/core/live_check.py` and `src/core/live_check_driver.py`,
so the command surface can change without touching what an operator's scripts
branch on.

**No `--real-money` option exists here, and none may be added.** The two-factor
crossing (`--real-money` *and* `NTRADER_REAL_MONEY_ACCOUNT`) is not something a
connectivity check has any business declaring; with the flag unavailable, an
authorization sitting in the environment refuses at Layer 1 with exit code 3,
which is the correct outcome.
"""

import click
from rich.console import Console

from src.config import get_settings
from src.core.live_check import render_report
from src.core.live_check_driver import run_live_check

console = Console()

#: The instrument a bare `ntrader live check` subscribes to. A module constant
#: rather than a new setting: an env var would ripple into `.env.example`,
#: `README.md` and `docs/setup/IBKR_SETUP.md` for a value `--bar-type` already
#: overrides. Same call Story 1.6 made for its reconnect window.
DEFAULT_BAR_TYPE = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"

#: How long to watch for bars once connected. Over one minute, so a 1-minute bar
#: has time to close inside regular trading hours.
DEFAULT_OBSERVE_SECONDS = 90.0

#: How long both engines have to report connected. Deliberately not
#: `IBKR_CONNECTION_TIMEOUT` (300s), which exists for the historical fetch
#: client: a check exists to answer quickly, and 5 minutes of silence before
#: "unreachable" is not an answer.
DEFAULT_CONNECT_TIMEOUT_SECONDS = 60.0


@click.group("live")
def live() -> None:
    """Live paper-trading commands."""


@live.command("check")
@click.option(
    "--bar-type",
    "bar_types",
    multiple=True,
    default=(DEFAULT_BAR_TYPE,),
    show_default=True,
    help="Bar type to subscribe to; repeat for several.",
)
@click.option(
    "--observe-seconds",
    type=click.FloatRange(min=0),
    default=DEFAULT_OBSERVE_SECONDS,
    show_default=True,
    help="How long to watch for bars once connected (0 subscribes but observes nothing).",
)
@click.option(
    "--connect-timeout",
    type=click.FloatRange(min=0, min_open=True),
    default=DEFAULT_CONNECT_TIMEOUT_SECONDS,
    show_default=True,
    help="How long the broker has to report connected before exit code 4.",
)
@click.option(
    "--require-bars",
    is_flag=True,
    default=False,
    help="Fail (exit 1) if no bar closes during the window. Only meaningful inside RTH.",
)
def check(
    bar_types: tuple[str, ...],
    observe_seconds: float,
    connect_timeout: float,
    require_bars: bool,
) -> None:
    """Prove the safety gate holds and the broker connection works.

    Evaluates the pre-connection gate, connects to the configured IBKR paper
    gateway, verifies the account the gateway actually reports, subscribes to the
    configured instrument, reports the bars it receives, and disconnects.

    \b
    Exit codes:
      0  the check completed
      1  a configuration or runtime failure
      2  usage error
      3  the safety gate refused the connection (scriptably distinct)
      4  the broker was unreachable

    Connection settings come from `IBKRSettings` only — `IBKR_HOST`, `IBKR_PORT`,
    `IBKR_LIVE_CLIENT_ID`, `IBKR_TRADING_MODE`, `TWS_ACCOUNT` — via `.env` or the
    environment. There are deliberately no host/port/account flags.
    """
    report = run_live_check(
        get_settings().ibkr,
        bar_types=list(bar_types),
        observe_seconds=observe_seconds,
        connect_timeout=connect_timeout,
        require_bars=require_bars,
    )
    # `markup=False`: parts of this text arrive from third-party exception
    # strings, and a stray `[...]` would otherwise be eaten as Rich markup —
    # silently dropping the operator's most important line.
    console.print(render_report(report), markup=False, highlight=False)
    raise SystemExit(report.exit_code)
