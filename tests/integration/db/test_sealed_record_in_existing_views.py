"""Story 5.6 — a sealed paper record in the existing web views, against Postgres.

The record is sealed through the real seal path beside the backtest its session
was linked to (``_sealed_record``), then read through the **unmodified** web
app in-process: the list page and its HTMX fragment, the dashboard, the detail
page, the HTMX trades table and every JSON endpoint the detail page's charts
call. Nothing under ``src/api/**`` or ``templates/**`` is touched (AR44); the
proof is that a record sealed by Epic 5 renders through them as a backtest does.

"Renders correctly" (D-F): HTTP 200, the expected labels present, values in the
view's own formats — compared with strings computed by hand (D-C), never with
the view's own formatter — and stored text escaped. The backtest beside it is
held to the same assertions, so the two records are read one way.
"""

import html
import re
from collections import Counter
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.web import app
from tests.integration.db._sealed_record import (
    INSTRUMENT,
    OPEN_LEGS,
    PAPER_LEGS,
    SOURCE,
    SealedPair,
    asgi_client,
    seal_beside_backtest,
    seal_paper,
    short,
    visible_text,
)

pytestmark = pytest.mark.integration

#: ``build_metrics_panel``'s names, in its order — written out, not imported.
METRIC_NAMES = [
    "Total Return",
    "CAGR",
    "Final Balance",
    "Sharpe Ratio",
    "Sortino Ratio",
    "Max Drawdown",
    "Volatility",
    "Total Trades",
    "Win Rate",
    "Profit Factor",
]
#: Text a view must never show where a backtest shows a number.
NOT_A_NUMBER = re.compile(r"\b(None|nan|NaN|inf|Infinity)\b")
#: AR7's seven keys (Story 5.4 AC #1) — written out, not imported (code review P10).
AR7_KEYS = (
    "starting_capital",
    "rth_only",
    "price_basis",
    "market_data_tier",
    "closed_trade_count",
    "open_positions_at_seal",
    "schema_version",
)
#: The JSON export's row keys (``src/api/rest/trades.py``), written out (code review P7).
EXPORT_KEYS = {
    "instrument_id",
    "trade_id",
    "order_side",
    "entry_timestamp",
    "entry_price",
    "exit_timestamp",
    "exit_price",
    "quantity",
    "profit_loss",
    "profit_pct",
    "commission_amount",
    "holding_period_seconds",
}


@pytest.fixture
async def pair(db_session: AsyncSession) -> SealedPair:
    return await seal_beside_backtest(db_session)


async def _run(db: AsyncSession, pk: int):
    """The columns the views read, straight from Postgres."""
    db.expire_all()
    return (
        await db.execute(
            text(
                "SELECT r.start_date, r.end_date, r.config_snapshot, m.sharpe_ratio "
                "FROM backtest_runs r JOIN performance_metrics m ON m.backtest_run_id = r.id "
                "WHERE r.id = :pk"
            ),
            {"pk": pk},
        )
    ).one()


def _list_rows(page: str) -> dict[str, list[str]]:
    """The results table's rows, keyed by run id, each as its cells' visible text."""
    rows = {}
    for row in re.findall(r'<tr class="table-row-hover.*?</tr>', page, flags=re.DOTALL):
        run_id = re.search(r"window\.location='/backtests/([0-9a-f-]{36})'", row)
        assert run_id is not None, row
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)
        rows[run_id.group(1)] = [visible_text(cell) for cell in cells]
    return rows


def _metrics(page: str) -> list[tuple[str, str]]:
    """The metrics panel's (name, formatted value) pairs, in page order."""
    pairs = re.findall(
        r'cursor-help">([^<]+)</button>\s*<span class="[^"]*">\s*([^<]*?)\s*</span>',
        page,
    )
    return [(html.unescape(name), html.unescape(value)) for name, value in pairs]


def _conditions_cell(page: str) -> str:
    """The raw ``<dd>`` of the config panel's one "Session Conditions" row — that
    row alone, never "CLI Command to Replicate", which repeats the same text
    (code review P2)."""
    cells = re.findall(
        r"<dt[^>]*>\s*Session Conditions\s*</dt>\s*<dd[^>]*>(.*?)</dd>", page, flags=re.DOTALL
    )
    assert len(cells) == 1, f"expected one Session Conditions row, found {len(cells)}"
    return cells[0]


def _assert_rendered_escaped(cell: str, conditions: dict) -> str:
    """R1a: the row holds the stored document's text exactly, HTML-escaped."""
    assert html.unescape(cell) == str(conditions)
    assert "'" not in cell and "&#39;" in cell  # the quotes reach the page only as entities
    return html.unescape(cell)


def _trade_table_rows(fragment: str) -> Counter:
    """The trades table's rows as cell tuples, without the row number and the two
    timestamps — a multiset, so two legs can never match one row (code review P6).
    Cells: instrument, side, quantity, entry, exit, P&L, P&L %, holding period."""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", fragment, flags=re.DOTALL)
    cells = [re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL) for row in rows]
    return Counter(tuple(visible_text(c) for c in row[3:]) for row in cells if row)


def _money(value: Decimal) -> str:
    """``trades_table.html``'s money cell: ``+`` only when positive, then ``$x,xxx.xx``."""
    return f"{'+' if value > 0 else ''}${value:,.2f}"


class TestTheListShowsThePaperRecordBesideTheBacktest:
    @pytest.mark.acceptance_criterion(
        "5.6a",
        "the paper record appears alongside backtests in the existing results list, rendering "
        "correctly with no template, route, or query change",
        SOURCE,
    )
    async def test_both_records_are_rows_of_the_list_and_its_fragment(self, db_session, pair):
        paper = await _run(db_session, pair.paper_pk)

        async with asgi_client(db_session) as client:
            responses = [await client.get("/backtests"), await client.get("/backtests/fragment")]

        for response in responses:
            assert response.status_code == 200
            rows = _list_rows(response.text)
            assert rows[str(pair.paper_run_id)][:8] == [
                short(pair.paper_run_id),
                "Sma Crossover",
                "AAPL",
                f"{paper.start_date.date()} to {paper.end_date.date()}",
                "+0.80%",
                f"{paper.sharpe_ratio:.2f}",
                "-6.88%",
                "Success",
            ]
            assert rows[str(pair.backtest_run_id)][:8] == [
                short(pair.backtest_run_id),
                "Sma Crossover",
                "AAPL",
                "2016-01-04 to 2024-01-02",
                "+25.00%",
                "1.85",
                "-12.00%",
                "Success",
            ]
            assert not NOT_A_NUMBER.search(" ".join(rows[str(pair.paper_run_id)]))

    @pytest.mark.parametrize(
        "query", ["strategy=Sma+Crossover", "instrument=AAPL", "status=success"]
    )
    async def test_the_existing_filters_keep_it_beside_the_backtest(self, db_session, pair, query):
        async with asgi_client(db_session) as client:
            response = await client.get(f"/backtests/fragment?{query}")

        assert response.status_code == 200
        assert {str(pair.paper_run_id), str(pair.backtest_run_id)} <= set(_list_rows(response.text))

    @pytest.mark.parametrize("query", ["instrument=MSFT", "status=failed"])
    async def test_a_filter_that_matches_neither_drops_both(self, db_session, pair, query):
        """The negative control (code review P8): a filter the route ignored would
        keep both rows and still pass the test above."""
        async with asgi_client(db_session) as client:
            response = await client.get(f"/backtests/fragment?{query}")

        assert response.status_code == 200
        assert not {str(pair.paper_run_id), str(pair.backtest_run_id)} & set(
            _list_rows(response.text)
        )

    async def test_the_dashboard_lists_it_in_recent_activity(self, db_session, pair):
        async with asgi_client(db_session) as client:
            response = await client.get("/")

        assert response.status_code == 200
        assert short(pair.paper_run_id) in response.text
        assert short(pair.backtest_run_id) in response.text


class TestTheDetailViewRendersTheRecord:
    @pytest.mark.acceptance_criterion(
        "5.6b",
        "the sealed record's detail view renders its metrics, trades, and config snapshot "
        "including session_conditions",
        SOURCE,
    )
    async def test_the_metrics_render_under_the_backtests_names_and_formats(self, db_session, pair):
        async with asgi_client(db_session) as client:
            paper = await client.get(f"/backtests/{pair.paper_run_id}")
            backtest = await client.get(f"/backtests/{pair.backtest_run_id}")

        assert (paper.status_code, backtest.status_code) == (200, 200)
        paper_metrics, backtest_metrics = dict(_metrics(paper.text)), dict(_metrics(backtest.text))
        assert [name for name, _ in _metrics(paper.text)] == METRIC_NAMES
        assert [name for name, _ in _metrics(backtest.text)] == METRIC_NAMES
        assert {
            name: paper_metrics[name]
            for name in ("Total Return", "Final Balance", "Max Drawdown", "Total Trades")
        } == {
            "Total Return": "0.80%",
            "Final Balance": "$100,800.00",
            "Max Drawdown": "-6.88%",
            "Total Trades": "4",
        }
        # 2 of 4 trades won; gross profit 1,500 over gross loss 700.
        assert (paper_metrics["Win Rate"], paper_metrics["Profit Factor"]) == ("50.00%", "2.1429")
        assert (backtest_metrics["Total Return"], backtest_metrics["Win Rate"]) == (
            "25.00%",
            "66.67%",
        )
        assert not NOT_A_NUMBER.search(" ".join(paper_metrics.values()))
        assert "Trading Summary" in paper.text
        assert "ibkr_live" in visible_text(paper.text)

    @pytest.mark.acceptance_criterion(
        "5.6b",
        "the sealed record's detail view renders its metrics, trades, and config snapshot "
        "including session_conditions",
        SOURCE,
    )
    async def test_the_config_panel_renders_every_stored_condition_escaped(self, db_session, pair):
        """PO ruling R1a: the one long "Session Conditions" row is correct rendering."""
        conditions = (await _run(db_session, pair.paper_pk)).config_snapshot["session_conditions"]

        async with asgi_client(db_session) as client:
            response = await client.get(f"/backtests/{pair.paper_run_id}")

        assert response.status_code == 200
        row = _assert_rendered_escaped(_conditions_cell(response.text), conditions)
        for key in AR7_KEYS:
            assert f"'{key}': {conditions[key]!r}" in row
        assert "'price_basis': 'ibkr_unadjusted_smart'" in row
        assert "'start_date_basis': 'session_created_at'" in row
        assert conditions["closed_trade_count"] == len(PAPER_LEGS)

    def test_the_row_check_reads_the_row_not_the_replicate_command(self):
        """Not vacuous (code review P2): the same text in "CLI Command to Replicate"
        alone, beside an empty row, must not pass for the row."""
        conditions = {"price_basis": "ibkr_unadjusted_smart"}
        escaped = html.escape(str(conditions), quote=True)
        page = (
            '<dt class="x">Session Conditions</dt>\n<dd class="y"></dd>'
            f'<pre id="cli-command">--session-conditions {escaped}</pre>'
        )

        with pytest.raises(AssertionError):
            _assert_rendered_escaped(_conditions_cell(page), conditions)

    async def test_a_record_of_sixty_trades_renders_every_curve_point(self, db_session):
        """R1a's long case: the row is long, and every stored point is in it."""
        legs = tuple(
            ("BUY", "100", "101", "100") if index % 2 else ("BUY", "100", "99.5", "-50")
            for index in range(60)
        )
        session_pk = await seal_paper(db_session, name="paper-sixty", legs=legs)
        run_id, pk = (
            await db_session.execute(
                text(
                    "SELECT r.run_id, r.id FROM backtest_runs r JOIN trading_sessions s "
                    "ON s.sealed_run_id = r.run_id WHERE s.id = :pk"
                ),
                {"pk": session_pk},
            )
        ).one()
        conditions = (await _run(db_session, pk)).config_snapshot["session_conditions"]
        curve = conditions["equity_series"]["curve"]

        async with asgi_client(db_session) as client:
            response = await client.get(f"/backtests/{run_id}")

        assert response.status_code == 200
        # One point per distinct close instant: every leg closes on its own day (P9).
        assert len(curve) == len(legs)
        row = _assert_rendered_escaped(_conditions_cell(response.text), conditions)
        for point in curve:
            assert str(point) in row

    @pytest.mark.acceptance_criterion(
        "5.6b",
        "the sealed record's detail view renders its metrics, trades, and config snapshot "
        "including session_conditions",
        SOURCE,
    )
    async def test_the_trades_table_lists_the_sessions_rows(self, db_session, pair, monkeypatch):
        """The route calls ``localhost:8000`` itself; it is answered in-process."""
        real_client = httpx.AsyncClient

        def in_process(*args, **kwargs):
            return real_client(transport=httpx.ASGITransport(app=app))

        async with asgi_client(db_session) as client:
            monkeypatch.setattr(httpx, "AsyncClient", in_process)
            response = await client.get(f"/backtests/{pair.paper_pk}/trades-table")

        assert response.status_code == 200
        closed = [
            (
                INSTRUMENT,
                side,
                "100.00",
                f"${Decimal(entry):,.2f}",
                f"${Decimal(exit_):,.2f}",
                _money(Decimal(pnl)),
                # The side-unaware formula: (exit − entry) / entry, a SELL included.
                f"{(Decimal(exit_) - Decimal(entry)) / Decimal(entry) * 100:+.2f}%",
                "2h 0m",
            )
            for side, entry, exit_, pnl in PAPER_LEGS
        ]
        # An open row: no exit price, no P&L % and no holding period — "-", "0.00%", "-".
        opened = [
            (INSTRUMENT, "BUY", f"{leg.quantity:,.2f}", "$187.25", "-", "$-1.00", "0.00%", "-")
            for leg in OPEN_LEGS
        ]
        assert _trade_table_rows(response.text) == Counter(closed + opened)

    async def test_every_call_the_page_makes_answers_for_the_paper_record(
        self, db_session, pair, monkeypatch
    ):
        async def no_catalog(backtest):
            raise FileNotFoundError("no catalog in this test")

        monkeypatch.setattr("src.api.rest.timeseries._load_chart_bars", no_catalog)
        pk, run_id = pair.paper_pk, pair.paper_run_id
        paths = [
            f"/api/equity-curve/{pk}",
            f"/api/statistics/{pk}",
            f"/api/drawdown/{pk}",
            f"/api/trades/{run_id}",
            f"/api/backtests/{pk}/trades",
            f"/api/backtests/{pk}/export?format=csv",
            f"/api/backtests/{pk}/export?format=json",
            f"/api/timeseries/run/{run_id}",
            f"/backtests/{run_id}/export",
        ]

        async with asgi_client(db_session) as client:
            responses = {path: await client.get(path) for path in paths}

        assert {path: r.status_code for path, r in responses.items()} == dict.fromkeys(paths, 200)
        curve = responses[f"/api/equity-curve/{pk}"].json()
        assert Decimal(curve["final_balance"]) == Decimal("100800.00")
        # The bar load is forced to fail, so no candles is the route's own degrade
        # path, not a finding about the record (code review P13). What the record
        # decides is the symbol and the bar type the chart asks for: the spec's.
        series = responses[f"/api/timeseries/run/{run_id}"].json()
        assert (series["symbol"], series["timeframe"], series["candles"]) == (
            "AAPL",
            "1-MINUTE-LAST",
            [],
        )


async def _rows_of(db: AsyncSession, where: str, pk: int) -> list:
    """The trade rows Postgres holds for ``where = pk`` — every row, duplicates kept."""
    db.expire_all()
    result = await db.execute(
        text(
            "SELECT trade_id, client_order_id, order_side, entry_price, exit_price, profit_loss "
            f"FROM trades WHERE {where} = :pk"
        ),
        {"pk": pk},
    )
    return list(result)


def _keys(rows: list) -> Counter:
    return Counter((row.trade_id, row.client_order_id) for row in rows)


def _expected_markers(rows: list) -> Counter:
    """``/api/trades``' markers for these rows: an entry per row (P&L 0), an exit
    per closed row, on the opposite side, carrying its P&L."""
    entries = [(r.order_side.lower(), float(r.entry_price), 0.0) for r in rows]
    exits = [
        ("sell" if r.order_side == "BUY" else "buy", float(r.exit_price), float(r.profit_loss))
        for r in rows
        if r.exit_price is not None
    ]
    return Counter(entries + exits)


def _exported(rows: list) -> Counter:
    return Counter(
        (r.trade_id, str(r.entry_price), r.exit_price and str(r.exit_price), str(r.profit_loss))
        for r in rows
    )


class TestTradeQueriesReturnTheSessionsTrades:
    """AR8: after the backfill, every query keyed on ``backtest_run_id`` (D-E) —
    each compared row by row as a multiset (code review P7), the backtest's held
    to the same assertions."""

    @pytest.mark.acceptance_criterion(
        "5.6e",
        "existing trade queries return the session's trades through backtest_run_id exactly as "
        "they do for backtests",
        SOURCE,
    )
    async def test_every_trade_query_returns_the_sessions_rows_as_for_a_backtest(
        self, db_session, pair
    ):
        session_rows = await _rows_of(db_session, "session_id", pair.session_pk)
        # (pk, run_id, closed rows, the trades-based drawdown by hand: the paper
        # account peaks at 101,000 and falls to 100,300; the backtest's at 102,000
        # and falls to 101,800).
        sides = {
            "paper": (pair.paper_pk, pair.paper_run_id, len(PAPER_LEGS), Decimal("700")),
            "backtest": (pair.backtest_pk, pair.backtest_run_id, 3, Decimal("200")),
        }
        assert _keys(session_rows) == _keys(
            await _rows_of(db_session, "backtest_run_id", pair.paper_pk)
        )
        assert len(session_rows) == len(PAPER_LEGS) + len(OPEN_LEGS)

        async with asgi_client(db_session) as client:
            for label, (pk, run_id, closed, drawdown) in sides.items():
                stored = await _rows_of(db_session, "backtest_run_id", pk)
                listed = (await client.get(f"/api/backtests/{pk}/trades?page_size=100")).json()
                exported = (await client.get(f"/api/backtests/{pk}/export?format=json")).json()
                markers = (await client.get(f"/api/trades/{run_id}")).json()
                statistics = (await client.get(f"/api/statistics/{pk}")).json()
                curve = (await client.get(f"/api/equity-curve/{pk}")).json()
                drawdowns = (await client.get(f"/api/drawdown/{pk}")).json()

                listed_keys = Counter(
                    (t["trade_id"], t["client_order_id"]) for t in listed["trades"]
                )
                assert listed_keys == _keys(stored), label
                assert listed["pagination"]["total_items"] == len(stored), label
                # The export is a bare list of rows with exactly these keys.
                assert isinstance(exported, list), label
                assert all(set(row) == EXPORT_KEYS for row in exported), label
                assert Counter(
                    (r["trade_id"], r["entry_price"], r["exit_price"], r["profit_loss"])
                    for r in exported
                ) == _exported(stored), label
                assert Counter(
                    (m["side"], m["price"], m["pnl"]) for m in markers["trades"]
                ) == _expected_markers(stored), label
                assert statistics["total_trades"] == closed, label
                # The starting capital, then one point per closed row.
                assert len(curve["points"]) == closed + 1, label
                assert Decimal(curve["points"][0]["balance"]) == Decimal("100000.00"), label
                worst = drawdowns["max_drawdown"] or drawdowns["current_drawdown"]
                assert Decimal(worst["drawdown_amount"]) == drawdown, label

    async def test_open_rows_come_back_like_a_backtests_open_row(self, db_session, pair):
        async with asgi_client(db_session) as client:
            paper = (await client.get(f"/api/backtests/{pair.paper_pk}/trades")).json()
            backtest = (await client.get(f"/api/backtests/{pair.backtest_pk}/trades")).json()

        def open_rows(listing):
            return [t for t in listing["trades"] if t["exit_timestamp"] is None]

        for row in open_rows(paper) + open_rows(backtest):
            assert (row["exit_price"], row["profit_pct"], row["client_order_id"]) == (
                None,
                None,
                None,
            )
        assert {row["trade_id"] for row in open_rows(paper)} == {
            leg.position_id for leg in OPEN_LEGS
        }
        assert len(open_rows(backtest)) == 1

    async def test_a_profitable_short_reads_negative_profit_pct_on_both_sides(
        self, db_session, pair
    ):
        """Debt item 1, a disposition: the views show the stored sign as stored, the
        same on both sides. The rows here are hand-seeded, so this proves the read
        path only (code review P3). That both production formulas — the backtest's
        ``save_trades_from_positions`` and the recorder's ``aggregate_closed_position``
        — store a negative ``profit_pct`` for a profitable SHORT is pinned at the
        component tier: ``tests/component/services/test_profit_pct_parity.py``."""
        async with asgi_client(db_session) as client:
            listings = [
                (await client.get(f"/api/backtests/{pk}/trades")).json()
                for pk in (pair.paper_pk, pair.backtest_pk)
            ]

        for listing in listings:
            shorts = [
                t
                for t in listing["trades"]
                if t["order_side"] == "SELL" and t["exit_price"] and Decimal(t["profit_loss"]) > 0
            ]
            assert shorts
            assert all(Decimal(t["profit_pct"]) < 0 for t in shorts)
