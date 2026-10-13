"""Story 5.6 — a sealed paper record in the existing CLI views, against Postgres.

The 2–10 run comparison view is ``ntrader backtest compare`` — there is no web
comparison route (``src/cli/commands/compare.py``, frozen by AR44). The CLI's
own list and detail (``ntrader history``, ``ntrader backtest show``) read the
same rows. Each command runs **unmodified** through Click, its
``get_sync_session`` pointed at the test schema the record was sealed into, its
module-level Rich console swapped for a wide, colourless one so the table reads
the same on every terminal.

"Same metric names, formatting, and units" (D-C): both columns share one list of
row labels, and every value is compared with a string computed by hand from the
seeded trades and the backtest's stated result — never with the command's own
formatter.
"""

import io
import re
from uuid import UUID

import pytest
from click.testing import CliRunner
from rich.console import Console
from sqlalchemy.ext.asyncio import AsyncSession

from src.cli.commands import compare, history, show
from src.db.repositories.backtest_repository import BacktestRepository
from src.db.repositories.backtest_repository_sync import SyncBacktestRepository
from src.services.backtest_query import BacktestQueryService
from tests.integration.db._sealed_record import (
    SOURCE,
    SealedPair,
    schema_of,
    seal_beside_backtest,
    short,
    sync_session_on,
)

pytestmark = pytest.mark.integration

#: ``compare.py``'s row labels, in its order — written out, not imported.
COMPARE_ROWS = [
    "Strategy",
    "Symbol",
    "Date",
    "Total Return",
    "Final Balance",
    "Sharpe Ratio",
    "Max Drawdown",
    "Win Rate",
    "Total Trades",
]
_CELL_BORDER = re.compile(r"[│┃]")


@pytest.fixture
async def pair(db_session: AsyncSession) -> SealedPair:
    return await seal_beside_backtest(db_session)


#: Session time zones the comparison is read under (code review P1): UTC, and
#: two far enough either side that at any hour one of them is on another date.
TIME_ZONES = ("UTC", "Pacific/Kiritimati", "Pacific/Pago_Pago")


async def _run_cli(
    db: AsyncSession, monkeypatch, module, command, args: list[str], time_zone: str | None = None
) -> str:
    """Run ``command`` unmodified on the test schema; return what it printed."""
    schema = await schema_of(db)
    out = io.StringIO()
    monkeypatch.setattr(module, "get_sync_session", lambda: sync_session_on(schema, time_zone))
    monkeypatch.setattr(module, "console", Console(file=out, width=250, color_system=None))
    result = CliRunner().invoke(command, args)
    assert result.exit_code == 0, (result.output, result.exception)
    return out.getvalue()


def _table(printed: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """The comparison table's column headers and its rows, in printed order —
    a list, so a repeated label is seen rather than collapsed (code review P11)."""
    lines = [line for line in printed.splitlines() if _CELL_BORDER.search(line)]
    # ``compare`` prints an error and still exits 0 (code review P12): show it.
    assert lines, f"no comparison table was printed:\n{printed}"
    split = [[cell.strip() for cell in _CELL_BORDER.split(line)[1:-1]] for line in lines]
    header, body = split[0], split[1:]
    return header, [(row[0], row[1:]) for row in body]


async def _as_the_cli_reads_them(
    db: AsyncSession, pair: SealedPair, time_zone: str
) -> tuple[dict[UUID, str], str]:
    """Each run's creation date and the paper Sharpe, read the way ``compare``
    reads them — pg8000, in the session's zone (code review P1: asyncpg returns
    UTC, so its date is a different day for part of every day). These are the
    two values not computed by hand: the row is created at seal time, and Sharpe
    is the extractor's daily-resampled ratio."""
    schema = await schema_of(db)
    with sync_session_on(schema, time_zone) as session:
        repository = SyncBacktestRepository(session)
        runs = {run_id: repository.find_by_run_id(run_id) for run_id in pair.run_ids}
    dates = {}
    for run_id, run in runs.items():
        assert run is not None and run.metrics is not None
        dates[run_id] = run.created_at.strftime("%Y-%m-%d")
    sharpe = runs[pair.paper_run_id].metrics.sharpe_ratio  # type: ignore[union-attr]
    assert sharpe is not None
    return dates, f"{sharpe:.2f}"


class TestTheComparisonView:
    @pytest.mark.acceptance_criterion(
        "5.6c",
        "the sealed session and its linked backtest render side by side in the existing 2-10 "
        "run comparison view with the same metric names, formatting, and units",
        SOURCE,
    )
    @pytest.mark.parametrize("time_zone", TIME_ZONES)
    async def test_the_paper_record_and_its_backtest_render_side_by_side(
        self, db_session, pair, monkeypatch, time_zone
    ):
        dates, sharpe = await _as_the_cli_reads_them(db_session, pair, time_zone)
        # The two ids as the operator has them: read back from the session row.
        args = [str(run_id) for run_id in pair.run_ids]

        printed = await _run_cli(
            db_session, monkeypatch, compare, compare.compare_backtests, args, time_zone
        )

        header, rows = _table(printed)
        assert [label for label, _ in rows] == COMPARE_ROWS
        paper_col = header.index(f"{short(pair.paper_run_id)}...") - 1
        backtest_col = header.index(f"{short(pair.backtest_run_id)}...") - 1
        assert {label: cells[paper_col] for label, cells in rows} == {
            "Strategy": "Sma Crossover",
            "Symbol": "AAPL",
            "Date": dates[pair.paper_run_id],
            "Total Return": "0.80%",
            "Final Balance": "$100,800.00",
            "Sharpe Ratio": sharpe,
            "Max Drawdown": "-6.88%",
            "Win Rate": "50.0%",
            "Total Trades": "4",
        }
        assert {label: cells[backtest_col] for label, cells in rows} == {
            "Strategy": "Sma Crossover",
            "Symbol": "AAPL",
            "Date": dates[pair.backtest_run_id],
            "Total Return": "25.00%",
            "Final Balance": "$125,000.00",
            "Sharpe Ratio": "1.85",
            "Max Drawdown": "-12.00%",
            "Win Rate": "66.7%",
            "Total Trades": "3",
        }
        assert "Best Performer by Sharpe Ratio" in printed

    async def test_the_service_twin_returns_both_with_their_metrics(self, db_session, pair):
        service = BacktestQueryService(BacktestRepository(db_session))

        runs = await service.compare_backtests([pair.paper_run_id, pair.backtest_run_id])

        assert {run.run_id: run.run_type for run in runs} == {
            pair.paper_run_id: "paper",
            pair.backtest_run_id: "backtest",
        }
        assert all(run.metrics is not None for run in runs)


class TestTheCliListAndDetail:
    async def test_history_lists_the_paper_record_beside_the_backtest(
        self, db_session, pair, monkeypatch
    ):
        printed = await _run_cli(
            db_session, monkeypatch, history, history.list_backtest_history, []
        )

        assert str(pair.paper_run_id) in printed
        assert str(pair.backtest_run_id) in printed

    async def test_show_prints_the_snapshot_with_its_conditions(
        self, db_session, pair, monkeypatch
    ):
        printed = await _run_cli(
            db_session,
            monkeypatch,
            show,
            show.show_backtest_details,
            [str(pair.paper_run_id)],
        )

        assert '"session_conditions"' in printed
        assert '"price_basis": "ibkr_unadjusted_smart"' in printed
        assert "ibkr_live" in printed
        # The row and its value, not just the label (code review P15): 4 closed trades.
        assert re.search(r"Total Trades\W+4\b", printed), printed
