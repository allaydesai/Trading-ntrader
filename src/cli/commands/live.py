"""CLI command group: live paper-trading operations (Stories 1.7, 2.2, 2.5).

Ships `ntrader live check`, `create` and `start`. Epic 2 adds
`stop`/`status`/`list` and Epic 4/5 add `reconcile`/`seal` to this same group
(architecture D8); this module deliberately stubs none of them.

Thin by contract: parse options, hand typed settings to the driver, the
repository or the runner, render the result, exit on its code. Every decision
for `check` lives in `src/core/live_check.py` and `src/core/live_check_driver.py`;
for `create`, the spec is built and validated entirely by `src.models.session`
and persisted through `SyncTradingSessionRepository`; for `start`, the sequence
is `src/core/live_session_runner.py`'s. This module owns none of that logic —
only the option surface, the composition, and the exit-code mapping.

**`start` is the composition root.** It is the only place that holds both a
database session and a `Settings`, and it is where the two are narrowed: the
runner gets `settings.ibkr` and an already-built `CacheConfig`, never the whole
object, because AR38 keeps SQLAlchemy out of the runner and the runner has no
business reaching `settings.redis`.

**No `--real-money` option exists here, and none may be added.** The two-factor
crossing (`--real-money` *and* `NTRADER_REAL_MONEY_ACCOUNT`) is not something a
connectivity check has any business declaring; with the flag unavailable, an
authorization sitting in the environment refuses at Layer 1 with exit code 3,
which is the correct outcome.
"""

import asyncio
from datetime import datetime
from typing import NoReturn, Optional
from uuid import UUID

import click
import structlog
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape
from sqlalchemy.exc import SQLAlchemyError

from src.config import get_settings
from src.core.live_cache import build_cache_config
from src.core.live_check import (
    EXIT_CODES,
    EXIT_ERROR,
    classify_failure,
    failure_message,
    render_report,
)
from src.core.live_check_driver import run_live_check
from src.core.live_session_record import SessionReclaimedError
from src.core.live_session_runner import (
    DEFAULT_SESSION_CONNECT_TIMEOUT_SECONDS,
    LiveSessionRunner,
)
from src.core.strategy_registry import StrategyRegistry
from src.db.exceptions import DatabaseConnectionError, DuplicateRecordError
from src.db.repositories.backtest_repository_sync import SyncBacktestRepository
from src.db.repositories.trading_session_repository_sync import SyncTradingSessionRepository
from src.db.session_sync import get_sync_session
from src.models.session import SessionSpec, SessionStatus, StrategySpec
from src.services.session_record import SqlSessionRecord
from src.services.session_service import SessionService

console = Console()
logger = structlog.get_logger(__name__)

#: The instrument a bare `ntrader live check` subscribes to. A module constant
#: rather than a new setting: an env var would ripple into `.env.example`,
#: `README.md` and `docs/setup/IBKR_SETUP.md` for a value `--bar-type` already
#: overrides. Same call Story 1.6 made for its reconnect window.
DEFAULT_BAR_TYPE = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"

#: How long to watch for bars once connected. Over one minute, so a 1-minute bar
#: has time to close inside regular trading hours.
DEFAULT_OBSERVE_SECONDS = 90.0

#: Longest `--name` `live create` accepts. Must equal `trading_sessions.name`'s
#: `String(100)`; `test_live_cli.py` asserts the two agree so they cannot drift.
MAX_SESSION_NAME_LENGTH = 100

#: How long both engines have to report connected. Deliberately not
#: `IBKR_CONNECTION_TIMEOUT` (300s), which exists for the historical fetch
#: client: a check exists to answer quickly, and 5 minutes of silence before
#: "unreachable" is not an answer.
DEFAULT_CONNECT_TIMEOUT_SECONDS = 60.0


@click.group("live")
def live() -> None:
    """Live paper-trading commands."""


def _validate_strategy(ctx: click.Context, param: click.Parameter, value: str) -> str:
    """Resolve `--strategy` through the registry; an unknown name is a usage error.

    Mirrors `backtest.py`'s `validate_strategy` callback shape, so an unknown
    strategy fails at exit code 2 (Click's own usage-error code) rather than
    surfacing later as a plain ValueError from `StrategySpec.from_overrides`.
    """
    StrategyRegistry.discover()
    if not StrategyRegistry.exists(value):
        available = StrategyRegistry.get_names()
        raise click.BadParameter(
            f"Unknown strategy '{value}'. Available strategies: {', '.join(available)}"
        )
    return value


def _validate_name(ctx: click.Context, param: click.Parameter, value: str) -> str:
    """Reject a blank or over-long `--name` as a usage error, before any DB work.

    `required=True` checks presence only. Two failures it does not catch:

    - A blank or whitespace-only name (a scripted `--name "$VAR"` that expands
      empty) would persist as the session's permanent handle, which every later
      `live start/status/seal` has to name on a command line.
    - A name longer than the column would reach Postgres and come back as
      `value too long for type character varying(100)` at exit 1, though this
      command's exit-code table reserves 1 for state conflicts and 2 for misuse.

    The name is deliberately *not* trimmed: a spec frozen for the life of a
    forward test should store exactly what the operator typed.
    """
    if not value.strip():
        raise click.BadParameter("--name must not be blank or whitespace-only.")
    if len(value) > MAX_SESSION_NAME_LENGTH:
        raise click.BadParameter(
            f"--name is {len(value)} characters; the maximum is {MAX_SESSION_NAME_LENGTH}."
        )
    return value


def _parse_param(
    ctx: click.Context, param: click.Parameter, values: tuple[str, ...]
) -> dict[str, str]:
    """Parse repeated `--param key=value` options into a dict of raw strings.

    A new convention for this repo — no existing command has `--param`.
    `str.partition("=")`, not `split("=")`: a value may legitimately contain
    `=`. Values stay strings; the strategy's own parameter model coerces them.
    """
    overrides: dict[str, str] = {}
    for raw in values:
        key, separator, value = raw.partition("=")
        if not separator:
            raise click.BadParameter(f"--param must be key=value, got {raw!r}")
        # A blank key is accepted by partition() and would render as nothing in
        # the unknown-key message below — the operator would be told a
        # parameter is unknown without being told which.
        if not key.strip():
            raise click.BadParameter(f"--param key must not be blank, got {raw!r}")
        if key in overrides:
            raise click.BadParameter(f"--param key {key!r} was given more than once")
        overrides[key] = value
    return overrides


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


@live.command("create")
@click.option(
    "--name",
    required=True,
    callback=_validate_name,
    help="Unique handle for this session.",
)
@click.option(
    "--strategy",
    required=True,
    callback=_validate_strategy,
    help="Strategy to run, by its StrategyRegistry name.",
)
@click.option(
    "--bar-type",
    "bar_types",
    multiple=True,
    required=True,
    help="Bar type this strategy subscribes to; repeat for several.",
)
@click.option(
    "--param",
    "overrides",
    multiple=True,
    callback=_parse_param,
    help="Strategy parameter override as key=value; repeat for several.",
)
@click.option(
    "--compare-to",
    type=click.UUID,
    default=None,
    help="run_id of the backtest this session is intended to be compared against.",
)
def create(
    name: str,
    strategy: str,
    bar_types: tuple[str, ...],
    overrides: dict[str, str],
    compare_to: Optional[UUID],
) -> None:
    """Define a session once; its specification is frozen for its whole life.

    A typo in `--strategy`, `--bar-type` or `--param` at a later `start` can
    never silently change what a multi-week forward test is running — the spec
    is validated and persisted here, in full, and nothing after this command
    ever updates it.

    \b
    Exit codes:
      0  the session was created
      1  a state conflict or database failure (duplicate name, unknown
         --compare-to, database unavailable)
      2  usage error (unknown strategy, malformed --param, unknown parameter
         key, an unusable bar type, or an invalid parameter value)
    """
    definition = StrategyRegistry.get(strategy)  # already validated by the callback
    if definition.param_model is not None:
        valid = set(definition.param_model.model_fields)
        unknown = sorted(set(overrides) - valid)
        if unknown:
            # Keys are rendered with !r so a whitespace-padded key cannot look
            # identical to a valid one in the error message.
            raise click.UsageError(
                f"Unknown parameter(s) for {definition.name!r}: "
                f"{', '.join(repr(key) for key in unknown)}. "
                f"Valid parameters: {', '.join(sorted(valid))}."
            )

    try:
        strategy_spec = StrategySpec.from_overrides(
            strategy_id=strategy,
            overrides=overrides,
            settings=get_settings(),
            bar_types=bar_types,
        )
        spec = SessionSpec(strategies=(strategy_spec,))
    except ValidationError as e:
        # pydantic prefixes ValueError-raising validator messages with
        # "Value error, " — strip that noise (precedent: _backtest_helpers.py).
        messages = "; ".join(err.get("msg", "") for err in e.errors())
        messages = messages.replace("Value error, ", "")
        raise click.UsageError(messages or str(e)) from e
    except ValueError as e:
        # from_overrides runs build_strategy_params before pydantic, so
        # resolution/parameter failures surface as a plain ValueError.
        raise click.UsageError(str(e)) from e

    try:
        with get_sync_session() as session:
            if compare_to is not None:
                backtest_run = SyncBacktestRepository(session).find_by_run_id(compare_to)
                if backtest_run is None:
                    console.print(f"[red]No backtest found with run_id {compare_to}[/red]")
                    raise SystemExit(EXIT_ERROR)

            try:
                trading_session = SyncTradingSessionRepository(session).create(
                    name=name,
                    spec=spec.to_stored(),
                    linked_backtest_run_id=compare_to,
                )
            except DuplicateRecordError as e:
                console.print(f"[red]{escape(str(e))}[/red]")
                raise SystemExit(EXIT_ERROR) from e
    except (RuntimeError, DatabaseConnectionError, SQLAlchemyError) as e:
        console.print(f"[red]Database error: {escape(str(e))}[/red]")
        raise SystemExit(EXIT_ERROR) from e

    logger.info("session.created", session_id=str(trading_session.session_id), name=name)
    console.print(
        f"Session created: [bold]{escape(name)}[/bold] (session_id={trading_session.session_id})"
    )


def _claim_session(identifier: str) -> tuple[UUID, dict, datetime]:
    """Resolve the identifier and move the session to ``running``, atomically.

    Everything happens inside **one** short-lived ``get_sync_session()`` block
    that closes before the runner exists. Story 2.3's forward constraint is
    explicit about why: the ``-> running`` transition takes an exclusive row
    lock (it is the reclaim-or-refuse decision), and holding that lock for the
    life of a 6.5-hour session would block every ``live status``.

    Returns:
        The session's UUID, its stored spec payload, and the instant this
        transition stamped into ``last_started_at`` — the value the runner's
        record port is bound to, and the one the mid-run reclaim guard reads.

    Raises:
        RecordNotFoundError: No session matches ``identifier``.
        InvalidSessionTransition: The session is already running with a fresh
            heartbeat, or is sealed.
    """
    with get_sync_session() as db_session:
        service = SessionService(SyncTradingSessionRepository(db_session))
        trading_session = service.resolve(identifier)
        started = service.transition(trading_session.session_id, to=SessionStatus.RUNNING)
        return started.session_id, started.spec, started.last_started_at


def _exit_with(exc: BaseException) -> NoReturn:
    """Render the failure and exit on AR28's code for it.

    Reuses ``live_check``'s table rather than inventing a second one: Story 1.7
    recorded that *"a CLI that invents an exit code outside its own documented
    table is worse than one that reports a generic failure"*. ``markup=False``
    because parts of this text can arrive from third-party exception strings,
    and a stray ``[...]`` would otherwise be eaten as Rich markup — silently
    dropping the operator's most important line.

    **Never retries.** ``InvalidSessionTransition`` inherits
    ``BacktestStorageError``, and an ``except BacktestStorageError: retry()``
    would spin until the incumbent's heartbeat went stale and then reclaim a
    live session — two processes on one broker account, which is the
    catastrophic failure NFR6 exists to prevent.
    """
    console.print(f"live start failed: {failure_message(exc)}", markup=False, highlight=False)
    raise SystemExit(EXIT_CODES[classify_failure(exc)])


def _release_quietly(record: SqlSessionRecord) -> None:
    """Put the row back to ``stopped`` after a failure the runner never saw.

    The runner's own ``finally`` does this for anything raised inside
    ``run()``. This covers the window AC #10 names explicitly — between the
    ``-> running`` transition and the runner existing — where a raise would
    otherwise leave the row ``running`` and the session unstartable for the
    full 90-second staleness threshold.

    Guarded, because the failure already in flight is the one the operator
    needs; a second failure here must not replace it.
    """
    try:
        record.mark_stopped()
    except SessionReclaimedError:
        # The stop-path ownership guard refused (review fix, 2026-08-21):
        # another process took the session; its row is not ours to release.
        logger.error(
            "session.reclaimed_by_another_process",
            detail="release skipped; the row belongs to another process now",
        )
    except Exception as exc:  # noqa: BLE001 - must never replace the primary failure
        logger.error("session.release_failed", error_type=type(exc).__name__)


@live.command("start")
@click.argument("session")
@click.option(
    "--connect-timeout",
    type=click.FloatRange(min=0, min_open=True),
    default=DEFAULT_SESSION_CONNECT_TIMEOUT_SECONDS,
    show_default=True,
    help="How long the session has to connect and start trading before exit code 4.",
)
def start(session: str, connect_timeout: float) -> None:
    """Run a session in the foreground until it stops.

    Loads the session's frozen specification by name or id and starts it — no
    option here re-specifies a strategy, a bar type or a parameter, because a
    typo at start time must never silently change what a multi-week forward
    test is running (FR16, FR14).

    \b
    Startup runs in this order, each phase logging `phase=<name> status=...`:
      gate:static -> node:build -> node:connect -> gate:account
      -> reconcile -> warmup -> subscribe -> trading
    `reconcile` and `warmup` are no-op placeholders until Epic 4.

    \b
    Exit codes:
      0  the session ran and stopped cleanly
      1  a configuration, state or database failure
      2  usage error
      3  the safety gate refused the connection (scriptably distinct)
      4  the broker was unreachable, or the trader never started

    Connection settings come from `IBKRSettings` and the engine cache from
    `RedisSettings` — via `.env` or the environment. There are deliberately no
    host/port/account flags.
    """
    settings = get_settings()
    try:
        session_id, spec_payload, started_at = _claim_session(session)
    except (RuntimeError, DatabaseConnectionError, SQLAlchemyError) as exc:
        # Postgres-layer failures carry this codebase's own actionable text
        # ("Database not configured…", the connection detail) that the AR28
        # renderer would withhold as third-party — mirror `create` instead
        # (review fix, 2026-08-21).
        console.print(f"live start failed: {exc}", markup=False, highlight=False)
        raise SystemExit(EXIT_ERROR) from exc
    except (Exception, KeyboardInterrupt, asyncio.CancelledError) as exc:
        _exit_with(exc)

    # Everything after the `→ running` transition is guarded — including the
    # record's own construction (review fix, 2026-08-21): reading the spec
    # back and building the cache config can both fail, and a failure anywhere
    # in this window must still put the row back to `stopped` (AC #10). The
    # excepts rebuild the release adapter because its constructor is pure
    # attribute assignment and cannot itself be mid-failure.
    try:
        record = SqlSessionRecord(session_id, started_at=started_at)
        runner = LiveSessionRunner(
            settings.ibkr,
            session_id=session_id,
            spec=SessionSpec.from_stored(spec_payload),
            record=record,
            started_at=started_at,
            cache=build_cache_config(settings.redis),
            connect_timeout=connect_timeout,
        )
    except ValidationError as exc:
        # The stored spec no longer materialises — e.g. a strategy id that is
        # no longer registered. The messages are pydantic's, naming the field
        # and the registered strategies; render them as `create` does rather
        # than withholding a bare type name (review fix, 2026-08-21).
        messages = "; ".join(err.get("msg", "") for err in exc.errors())
        messages = messages.replace("Value error, ", "") or str(exc)
        _release_quietly(SqlSessionRecord(session_id, started_at=started_at))
        console.print(f"live start failed: {messages}", markup=False, highlight=False)
        raise SystemExit(EXIT_ERROR) from exc
    except (Exception, KeyboardInterrupt, asyncio.CancelledError) as exc:
        _release_quietly(SqlSessionRecord(session_id, started_at=started_at))
        _exit_with(exc)

    try:
        runner.run()
    except (Exception, KeyboardInterrupt, asyncio.CancelledError) as exc:
        # No `_release_quietly` here: the runner's own `finally` has already
        # marked the row stopped, and a second `transition(to=STOPPED)` would
        # raise `InvalidSessionTransition` from inside the error handler.
        _exit_with(exc)

    console.print(f"Session stopped: [bold]{escape(session)}[/bold]")
