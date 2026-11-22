"""CLI renderers for futures runs (M4): ``show``, ``report`` and ``history``.

A futures run gets its block (return on margin first); an equity run renders exactly as
before (no futures text, and the same report keys).
"""

import csv
import io
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from rich.console import Console

from src.cli.commands import history, report, show
from tests.unit.services.futures.test_returns import ES_SNAPSHOT, TRADE_A, TRADE_B

pytestmark = pytest.mark.unit

EQUITY_SNAPSHOT = {"strategy_type": "sma_crossover", "config": {"fast_period": 10}}


def _metrics():
    return SimpleNamespace(
        total_return=Decimal("0.0149"),
        final_balance=Decimal("101486.50"),
        cagr=Decimal("0.06"),
        sharpe_ratio=Decimal("1.1"),
        sortino_ratio=Decimal("1.2"),
        max_drawdown=Decimal("-0.02"),
        max_drawdown_date=None,
        calmar_ratio=Decimal("3"),
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


def _run(snapshot, symbol="ES", trades=(TRADE_A, TRADE_B)):
    return SimpleNamespace(
        run_id=UUID("12345678-1234-5678-1234-567812345678"),
        strategy_name="SMA Crossover",
        strategy_type="sma_crossover",
        instrument_symbol=symbol,
        start_date=datetime(2024, 3, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 3, 31, tzinfo=timezone.utc),
        initial_capital=Decimal("100000"),
        data_source="catalog",
        execution_status="success",
        execution_duration_seconds=Decimal("1.5"),
        created_at=datetime(2024, 4, 1, tzinfo=timezone.utc),
        error_message=None,
        reproduced_from_run_id=None,
        config_snapshot=snapshot,
        metrics=_metrics(),
        trades=list(trades),
    )


@contextmanager
def _captured(module):
    buffer = io.StringIO()
    with patch.object(module, "console", Console(file=buffer, width=200, no_color=True)):
        yield buffer


class TestShow:
    def test_futures_run_shows_return_on_margin_and_terms(self):
        with _captured(show) as out:
            show._display_backtest(_run(ES_SNAPSHOT))
        text = out.getvalue()
        assert "Futures" in text
        assert "Return on margin (headline)" in text
        assert "+2.70%" in text
        assert "Return on underlying" in text
        assert "$55,000.00" in text
        assert "ratio-adjusted" in text
        assert text.index("Return on margin") < text.index("Configuration Snapshot")

    def test_equity_run_has_no_futures_block(self):
        with _captured(show) as out:
            show._display_backtest(_run(EQUITY_SNAPSHOT, symbol="AAPL"))
        assert "Return on margin" not in out.getvalue()
        assert "ratio-adjusted" not in out.getvalue()


class TestReportSummary:
    def test_futures_summary_adds_the_futures_table(self):
        with _captured(report) as out:
            report._display_summary_panel(_run(ES_SNAPSHOT))
            report._display_metrics_table(_run(ES_SNAPSHOT))
            report._display_futures(_run(ES_SNAPSHOT))
        assert "Return on margin (headline)" in out.getvalue()

    def test_equity_summary_prints_nothing_for_futures(self):
        with _captured(report) as out:
            report._display_futures(_run(EQUITY_SNAPSHOT, symbol="AAPL"))
        assert out.getvalue() == ""


class TestReportFiles:
    def test_text_report_has_futures_section(self, tmp_path):
        path = tmp_path / "r.txt"
        with _captured(report):
            report._generate_text_report(_run(ES_SNAPSHOT), str(path))
        text = path.read_text()
        assert "Return on margin (headline): +2.70%" in text
        assert "ratio-adjusted" in text

    def test_equity_text_report_unchanged(self, tmp_path):
        path = tmp_path / "r.txt"
        with _captured(report):
            report._generate_text_report(_run(EQUITY_SNAPSHOT, symbol="AAPL"), str(path))
        assert "Return on margin" not in path.read_text()

    def _csv(self, backtest, tmp_path):
        path = tmp_path / "r.csv"
        with _captured(report):
            report._generate_csv_report(backtest, str(path))
        with path.open() as handle:
            return {row["metric"]: row["value"] for row in csv.DictReader(handle)}

    def test_csv_report_has_futures_rows(self, tmp_path):
        rows = self._csv(_run(ES_SNAPSHOT), tmp_path)
        assert rows["futures_return_on_margin_pct"] == "2.7027"
        assert rows["futures_peak_margin_usd"] == "55000.00"
        assert rows["futures_ES.CME_multiplier"] == "50"
        assert rows["asset_class"] == "futures"

    def test_equity_csv_report_keys_unchanged(self, tmp_path):
        rows = self._csv(_run(EQUITY_SNAPSHOT, symbol="AAPL"), tmp_path)
        assert not [k for k in rows if k.startswith("futures_") or k == "asset_class"]

    def _json(self, backtest, tmp_path):
        path = tmp_path / "r.json"
        with _captured(report):
            report._generate_json_report(backtest, str(path))
        return json.loads(path.read_text())

    def test_json_report_has_futures_summary_and_trades(self, tmp_path):
        data = self._json(_run(ES_SNAPSHOT), tmp_path)
        assert data["futures"]["return_on_margin_pct"] == "2.7027"
        assert data["futures"]["note"].startswith("Prices are ratio-adjusted")
        trades = data["futures"]["trades"]
        assert [t["contracts"] for t in trades] == ["2", "1"]
        assert trades[1]["points_adjusted"] == "-10.00"

    def test_equity_json_report_has_no_futures_key(self, tmp_path):
        data = self._json(_run(EQUITY_SNAPSHOT, symbol="AAPL"), tmp_path)
        assert "futures" not in data
        assert data["metrics"]["total_trades"] == 2


class TestHistory:
    def _history(self, runs):
        repo = MagicMock()
        repo.find_recent.return_value = runs
        session = MagicMock()
        session.__enter__.return_value = session
        with (
            patch.object(history, "get_sync_session", return_value=session),
            patch.object(history, "SyncBacktestRepository", return_value=repo),
            _captured(history) as out,
        ):
            history._list_history_sync(10, None, None, None, "date", False, False)
        return out.getvalue()

    def test_futures_row_shows_return_on_margin(self):
        text = self._history([_run(ES_SNAPSHOT)])
        assert "ES (fut)" in text
        assert "RoM +2.70%" in text

    def test_equity_row_unchanged(self):
        text = self._history([_run(EQUITY_SNAPSHOT, symbol="AAPL")])
        assert "1.49%" in text
        assert "(fut)" not in text
        assert "RoM" not in text
