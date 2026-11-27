"""Profile a stored SPY run against the stored SPY calendar (tasks.md 2.11, exit check).

Reads run ``c4c9c098`` (Bollinger Reversal, SPY daily, 2015-2025) and the stored SPY
calendar from the real database; reports are written to a throwaway schema that holds
only ``regime_reports`` (first on ``search_path``, so nothing is written to ``public``).
Skips where that run or calendar is absent (CI builds neither).
"""

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.config import get_settings
from src.db.base import Base
from src.db.models.regime import RegimeReport
from src.mcp_server.analysis import regime_export, regime_reports
from tests.integration.mcp_server.conftest import requires_postgres

pytestmark = [pytest.mark.integration, requires_postgres]

RUN = "c4c9c098-1062-47cc-a1f5-6d969264a31e"
LAST_EQUITY = 9_354_378.78
INITIAL = 10_000_000.00


def _present() -> bool:
    try:
        with create_engine(get_settings().database_url).connect() as conn:
            run = conn.execute(
                text("SELECT 1 FROM backtest_runs WHERE run_id = CAST(:r AS uuid)"), {"r": RUN}
            ).first()
            calendar = conn.execute(
                text(
                    "SELECT 1 FROM regime_calendar_versions WHERE reference = 'SPY' "
                    "AND catalog = 'firstrate-etf' AND label_set = 'detector'"
                )
            ).first()
    except Exception:
        return False
    return run is not None and calendar is not None


requires_stored_spy = pytest.mark.skipif(
    not _present(), reason=f"needs stored run {RUN[:8]} and the SPY calendar"
)


@pytest.fixture
def reports_schema(request, monkeypatch):
    worker = getattr(request.config, "workerinput", {}).get("workerid", "master")
    schema = f"test_regime_real_{worker}".replace("-", "_")
    url = get_settings().database_url
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema},public"})
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {schema}"))
        Base.metadata.create_all(conn, tables=[RegimeReport.__table__], checkfirst=False)
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

    monkeypatch.setattr(regime_reports, "get_sync_session", session)
    monkeypatch.setattr(regime_export, "get_sync_session", session)
    yield session
    engine.dispose()
    with create_engine(url).begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


@requires_stored_spy
@pytest.mark.parametrize("label_set", ["detector", "ground_truth"])
def test_bucket_pnl_reconciles_to_the_cent(reports_schema, label_set):
    data = regime_reports.run_regime_attribution([RUN], label_set=label_set, bootstrap_samples=200)
    (run,) = data["runs"]
    assert run["notes"] and "open" in run["notes"][0]
    for name, family in run["families"].items():
        rows = family["rows"] + [family["unlabelled"]]
        assert round(sum(r["pnl"] for r in rows), 2) == pytest.approx(
            LAST_EQUITY - INITIAL, abs=0.011
        ), name
        assert family["reconciliation"]["reconciles"] is True
        view = family["trades"]
        assert sum(r["trades"] for r in view["rows"]) + view["unlabelled"]["trades"] == 117
        assert view["open_trades"] == 1


@requires_stored_spy
def test_lag_zero_and_lag_one_differ_on_the_stored_run(reports_schema):
    lag0 = regime_reports.run_regime_attribution([RUN], lag_days=0, bootstrap_samples=200)
    lag1 = regime_reports.run_regime_attribution([RUN], bootstrap_samples=200)
    assert (lag0["lag_days"], lag1["lag_days"]) == (0, 1)
    rows0 = lag0["runs"][0]["families"]["sma200"]["rows"]
    rows1 = lag1["runs"][0]["families"]["sma200"]["rows"]
    assert rows0 != rows1  # the day counts can match: each flip moves one day each way
    assert [r["pnl"] for r in rows0] != [r["pnl"] for r in rows1]


@requires_stored_spy
def test_the_stored_report_round_trips(reports_schema):
    data = regime_reports.run_regime_attribution([RUN], label_set="ground_truth",
                                                 bootstrap_samples=200)  # fmt: skip
    stored = regime_export._stored(data["report_id"])
    for key in ("hindsight", "stamp", "labels_hash", "calendar", "params", "runs"):
        assert stored[key] == data[key], key
    assert stored["calendar_version_ids"] == [
        {
            "version_id": data["calendar"][0]["version_id"],
            "build_no": data["calendar"][0]["build_no"],
        }
    ]
