"""``ntrader live reconcile <session>`` — positions and cash against IBKR, on demand (Story 4.6).

Split out of ``live.py``, which is over its size cap and may only gain the
``live.add_command(...)`` registration line (``live_status.py``'s precedent).

Owns: resolving the session (read-only — ``SessionService.resolve`` and
nothing else), composing the check (settings, the comparison port), printing
the report, and exiting. Every decision lives below it:
``src/core/live_reconcile.py`` runs the check,
``src/services/reconciliation_service.py`` compares and renders.

**It changes nothing, anywhere (FR35).** No transition, no record write, no
Redis write, no order: a discrepancy is reported and the command exits ``5``.
IBKR is the authority; at the session's next start its reconciliation
hydrates from the broker. There is no real-money flag and none may be added:
the check runs behind the same two-layer gate as ``live check``.
"""

from typing import NoReturn

import click
from rich.console import Console

from src.config import get_settings
from src.core.live_check import EXIT_CODES, classify_failure, failure_message
from src.core.live_reconcile import (
    ReconcileTarget,
    exit_code_for,
    reconcile_settings,
    run_reconcile,
)
from src.db.repositories.trading_session_repository_sync import SyncTradingSessionRepository
from src.db.session_sync import get_sync_session
from src.services.reconciliation_service import compare, render_report
from src.services.session_service import SessionService

console = Console(width=200)


def _resolve(identifier: str) -> ReconcileTarget:
    """Name or UUID → plain values, in one short-lived, read-only DB session."""
    with get_sync_session() as db_session:
        row = SessionService(SyncTradingSessionRepository(db_session)).resolve(identifier)
        return ReconcileTarget(name=row.name, session_id=row.session_id, status=str(row.status))


def _print(line: str) -> None:
    console.print(line, markup=False, highlight=False)


def _exit_on_failure(exc: BaseException) -> NoReturn:
    """AR28's table, shared with ``check``/``start``/``status``: nothing was compared."""
    _print(f"live reconcile failed: {failure_message(exc)}")
    raise SystemExit(EXIT_CODES[classify_failure(exc)]) from exc


@click.command("reconcile")
@click.argument("session")
def reconcile(session: str) -> None:
    """Compare a session's positions and cash against IBKR, and report every difference.

    Connects read-only on IBKR_LIVE_CLIENT_ID + 1, so a running session is never
    evicted. Reads the broker's positions and cash, reads the session's own view
    from its engine cache (Redis), prints each line, and disconnects. Changes
    nothing. Exit 0: everything matches exactly. Exit 5: a discrepancy was found.
    """
    try:
        target = _resolve(session)
        settings = get_settings()
        client_id = reconcile_settings(settings.ibkr).ibkr_live_client_id
        _print(
            f"reconcile: session {target.name} (status={target.status}) against IBKR on "
            f"client_id={client_id}"
        )
        if target.status == "running":
            _print(
                "note: the session is running — a fill landing during the check can show as a "
                "transient position discrepancy, and the session's cash is IBKR's last "
                "account-summary push, which can lag the broker by about 3 minutes after any "
                "fill; re-run once that has passed to confirm a difference"
            )
        report = run_reconcile(settings.ibkr, settings.redis, target, compare=compare)
    except (Exception, KeyboardInterrupt) as exc:
        _exit_on_failure(exc)
    for line in render_report(report):
        _print(line)
    raise SystemExit(exit_code_for(report))
