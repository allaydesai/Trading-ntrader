"""``run_regime_attribution`` and its vault export through an in-memory MCP client (2.8).

Moved from the component tier, as 1.11 was: the snapshot and stored-report checks need the
calendar's and the reports' PL/pgSQL triggers. Storage is real Postgres in a throwaway
schema holding the run tables and the regime tables (``search_path`` is that schema alone,
so nothing reaches ``public``). Reference bars are a fixture frame; the calendar is built
from it by ``build_regime_calendar``.
"""

from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pandas as pd
import pytest
from mcp import Client
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from src.config import get_settings
from src.db.base import Base
from src.db.models.backtest import BacktestRun, PerformanceMetrics, RunEquityCurve
from src.db.models.regime import (
    RegimeCalendarBuild,
    RegimeCalendarVersion,
    RegimeLabel,
    RegimeReport,
)
from src.db.models.trade import Trade
from src.db.models.trading_session import TradingSession
from src.mcp_server.analysis import regime_calendar, regime_export, regime_reports
from src.mcp_server.context import ServerContext
from src.mcp_server.server import build_server
from src.mcp_server.settings import McpSettings
from tests.integration.mcp_server.conftest import requires_postgres
from tests.integration.mcp_server.test_regime_calendar_tools import make_bars

pytestmark = [pytest.mark.integration, requires_postgres]

TABLES = [
    t.__table__
    for t in (
        BacktestRun, PerformanceMetrics, TradingSession, Trade, RunEquityCurve,
        RegimeCalendarVersion, RegimeLabel, RegimeCalendarBuild, RegimeReport,
    )
]  # fmt: skip
INITIAL = 1_000_000.0


@pytest.fixture
def store(request, monkeypatch):
    worker = getattr(request.config, "workerinput", {}).get("workerid", "master")
    schema = f"test_regime_report_{worker}".replace("-", "_")
    url = get_settings().database_url
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {schema}"))
        Base.metadata.create_all(conn, tables=TABLES, checkfirst=False)
    maker = sessionmaker(engine, expire_on_commit=False)

    @contextmanager
    def session():
        s = maker()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    for module in (regime_calendar, regime_reports, regime_export):
        monkeypatch.setattr(module, "get_sync_session", session)
    yield session
    engine.dispose()
    with create_engine(url).begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


@pytest.fixture
def bars(monkeypatch):
    holder = {"frame": make_bars(700)}
    monkeypatch.setattr(regime_calendar, "load_reference_bars", lambda c, s: holder["frame"])
    monkeypatch.setattr(regime_reports, "load_reference_bars", lambda c, s: holder["frame"])
    return holder


@pytest.fixture
def server(tmp_path):
    vault = tmp_path / "vault"
    (vault / "Lab" / "results").mkdir(parents=True)
    settings = McpSettings(_env_file=None, jobs_dir=tmp_path / "jobs", vault_path=vault)
    return build_server(ServerContext(settings=settings))


async def _call(server, name, args=None):
    async with Client(server) as client:
        result = await client.call_tool(name, args or {})
    assert not result.is_error, result.content
    return result.structured_content


def _stamp(day: date) -> int:
    return int(datetime.combine(day, time(), tzinfo=timezone.utc).timestamp())


def seed_run(store, days: list[date], *, basis: str = "mark_to_market") -> str:
    """A stored SPY run over ``days``: a drifting curve, a few trades, one still open."""
    values = [INITIAL * (1 + 0.0004 * (i % 37) - 0.0002 * (i % 11)) for i in range(len(days))]
    run = BacktestRun(
        run_id=uuid4(), strategy_name="Fixture", strategy_type="fixture",
        instrument_symbol="SPY", start_date=datetime.combine(days[0], time(), timezone.utc),
        end_date=datetime.combine(days[-1], time(), timezone.utc),
        initial_capital=Decimal(str(INITIAL)), data_source="catalog:firstrate-etf",
        execution_status="success", execution_duration_seconds=Decimal("1"),
        config_snapshot={"metrics_basis": basis, "config": {}},
    )  # fmt: skip
    with store() as session:
        session.add(run)
        session.flush()
        session.add(
            PerformanceMetrics(
                backtest_run_id=run.id, total_return=Decimal("0"),
                final_balance=Decimal(str(round(values[-1], 2))), total_trades=3,
                winning_trades=1, losing_trades=2,
            )
        )  # fmt: skip
        session.add(
            RunEquityCurve(
                backtest_run_id=run.id,
                points=[{"time": _stamp(d), "value": v} for d, v in zip(days, values)],
            )
        )
        for i, (start, end) in enumerate(((5, 9), (40, 52), (120, 130), (len(days) - 3, None))):
            entry = datetime.combine(days[start], time(5), timezone.utc)
            exit_ = None if end is None else datetime.combine(days[end], time(5), timezone.utc)
            session.add(
                Trade(
                    backtest_run_id=run.id, instrument_id="SPY.ARCA", trade_id=f"t{i}",
                    venue_order_id=f"o{i}", order_side="BUY" if i % 2 else "SELL",
                    quantity=Decimal("10"), entry_price=Decimal("100"),
                    exit_price=None if end is None else Decimal("101"),
                    profit_loss=Decimal("0") if end is None else Decimal(str(10 * (i - 1))),
                    entry_timestamp=entry, exit_timestamp=exit_,
                )
            )  # fmt: skip
    return str(run.run_id)


def _run_days(frame: pd.DataFrame, first: int, last: int | None = None) -> list[date]:
    return list(frame.index[first:last])


async def test_a_realised_basis_run_is_refused(server, store, bars):
    await _call(server, "build_regime_calendar")
    run_id = seed_run(store, _run_days(bars["frame"], 300), basis="realised")
    data = await _call(server, "run_regime_attribution", {"run_ids": [run_id]})
    assert data["ok"] is False and data["error"]["code"] == "curve_missing"
    assert "reproduce_run" in data["error"]["fix"]


async def test_lag_defaults_per_label_set_and_ground_truth_refuses_any_lag(server, store, bars):
    await _call(server, "build_regime_calendar")
    run_id = seed_run(store, _run_days(bars["frame"], 300))
    detector = await _call(server, "run_regime_attribution", {"run_ids": [run_id]})
    assert detector["ok"] is True and detector["lag_days"] == 1
    truth = await _call(
        server, "run_regime_attribution", {"run_ids": [run_id], "label_set": "ground_truth"}
    )
    assert truth["ok"] is True and truth["lag_days"] == 0
    for lag in (0, 1):
        refused = await _call(
            server,
            "run_regime_attribution",
            {"run_ids": [run_id], "label_set": "ground_truth", "lag_days": lag},
        )
        assert refused["ok"] is False and refused["error"]["code"] == "lag_not_allowed"


async def test_payload_records_versions_hash_params_and_overrides(server, store, bars):
    await _call(server, "build_regime_calendar")
    run_id = seed_run(store, _run_days(bars["frame"], 300))
    data = await _call(server, "run_regime_attribution", {"run_ids": [run_id], "min_days": 40})
    (calendar,) = data["calendar"]
    assert (calendar["version"], calendar["build_no"], calendar["label_set"]) == (1, 1, "detector")
    assert len(data["labels_hash"]) == 64
    assert data["params"]["min_days"] == 40 and data["params"]["lag_days"] == 1
    assert data["overrides"] == {"min_days": 40} and data["declared_robustness_check"] is True
    (run,) = data["runs"]
    family = run["families"]["sma200"]
    assert family["reconciliation"]["reconciles"] is True
    with store() as session:
        row = session.scalars(select(RegimeReport)).one()
        assert str(row.report_id) == data["report_id"]
        assert row.calendar_version_ids == [{"version_id": calendar["version_id"], "build_no": 1}]
        assert row.labels_hash == data["labels_hash"] and row.hindsight is False
        assert row.params["overrides"] == {"min_days": 40}


async def test_hindsight_stamp_only_on_ground_truth(server, store, bars):
    await _call(server, "build_regime_calendar")
    run_id = seed_run(store, _run_days(bars["frame"], 300))
    truth = await _call(
        server, "run_regime_attribution", {"run_ids": [run_id], "label_set": "ground_truth"}
    )
    assert truth["hindsight"] is True and truth["stamp"] == "hindsight, not tradeable"
    assert set(truth["runs"][0]["families"]) >= {"leg_major", "ranging", "volatility", "cross"}
    detector = await _call(server, "run_regime_attribution", {"run_ids": [run_id]})
    assert detector["hindsight"] is False and "stamp" not in detector


async def test_ground_truth_snapshot_reproduces_after_values_fill_in(server, store, bars):
    """Stored at build 1; a later build fills the last sessions' values; as of build 1 the
    rerun reads the same rows (hash match) and gives identical buckets."""
    await _call(server, "build_regime_calendar")
    days = _run_days(bars["frame"], 400)
    run_id = seed_run(store, days)
    args = {"run_ids": [run_id], "label_set": "ground_truth"}
    first = await _call(server, "run_regime_attribution", args)
    bars["frame"] = make_bars(760)  # same seed: the first 700 bars are unchanged
    built = await _call(server, "build_regime_calendar")
    assert built["versions"]["ground_truth"]["build_no"] == 2
    again = await _call(server, "run_regime_attribution", {**args, "as_of_build": 1})
    assert again["labels_hash"] == first["labels_hash"]
    assert again["runs"][0]["families"] == first["runs"][0]["families"]
    assert again["overrides"] == {} and again["params"]["as_of_build"] == 1
    latest = await _call(server, "run_regime_attribution", args)
    assert latest["labels_hash"] != first["labels_hash"]
    assert latest["runs"][0]["families"] != first["runs"][0]["families"]


async def test_detector_saturday_past_data_end_stays_unlabelled_on_a_rerun(server, store, bars):
    bars["frame"] = make_bars(700).iloc[:-1]
    frame = bars["frame"]
    friday = next(d for d in reversed(frame.index) if d.weekday() == 4)
    bars["frame"] = frame[frame.index <= friday]
    await _call(server, "build_regime_calendar")
    days = [*_run_days(bars["frame"], 400), friday + timedelta(days=1)]
    run_id = seed_run(store, days)
    first = await _call(server, "run_regime_attribution", {"run_ids": [run_id]})
    assert first["runs"][0]["families"]["sma200"]["unlabelled"]["days"] == 1
    bars["frame"] = make_bars(700)[lambda f: f.index <= friday + timedelta(days=3)]
    built = await _call(server, "build_regime_calendar")
    assert built["versions"]["detector"]["build_no"] == 2
    again = await _call(server, "run_regime_attribution", {"run_ids": [run_id], "as_of_build": 1})
    assert again["runs"][0]["families"]["sma200"]["unlabelled"]["days"] == 1
    assert again["labels_hash"] == first["labels_hash"]
    latest = await _call(server, "run_regime_attribution", {"run_ids": [run_id]})
    assert latest["runs"][0]["families"]["sma200"]["unlabelled"]["days"] == 0


async def test_two_runs_report_correlation_overall_and_per_bucket(server, store, bars):
    await _call(server, "build_regime_calendar")
    days = _run_days(bars["frame"], 300)
    one, two = seed_run(store, days), seed_run(store, days[50:])
    data = await _call(server, "run_regime_attribution", {"run_ids": [one, two]})
    corr = data["correlation"]["sma200"]
    assert corr["shared_days"] == len(days) - 50
    assert corr["overall"][one][two] == pytest.approx(corr["overall"][two][one])
    assert [b["bucket"] for b in corr["buckets"]] == ["up", "down"]


async def test_export_writes_both_files_with_the_stamp(server, store, bars, tmp_path):
    await _call(server, "build_regime_calendar")
    run_id = seed_run(store, _run_days(bars["frame"], 300))
    truth = await _call(
        server, "run_regime_attribution", {"run_ids": [run_id], "label_set": "ground_truth"}
    )
    out = await _call(
        server, "export_results", {"slug": "gt-fixture", "regime_report": truth["report_id"]}
    )
    assert out["ok"] is True
    folder = tmp_path / "vault" / "Lab" / "results"
    stored = (folder / "gt-fixture.regime.json").read_text()
    note = (folder / "gt-fixture.regime.md").read_text()
    assert '"hindsight": true' in stored and "hindsight, not tradeable" in stored
    assert note.startswith("> **hindsight, not tradeable**")
    assert truth["labels_hash"] in note
    refused = await _call(
        server,
        "export_results",
        {"slug": "gt-fixture", "regime_report": truth["report_id"], "folder": "Elsewhere"},
    )
    assert refused["ok"] is False and refused["error"]["code"] == "folder_not_allowed"
    again = await _call(
        server, "export_results", {"slug": "gt-fixture", "regime_report": truth["report_id"]}
    )
    assert again["ok"] is False and again["error"]["code"] == "file_exists"
