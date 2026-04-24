"""Component tests for backtest detail page — "Back to Explorer" link (Story 3.2)."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_backtest_query_service
from src.api.web import app


def _make_backtest(**overrides):
    """Build a MagicMock BacktestRun that satisfies `to_detail_view` + resolver."""
    backtest = MagicMock()
    backtest.id = 1
    backtest.run_id = uuid4()
    backtest.strategy_name = "SMA Crossover"
    backtest.strategy_type = "trend_following"
    backtest.instrument_symbol = "SPY"
    backtest.start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
    backtest.end_date = datetime(2024, 12, 31, tzinfo=timezone.utc)
    backtest.initial_capital = Decimal("100000.00")
    backtest.data_source = "catalog:firstrate-research"
    backtest.execution_status = "success"
    backtest.execution_duration_seconds = Decimal("45.5")
    backtest.error_message = None
    backtest.config_snapshot = {
        "catalog_name": "firstrate-research",
        "bar_type": "1-DAY-LAST",
        "symbol": "SPY",
    }
    backtest.created_at = datetime.now(timezone.utc)
    backtest.updated_at = datetime.now(timezone.utc)

    metrics = MagicMock()
    metrics.total_return = Decimal("0.05")
    metrics.final_balance = Decimal("105000")
    metrics.cagr = Decimal("0.05")
    metrics.sharpe_ratio = Decimal("1.0")
    metrics.sortino_ratio = Decimal("1.0")
    metrics.max_drawdown = Decimal("-0.05")
    metrics.volatility = Decimal("0.1")
    metrics.total_trades = 10
    metrics.winning_trades = 6
    metrics.losing_trades = 4
    metrics.win_rate = Decimal("0.6")
    metrics.profit_factor = Decimal("1.5")
    metrics.expectancy = Decimal("100")
    metrics.avg_win = Decimal("500")
    metrics.avg_loss = Decimal("-200")
    backtest.metrics = metrics

    for k, v in overrides.items():
        setattr(backtest, k, v)
    return backtest


@pytest.fixture
def client_with_backtest():
    backtest = _make_backtest()
    svc = AsyncMock()
    svc.get_backtest_by_id = AsyncMock(return_value=backtest)
    app.dependency_overrides[get_backtest_query_service] = lambda: svc
    try:
        yield TestClient(app), backtest
    finally:
        app.dependency_overrides.pop(get_backtest_query_service, None)


@pytest.mark.component
class TestBackToExplorerLink:
    """Story 3.2 — `Back to Explorer` secondary action on the detail page."""

    def test_link_rendered_with_valid_explorer_return(self, client_with_backtest):
        client, backtest = client_with_backtest
        response = client.get(
            f"/backtests/{backtest.run_id}"
            "?explorer_return=%2Fexplorer%3Fcatalog%3Dfoo%26search%3DSPY"
        )
        assert response.status_code == 200
        html = response.text
        assert "Back to Explorer" in html
        # The decoded `/explorer?catalog=foo&search=SPY` is injected into the href
        # (Jinja HTML-escapes `&` → `&amp;` inside attribute values).
        assert (
            'href="/explorer?catalog=foo&amp;search=SPY"' in html
            or 'href="/explorer?catalog=foo&search=SPY"' in html
        )

    @pytest.mark.parametrize(
        "bad",
        [
            "https://evil.example.com/phish",
            "//evil.example.com/path",
            "javascript:alert(1)",
            "data:text/html,<x>",
            "../backtests",
        ],
    )
    def test_link_rejects_open_redirect(self, client_with_backtest, bad):
        client, backtest = client_with_backtest
        response = client.get(f"/backtests/{backtest.run_id}?explorer_return={bad}")
        assert response.status_code == 200
        html = response.text
        assert "evil.example.com" not in html
        assert "javascript:alert" not in html
        assert "Back to Explorer" in html

    def test_link_fallback_uses_catalog_from_data_source(self, client_with_backtest):
        client, backtest = client_with_backtest
        response = client.get(f"/backtests/{backtest.run_id}")
        assert response.status_code == 200
        html = response.text
        # No explorer_return → fallback from `data_source=catalog:firstrate-research` + bar_type.
        assert "catalog=firstrate-research" in html
        assert "ticker=SPY" in html
        assert "tf=D" in html  # 1-DAY → D

    def test_link_fallback_without_catalog_name(self):
        """Non-named-catalog run → fallback is the bare `/explorer`."""
        backtest = _make_backtest(
            data_source="ibkr",
            config_snapshot={"bar_type": "1-DAY-LAST"},
        )
        svc = AsyncMock()
        svc.get_backtest_by_id = AsyncMock(return_value=backtest)
        app.dependency_overrides[get_backtest_query_service] = lambda: svc
        try:
            client = TestClient(app)
            response = client.get(f"/backtests/{backtest.run_id}")
            assert response.status_code == 200
            html = response.text
            # The link exists and points to /explorer.
            assert 'href="/explorer"' in html
        finally:
            app.dependency_overrides.pop(get_backtest_query_service, None)

    def test_link_has_focus_ring(self, client_with_backtest):
        client, backtest = client_with_backtest
        response = client.get(f"/backtests/{backtest.run_id}")
        # Story 2-4 focus-ring pattern
        assert "focus:ring-offset-slate-950" in response.text
