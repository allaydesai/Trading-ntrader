"""``ntrader live seal <session>`` — end a stopped session as one run record (Story 5.3).

Split out of ``live.py``, which may only gain the ``live.add_command(...)``
registration line (``live_status.py``'s and ``live_reconcile.py``'s precedent).

Owns: the option surface, the composition, the report and the exit code. The
composition root is here — the only place that holds the database session, the
settings and the clock at once: it opens one async session, hands the seal the
session's own engine-cache view as its open-position reader (PO ruling R2a),
**commits only when the seal returns**, and always disposes the async engine.
Every decision lives in ``src/services/session_seal_service.py``.

**It reaches no broker.** The one external read is the session's Redis view,
load-only (Story 4.6); nothing here, or below, sends an order or touches a
position (AR31). There is no real-money flag and none may be added.
"""

import asyncio
import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout, suppress
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import NoReturn
from uuid import UUID

import click
import structlog
from rich.console import Console

from src.config import get_settings
from src.core.live_check import EXIT_CODES, EXIT_ERROR, classify_failure, failure_message
from src.core.live_session_view import read_session_view
from src.db.exceptions import DuplicateRecordError, SealRefusedError, ValidationError
from src.db.session import dispose_all_connections, get_session
from src.models.reconciliation import SessionView
from src.services.session_seal_service import (
    LINKED_BACKTEST_WARNING_EVENT,
    SealReport,
    render_seal_report,
    require_sealable_capital,
    seal_json_payload,
    seal_session,
)

console = Console(width=200)
logger = structlog.get_logger(__name__)


def _parse_capital(ctx: click.Context, param: click.Parameter, value: str) -> Decimal:
    """A decimal amount the record can carry, kept exactly as typed (PO ruling R1a).

    Judged once, up front, before any connection: Story 5.1's positive,
    whole-cent rule and Nautilus's largest amount, through the seal service's
    one ``require_sealable_capital`` — so a bad value is a usage error (exit 2).
    """
    try:
        capital = Decimal(value)
    except InvalidOperation:
        raise click.BadParameter(f"{value!r} is not a decimal amount.") from None
    try:
        require_sealable_capital(capital)  # NaN/Infinity included: 5.1's rule refuses them
    except SealRefusedError as exc:
        raise click.BadParameter(str(exc)) from None
    return capital


def read_open_positions(session_id: UUID) -> SessionView:
    """The session's own view of what it holds — its engine cache, read-only.

    The configured account picks the positions to count. Without one, the
    view cannot say the session is flat, so the seal refuses (fail closed).
    """
    settings = get_settings()
    account = settings.ibkr.tws_account
    if not account or not account.strip():
        raise SealRefusedError(
            "TWS_ACCOUNT is not set, so the session's engine cache cannot be read for the "
            "account it traded and the session is not known to be flat."
        )
    return read_session_view(session_id, account, settings.redis)


@contextmanager
def _stdout_reserved_for_json(as_json: bool) -> Iterator[None]:
    """Under ``--json``, stdout carries the document and nothing else (AR29).

    The seal's dependencies log at INFO, and the console log handler writes to
    stdout. For the seal's duration, any non-file stream handler on the root
    logger — and anything printed — goes to stderr instead, then is restored.
    """
    if not as_json:
        yield
        return
    moved = [
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
        and handler.stream is not sys.stderr
    ]
    previous = [handler.setStream(sys.stderr) for handler in moved]
    try:
        with redirect_stdout(sys.stderr):
            yield
    finally:
        for handler, stream in zip(moved, previous):
            if stream is not None:
                handler.setStream(stream)


@dataclass
class _Outcome:
    """What the seal got to. ``report`` is set the moment the commit lands: from
    then on the seal is permanent, and nothing after it may report a failure."""

    report: SealReport | None = None
    cleanup_problem: str | None = None


async def _seal(identifier: str, starting_capital: Decimal, outcome: _Outcome) -> None:
    """One async session: committed only if the seal returns, rolled back
    explicitly otherwise; the engine is always disposed."""
    try:
        async with get_session() as db:
            try:
                report = await seal_session(
                    db,
                    identifier,
                    starting_capital=starting_capital,
                    read_open_positions=read_open_positions,
                )
                await db.commit()
            except BaseException:
                with suppress(Exception):  # the original failure is the one to report
                    await db.rollback()
                raise
        outcome.report = report
        _log_sealed(report)
    finally:
        try:
            await dispose_all_connections()
        except Exception as exc:  # never mask the seal's own outcome with cleanup's
            outcome.cleanup_problem = type(exc).__name__


def _log_sealed(report: SealReport) -> None:
    """After the commit only — a seal that rolls back leaves no record of either."""
    logger.info(
        "session.sealed",
        session_id=str(report.session_id),
        run_id=str(report.run_id),
        closed_trade_count=report.closed_trade_count,
    )
    if report.linked_backtest_warning is not None:
        logger.warning(
            LINKED_BACKTEST_WARNING_EVENT,
            session_id=str(report.session_id),
            linked_backtest_run_id=str(report.linked_backtest_run_id),
            warning=report.linked_backtest_warning,
        )


def _print(line: str, *, to_stderr: bool = False) -> None:
    """Plain text, never re-shaped: no markup, no emoji codes (``:100:``), no wrap."""
    stream = Console(width=200, file=sys.stderr) if to_stderr else console
    stream.print(line, markup=False, highlight=False, emoji=False, soft_wrap=True)


def _exit_with(message: str, code: int, *, as_json: bool) -> NoReturn:
    """Under ``--json`` a failure line goes to stderr (``live_status``'s rule)."""
    _print(f"live seal failed: {message}", to_stderr=as_json)
    raise SystemExit(code)


def _print_report(outcome: _Outcome, *, as_json: bool) -> None:
    report = outcome.report
    if report is None:  # pragma: no cover - every path without a report exits first
        raise RuntimeError("no seal report to print")
    if outcome.cleanup_problem is not None:
        _print(
            f"note: the seal committed and the record stands; the cleanup after it failed "
            f"({outcome.cleanup_problem}).",
            to_stderr=True,
        )
    if as_json:
        _print(json.dumps(seal_json_payload(report)))
        return
    for line in render_seal_report(report):
        _print(line)


@click.command("seal")
@click.argument("session")
@click.option(
    "--starting-capital",
    required=True,
    callback=_parse_capital,
    help="The paper account's starting balance, stored exactly as given (e.g. 100000.00).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit the seal report as machine-readable JSON.",
)
def seal(session: str, starting_capital: Decimal, as_json: bool) -> None:
    """Seal a stopped session into one run record beside its backtests.

    Computes the session's metrics from its trades through the backtest
    results path, writes one run record (run_type 'paper') and its metrics,
    links the session's trades to it, and moves the session to 'sealed' — all
    in one transaction. Sealing is permanent and is never done to a running
    session. A session whose engine cache still holds a position, or cannot
    be read, is refused: a seal never exits, flattens or modifies a position,
    and never contacts the broker. An order still resting at IBKR is not
    checked — cancel it in TWS before sealing.

    \b
    Exit codes:
      0  the session was sealed (a cleanup failure after the commit is
         noted beside the report, never reported as a failed seal)
      1  refused (not stopped, open positions, an unreadable engine cache,
         a trade, spec or metric the record cannot carry) or a database
         failure
      2  usage error, including a starting capital the record cannot carry
      4  a database failure whose exception name collides with the socket
         errors AR28 reserves 4 for (e.g. a refused Postgres connection).
         Not a broker diagnosis — seal never opens a broker socket.
    """
    outcome = _Outcome()
    try:
        with _stdout_reserved_for_json(as_json):
            asyncio.run(_seal(session, starting_capital, outcome))
    except (ValidationError, DuplicateRecordError) as exc:
        # AR12: the backtest path's own failures, unchanged. Their text is this
        # codebase's own, so it is shown — with the type, as raised.
        _exit_with(f"{type(exc).__name__}: {exc}", EXIT_ERROR, as_json=as_json)
    except (Exception, KeyboardInterrupt) as exc:
        if outcome.report is None:
            _exit_with(failure_message(exc), EXIT_CODES[classify_failure(exc)], as_json=as_json)
        # Committed: the record stands, whatever interrupted the cleanup.
        outcome.cleanup_problem = type(exc).__name__
    _print_report(outcome, as_json=as_json)
