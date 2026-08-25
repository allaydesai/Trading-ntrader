"""``ntrader live status``/``live list`` — read state from the database alone.

Split out of ``live.py`` purely for CLAUDE.md's 500-line file limit
(``live_start.py``'s split precedent) — ``live.py`` is over cap and may only
gain the ``live.add_command(...)`` registration lines for these two commands.

Owns: resolving a session, reading its counts and health, and rendering both
the human and ``--json`` output. Every decision lives in
``src/core/live_session_health.py`` — this module parses options, opens one
short-lived database session, calls the core builder, prints, and exits.

**Pure reader.** No ``TradingNode``, no import of ``live_session_runner``, no
transition and no port write — `status`/`list` work identically whether the
runner process exists or not (AC #1, #9). A stale-looking `running` row is
never reclaimed here; that is `start`'s job alone (AR33).
"""

import json
import sys
from datetime import datetime, timezone
from typing import NoReturn

import click
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from src.core.live_check import EXIT_CODES, classify_failure, failure_message
from src.core.live_session_health import (
    StatusReport,
    build_status_report,
    render_status,
    status_json_payload,
)
from src.core.live_trader_id import derive_trader_id
from src.db.repositories.trading_session_repository_sync import SyncTradingSessionRepository
from src.db.session_sync import get_sync_session
from src.services.session_service import DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS, SessionService

# Wide, like `strategy.py`'s console: `list`'s table has seven columns,
# including a 36-character UUID, and Rich's non-tty fallback width (80) would
# ellipsize every column into unreadable noise otherwise.
console = Console(width=200)

# No module-level logger, deliberately: Judgment call #7 — `status`/`list` are
# read-only and their console output *is* the product, so they emit no structlog
# events at all. A bound-but-unused logger in a module whose `--json` contract
# depends on stdout carrying nothing but JSON is an invitation to break it.


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_report(identifier: str) -> StatusReport:
    """Resolve, read counts, and build the report — one short-lived session.

    Mirrors ``live_start.claim_session``'s shape, minus the transition: a
    plain lookup takes no lock and performs no write (AR33).
    """
    with get_sync_session() as db_session:
        repository = SyncTradingSessionRepository(db_session)
        service = SessionService(repository)
        trading_session = service.resolve(identifier)
        counts = repository.trade_counts_by_session([trading_session.id])
        closed_count, open_count = counts.get(trading_session.id, (0, 0))
        return build_status_report(
            session_id=trading_session.session_id,
            name=trading_session.name,
            status=trading_session.status,
            last_heartbeat_at=trading_session.last_heartbeat_at,
            last_bar_at=trading_session.last_bar_at,
            last_started_at=trading_session.last_started_at,
            last_stopped_at=trading_session.last_stopped_at,
            sealed_at=trading_session.sealed_at,
            runtime_flags=trading_session.runtime_flags,
            closed_trade_count=closed_count,
            open_positions=open_count,
            now=_utc_now(),
            heartbeat_stale_after_seconds=DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS,
            trader_id=derive_trader_id(trading_session.session_id),
        )


def _load_reports() -> list[StatusReport]:
    """Every session, in ``find_all()``'s pinned order, one grouped count query.

    Deliberately passes no ``trader_id``: the table is already seven columns
    wide and an eighth would cost the ``Name`` column the width it needs.
    ``live status <session>`` is where a trader id is answered, and a test
    pins this asymmetry so it stays a policy rather than reverting to what it
    was — an omitted keyword argument in one of two near-identical loaders.
    """
    with get_sync_session() as db_session:
        repository = SyncTradingSessionRepository(db_session)
        rows = repository.find_all()
        counts = repository.trade_counts_by_session()
        now = _utc_now()
        return [
            build_status_report(
                session_id=row.session_id,
                name=row.name,
                status=row.status,
                last_heartbeat_at=row.last_heartbeat_at,
                last_bar_at=row.last_bar_at,
                last_started_at=row.last_started_at,
                last_stopped_at=row.last_stopped_at,
                sealed_at=row.sealed_at,
                runtime_flags=row.runtime_flags,
                closed_trade_count=counts.get(row.id, (0, 0))[0],
                open_positions=counts.get(row.id, (0, 0))[1],
                now=now,
                heartbeat_stale_after_seconds=DEFAULT_HEARTBEAT_STALE_AFTER_SECONDS,
            )
            for row in rows
        ]


def _exit_on_failure(exc: BaseException, *, command: str, as_json: bool) -> NoReturn:
    """AR28's table, reused rather than duplicated (Dev Notes).

    ``command`` names the command that actually failed. It is a parameter
    rather than a constant because both commands share this path, and a
    hardcoded prefix told an operator whose ``live list`` failed that
    ``live status`` had — an error attributed to a command they never ran.

    Under ``--json`` the message goes to **stderr**. AR29 makes stdout the
    machine-readable channel, and a human-readable failure line on stdout is
    exactly what breaks ``ntrader live status <name> --json | jq`` on the
    paths a monitoring script most needs to handle. The human path is
    unchanged — stdout, as before.
    """
    stream = Console(width=200, file=sys.stderr) if as_json else console
    stream.print(f"live {command} failed: {failure_message(exc)}", markup=False, highlight=False)
    raise SystemExit(EXIT_CODES[classify_failure(exc)]) from exc


@click.command("status")
@click.argument("session")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON instead of the human-readable summary.",
)
def status(session: str, as_json: bool) -> None:
    """Report a session's state, trade counts and health — from the database alone.

    Answers entirely from ``trading_sessions``: it never constructs a
    ``TradingNode`` and works identically whether the runner process for
    ``session`` is alive or not (AC #1). ``<session>`` may be a name or a
    ``session_id`` — the same ``SessionService.resolve()`` precedence every
    command shares (name-or-UUID, UUID checked first).

    \b
    Exit codes:
      0  the report was produced (including a `stale`/`degraded` session —
         reporting bad news is a successful report)
      1  no session matches the given identifier, or a database failure
      2  usage error
      4  a database failure whose exception name collides with the socket
         errors AR28 reserves 4 for (e.g. SQLAlchemy pool exhaustion, whose
         `TimeoutError` shares a name with the builtin). Documented rather
         than remapped: 4 is inside AR28's table, and AC #10 forbids adding
         exception names to `live_check`'s outcome map. Do not read 4 from
         these two commands as proof the broker is unreachable — neither
         opens a broker socket.
    """
    try:
        report = _load_report(session)
    except (Exception, KeyboardInterrupt) as exc:
        _exit_on_failure(exc, command="status", as_json=as_json)

    if as_json:
        console.print(
            json.dumps(status_json_payload(report)), markup=False, highlight=False, soft_wrap=True
        )
    else:
        console.print(render_status(report), markup=False, highlight=False)


@click.command("list")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit a machine-readable JSON array instead of the human-readable table.",
)
def list_sessions(as_json: bool) -> None:
    """List every session with its state, health and trade counts.

    Renders in ``find_all()``'s pinned order — newest first — so the same
    command run twice lists identically (AC #6).

    \b
    Exit codes:
      0  the list was produced (an empty list is still a successful report)
      1  a database failure
      2  usage error
      4  a database failure whose exception name collides with AR28's socket
         errors — see `live status --help`. Not a broker diagnosis.
    """
    try:
        reports = _load_reports()
    except (Exception, KeyboardInterrupt) as exc:
        _exit_on_failure(exc, command="list", as_json=as_json)

    if as_json:
        payload = [status_json_payload(report) for report in reports]
        console.print(json.dumps(payload), markup=False, highlight=False, soft_wrap=True)
        return

    if not reports:
        console.print("No sessions found.", markup=False, highlight=False)
        return

    table = Table(title="Paper-Trading Sessions", show_header=True, header_style="bold cyan")
    # `overflow="fold"` wraps rather than crops: `live create` accepts names up
    # to MAX_SESSION_NAME_LENGTH (100) but the other six columns leave `Name`
    # roughly 77 characters, and Rich's default crop silently produces a name
    # that cannot be pasted back into `ntrader live status <name>`.
    table.add_column("Name", style="magenta", overflow="fold")
    table.add_column("Session ID", style="cyan", no_wrap=True)
    table.add_column("State")
    table.add_column("Health")
    table.add_column("Closed Trades", justify="right")
    table.add_column("Open Positions", justify="right")
    table.add_column("Last Activity")
    for report in reports:
        table.add_row(
            escape(report.name),
            report.session_id,
            report.status,
            report.health,
            str(report.closed_trade_count),
            str(report.open_positions),
            report.last_activity_at.isoformat() if report.last_activity_at else "never",
        )
    console.print(table)
