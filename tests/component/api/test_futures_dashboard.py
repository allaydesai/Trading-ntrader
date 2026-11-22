"""Futures dashboard (M4, PO ruling Allay 2026-11-22): detail, trades table, run list.

- Equity pages render **byte for byte** as they did before the futures edits. The golden
  files under ``tests/fixtures/equity_dashboard_golden/`` were captured from the templates
  as they were before M4 touched them
  (``python -m tests.component.api.test_futures_dashboard --capture-golden``, run once on the
  pre-M4 tree). Don't recapture to make a failure pass: an equity diff is the bug.
  **One recorded exception:** ``trades_table.html`` was recaptured for the trades-table
  controls fix (Allay approved 2026-11-22: trades-table controls fix + equity golden
  update). Its controls now request this partial instead of the JSON API, and it renders
  its own ``#trades-table-content`` root. The other goldens are unchanged.
- Futures pages show contracts, multiplier, notional, margin used, return on margin (the
  headline), return on the underlying, points and percentages, and the ratio-adjustment note.
- The trades table is built in-process (no HTTP hop to a hardcoded port), and its page-size,
  sort and pagination controls request the partial and swap it in place.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_backtest_query_service, get_db
from src.api.models.backtest_list import FilteredBacktestListPage, to_list_item
from src.api.models.filter_models import FilterState
from src.api.web import app
from src.db.models.trade import Trade

pytestmark = pytest.mark.component

GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "equity_dashboard_golden"
T0 = datetime(2024, 3, 1, 14, tzinfo=timezone.utc)
ES_TERMS = {
    "ES.CME": {
        "multiplier": "50",
        "margin_initial_usd": "27500",
        "margin_maintenance_usd": "25000",
        "commission_per_contract_usd": "2.25",
        "margin_as_of": "2026-11-21",
    }
}


def _trade(n, iid, side, qty, entry, exit_, pnl, pct, start_h, end_h):
    return Trade(
        id=n,
        backtest_run_id=7,
        instrument_id=iid,
        trade_id=f"T-{n}",
        venue_order_id=f"V-{n}",
        client_order_id=f"C-{n}",
        order_side=side,
        quantity=Decimal(qty),
        entry_price=Decimal(entry),
        exit_price=Decimal(exit_),
        commission_amount=Decimal("4.50"),
        commission_currency="USD",
        fees_amount=Decimal("0"),
        profit_loss=Decimal(pnl),
        profit_pct=Decimal(pct),
        holding_period_seconds=(end_h - start_h) * 3600,
        entry_timestamp=T0 + timedelta(hours=start_h),
        exit_timestamp=T0 + timedelta(hours=end_h),
        created_at=T0,
    )


EQUITY_TRADES = [
    _trade(1, "AAPL.NASDAQ", "BUY", "600", "170.00", "172.00", "1195.50", "1.1765", 0, 5),
    _trade(2, "AAPL.NASDAQ", "BUY", "600", "173.00", "171.50", "-904.50", "-0.8671", 6, 9),
]
ES_TRADES = [
    _trade(1, "ES.CME", "BUY", "2", "5000.00", "5020.00", "1991.00", "0.4000", 0, 5),
    _trade(2, "ES.CME", "SELL", "1", "5030.00", "5040.00", "-504.50", "0.1988", 6, 9),
]


def _run(symbol, snapshot, trades):
    metrics = SimpleNamespace(
        total_return=Decimal("0.0149"),
        final_balance=Decimal("101486.50"),
        cagr=Decimal("0.06"),
        sharpe_ratio=Decimal("1.1"),
        sortino_ratio=Decimal("1.2"),
        max_drawdown=Decimal("-0.02"),
        volatility=Decimal("0.1"),
        total_trades=2,
        winning_trades=1,
        losing_trades=1,
        win_rate=Decimal("0.5"),
        profit_factor=Decimal("3.9"),
        expectancy=Decimal("743.25"),
        avg_win=Decimal("1991"),
        avg_loss=Decimal("-504.5"),
    )
    return SimpleNamespace(
        id=7,
        run_id=UUID("12345678-1234-5678-1234-567812345678"),
        strategy_name="SMA Crossover",
        strategy_type="sma_crossover",
        instrument_symbol=symbol,
        start_date=datetime(2024, 3, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 3, 31, tzinfo=timezone.utc),
        initial_capital=Decimal("100000"),
        data_source="catalog:firstrate-futures" if snapshot.get("futures_contracts") else "catalog",
        execution_status="success",
        execution_duration_seconds=Decimal("1.5"),
        error_message=None,
        created_at=datetime(2024, 4, 1, 9, 30, tzinfo=timezone.utc),
        reproduced_from_run_id=None,
        data_quality_flag=None,
        config_snapshot=snapshot,
        metrics=metrics,
        trades=list(trades),
    )


def equity_run():
    return _run(
        "AAPL", {"strategy_type": "sma_crossover", "config": {"fast_period": 10}}, EQUITY_TRADES
    )


def futures_run():
    snapshot = {
        "strategy_type": "sma_crossover",
        "config": {"contracts": 1},
        "futures_contracts": ES_TERMS,
        "profit_pct_basis": "underlying",
    }
    return _run("ES", snapshot, ES_TRADES)


def _scalars(rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    result.scalar.return_value = len(rows)
    return result


def _client(run):
    service = AsyncMock()
    service.get_backtest_by_id = AsyncMock(return_value=run)
    service.get_backtest_by_internal_id = AsyncMock(return_value=run)
    service.get_filtered_backtest_list_page = AsyncMock(
        return_value=FilteredBacktestListPage(
            backtests=[to_list_item(run)],
            page=1,
            page_size=20,
            total_count=1,
            filter_state=FilterState(),
            available_strategies=["SMA Crossover"],
            available_instruments=[run.instrument_symbol],
        )
    )
    db = MagicMock()
    db.execute = AsyncMock(side_effect=lambda *_a, **_k: _scalars(run.trades))

    async def override_db():
        yield db

    app.dependency_overrides[get_backtest_query_service] = lambda: service
    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


PAGES = {
    "detail.html": "/backtests/12345678-1234-5678-1234-567812345678",
    "trades_table.html": "/backtests/7/trades-table?page=1&page_size=20",
    "list_fragment.html": "/backtests/fragment",
    "export.csv": "/api/backtests/7/export?format=csv",
    "export.json": "/api/backtests/7/export?format=json",
}


def render(run) -> dict[str, str]:
    """Each page for ``run``, as served."""
    try:
        client = _client(run)
        pages = {}
        for name, url in PAGES.items():
            response = client.get(url)
            assert response.status_code == 200, (name, response.text[:500])
            pages[name] = response.text
        return pages
    finally:
        app.dependency_overrides.clear()


class TestEquityPagesAreUnchanged:
    @pytest.mark.parametrize("name", sorted(PAGES))
    def test_equity_page_is_byte_for_byte_the_pre_m4_render(self, name):
        expected = (GOLDEN / name).read_bytes().decode("utf-8")  # bytes: the CSV is CRLF
        assert render(equity_run())[name] == expected


def _text(html: str) -> str:
    import html as html_lib
    import re

    no_tags = re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", html, flags=re.S))
    return re.sub(r"\s+", " ", html_lib.unescape(no_tags))


class TestFuturesDetail:
    def test_panel_leads_with_return_on_margin(self):
        text = _text(render(futures_run())["detail.html"])
        assert "Return on margin" in text
        assert "+2.70%" in text  # 1,486.50 / 55,000
        assert "Return on underlying" in text
        assert "+0.20%" in text
        assert "Peak margin used $55,000.00" in text
        assert "Max contracts 2" in text
        assert "x50" in text
        assert "Largest notional $500,000.00" in text
        assert "Points per contract (adjusted) +10.00" in text
        assert "ratio-adjusted" in text
        assert text.index("Return on margin") < text.index("Performance Metrics")


class TestFuturesTradesTable:
    def test_contracts_points_and_percentages(self):
        text = _text(render(futures_run())["trades_table.html"])
        assert "Contracts" in text
        assert "Quantity" not in text
        assert "Notional" in text and "$500,000.00" in text
        assert "Margin" in text and "$55,000.00" in text
        assert "Points (adj.)" in text and "+20.00" in text and "-10.00" in text
        assert "+3.62%" in text  # return on margin, trade 1
        assert "-1.83%" in text  # return on margin, trade 2
        assert "+0.40%" in text and "-0.20%" in text  # side-aware underlying
        assert "ratio-adjusted" in text

    def test_sort_and_page_reach_the_in_process_query(self):
        client = _client(futures_run())
        try:
            response = client.get(
                "/backtests/7/trades-table?page=2&page_size=50&sort_by=profit_loss&sort_order=desc"
            )
            assert response.status_code == 200
            assert "sort_by=profit_loss" in response.text
        finally:
            app.dependency_overrides.clear()

    def test_unknown_sort_falls_back_to_entry_time(self):
        client = _client(futures_run())
        try:
            response = client.get("/backtests/7/trades-table?sort_by=pnl&sort_order=sideways")
            assert response.status_code == 200
        finally:
            app.dependency_overrides.clear()

    def test_missing_backtest_is_404(self):
        client = _client(futures_run())
        try:
            service = app.dependency_overrides[get_backtest_query_service]()
            service.get_backtest_by_internal_id.return_value = None
            assert client.get("/backtests/999/trades-table").status_code == 404
        finally:
            app.dependency_overrides.clear()


class TestFuturesExport:
    def test_csv_adds_futures_columns(self):
        import csv
        import io

        rows = list(csv.DictReader(io.StringIO(render(futures_run())["export.csv"])))
        assert rows[0]["contracts"] == "2"
        assert rows[0]["notional_usd"] == "500000.00"
        assert rows[0]["margin_used_usd"] == "55000.00"
        assert rows[1]["points_adjusted"] == "-10.00"
        assert rows[1]["return_on_margin_pct"] == "-1.8345"

    def test_json_adds_futures_fields(self):
        import json

        rows = json.loads(render(futures_run())["export.json"])
        assert rows[0]["return_on_underlying_pct"] == "0.4000"
        assert rows[1]["return_on_underlying_pct"] == "-0.1988"


class TestFuturesExportEdgeCases:
    """CodeRabbit round 2 on PR #53."""

    def test_a_flat_trade_keeps_its_zero_returns(self):
        import csv
        import io

        run = futures_run()
        run.trades = [_trade(3, "ES.CME", "BUY", "1", "5000.00", "5000.09", "0", "0.0018", 0, 1)]
        (row,) = list(csv.DictReader(io.StringIO(render(run)["export.csv"])))
        assert row["profit_loss"] == ""  # the equity column's existing convention
        assert row["return_on_margin_pct"] == "0.0000"
        assert row["points_adjusted"] == "0.09"

    def test_a_futures_run_without_trades_keeps_the_futures_header(self):
        run = futures_run()
        run.trades = []
        header = render(run)["export.csv"].splitlines()[0]
        assert header.endswith("return_on_underlying_pct,return_on_margin_pct")
        assert header.startswith("instrument_id,trade_id")


class TestFuturesList:
    def test_list_row_shows_asset_class_and_return_on_margin(self):
        text = _text(render(futures_run())["list_fragment.html"])
        assert "FUT" in text
        assert "RoM +2.70%" in text
        assert "acct +1.49%" in text


def _capture_golden() -> None:
    """Record the equity pages as the pre-M4 tree serves them (run once, before M4's edits).

    The pre-M4 trades-table route fetched ``http://localhost:8000/api/...`` with httpx; the
    capture answers that call with the in-process API endpoint, so no server is involved.
    """

    import httpx

    from src.api.rest.trades import get_backtest_trades

    run = equity_run()

    class _InProcess:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def get(self, url, params):
            db = MagicMock()
            db.execute = AsyncMock(side_effect=lambda *_a, **_k: _scalars(run.trades))
            service = AsyncMock()
            service.get_backtest_by_internal_id = AsyncMock(return_value=run)
            body = await get_backtest_trades(7, service, db, **params)
            return SimpleNamespace(status_code=200, json=lambda: body.model_dump(mode="json"))

    real = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **k: _InProcess()  # type: ignore[misc]
    try:
        GOLDEN.mkdir(parents=True, exist_ok=True)
        for name, html in render(run).items():
            (GOLDEN / name).write_bytes(html.encode("utf-8"))
            print(f"wrote {GOLDEN / name} ({len(html)} chars)")
    finally:
        httpx.AsyncClient = real


if __name__ == "__main__":
    if "--capture-golden" in sys.argv:
        _capture_golden()


def _controls(html: str) -> dict[str, str]:
    """Every hx-get in the trades fragment, keyed by the control's label."""
    import html as html_lib
    import re

    found = {}
    for tag, label in re.findall(
        r"(<(?:button|select)\b[^>]*>)(.*?)</(?:button|select)>", html, re.S
    ):
        if (url := re.search(r'hx-get="([^"]+)"', tag)) is None:
            continue
        key = html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", label)).strip())
        key = "page-size" if tag.startswith("<select") else re.sub(r" [↑↓]$", "", key)
        found[key] = html_lib.unescape(url.group(1))
        assert 'hx-target="#trades-table-content"' in tag, tag
        assert 'hx-swap="outerHTML"' in tag, tag
    return found


def _paged_client(run, total: int):
    """A client whose count query reports ``total`` trades (so there are several pages)."""
    client = _client(run)
    db = MagicMock()
    count = MagicMock()
    count.scalar.return_value = total
    executed: list[str] = []

    async def execute(statement, *args, **kwargs):
        executed.append(str(statement))
        return count if "count(" in str(statement) else _scalars(run.trades)

    db.execute = execute

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    return client, executed


@pytest.mark.parametrize("make_run", [equity_run, futures_run], ids=["equity", "futures"])
class TestTradesTableControls:
    """Page size, sort and pagination request the HTML partial and swap it in place."""

    def _fragment(self, make_run, url="/backtests/7/trades-table?page=2&page_size=20"):
        client, executed = _paged_client(make_run(), total=65)
        response = client.get(url)
        assert response.status_code == 200
        return response.text, client, executed

    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_the_fragment_root_is_the_swap_target(self, make_run):
        html, *_ = self._fragment(make_run)
        assert html.lstrip().startswith("<!-- Trades Table Partial")
        assert html.count('id="trades-table-content"') == 1

    def test_no_control_requests_the_json_api(self, make_run):
        html, *_ = self._fragment(make_run)
        assert "/api/backtests/" not in html
        assert all(u.startswith("/backtests/7/trades-table?") for u in _controls(html).values())

    def test_page_size_keeps_the_sort_and_restarts_at_page_one(self, make_run):
        html, *_ = self._fragment(
            make_run,
            "/backtests/7/trades-table?page=2&page_size=20&sort_by=profit_loss&sort_order=desc",
        )
        url = _controls(html)["page-size"]
        assert "page=1" in url and "sort_by=profit_loss" in url and "sort_order=desc" in url
        assert 'name="page_size"' in html

    @pytest.mark.parametrize(
        ("label", "field"),
        [
            ("Entry Date", "entry_timestamp"),
            ("Exit Date", "exit_timestamp"),
            ("P&L", "profit_loss"),
        ],
    )
    def test_each_sort_header_toggles_its_field_and_resets_the_page(self, make_run, label, field):
        html, client, executed = self._fragment(make_run)
        url = next(u for k, u in _controls(html).items() if k.startswith(label))
        assert f"sort_by={field}" in url and "page=1" in url
        expected = "desc" if field == "entry_timestamp" else "asc"  # entry asc is current
        assert f"sort_order={expected}" in url
        executed.clear()
        assert client.get(url).status_code == 200
        assert f"ORDER BY trades.{field} {expected.upper()}" in executed[-1]

    @pytest.mark.parametrize(
        ("label", "page"), [("« First", 1), ("‹ Prev", 1), ("Next ›", 3), ("Last »", 4)]
    )
    def test_each_page_button_requests_its_page(self, make_run, label, page):
        html, client, executed = self._fragment(make_run)
        url = _controls(html)[label]
        assert f"page={page}&" in url and "page_size=20" in url
        response = client.get(url)
        assert response.status_code == 200
        assert f"Page {page} of 4" in response.text
