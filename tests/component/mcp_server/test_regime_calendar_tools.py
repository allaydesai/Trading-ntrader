"""Regime calendar tools through an in-memory MCP client (tasks.md 1.11).

Bars come from a fixture frame (the catalog read is stubbed). Storage is real Postgres in
a throwaway schema, because the append-only rules live in PL/pgSQL triggers; the schema
is built by ``create_all``, whose hooks install the migration's trigger SQL.
"""

from contextlib import contextmanager
from datetime import date

import numpy as np
import pandas as pd
import pytest
from mcp import Client
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.config import get_settings
from src.db.base import Base
from src.db.models.regime import RegimeCalendarBuild, RegimeCalendarVersion, RegimeLabel
from src.db.repositories.regime_repository_sync import SyncRegimeRepository
from src.mcp_server.analysis import regime_calendar
from src.mcp_server.context import ServerContext
from src.mcp_server.server import build_server
from src.mcp_server.settings import McpSettings

pytestmark = pytest.mark.component

TABLES = [t.__table__ for t in (RegimeCalendarVersion, RegimeLabel, RegimeCalendarBuild)]


def _postgres_url() -> str | None:
    url = get_settings().database_url
    if not url:
        return None
    try:
        with create_engine(url).connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return None
    return url


def make_bars(n: int, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n)))
    days = pd.bdate_range("2000-01-03", periods=n).date
    frame = pd.DataFrame(
        {"open": close * 0.999, "high": close * 1.006, "low": close * 0.994, "close": close},
        index=pd.Index(days, name="session_date"),
    )
    return frame.round(4)


@pytest.fixture
def store(request, monkeypatch):
    """A scratch schema holding only the regime tables; yields a session maker."""
    url = _postgres_url()
    if url is None:
        pytest.skip("needs Postgres")
    worker = getattr(request.config, "workerinput", {}).get("workerid", "master")
    schema = f"test_regime_{worker}".replace("-", "_")
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

    monkeypatch.setattr(regime_calendar, "get_sync_session", session)
    yield session
    engine.dispose()
    with create_engine(url).begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


@pytest.fixture
def bars(monkeypatch):
    """The catalog the tools read; tests replace ``holder["frame"]`` to change it."""
    holder = {"frame": make_bars(700)}
    monkeypatch.setattr(regime_calendar, "load_reference_bars", lambda c, s: holder["frame"])
    return holder


@pytest.fixture
def server(tmp_path):
    return build_server(ServerContext(settings=McpSettings(_env_file=None, jobs_dir=tmp_path)))


async def _call(server, name, args=None):
    async with Client(server) as client:
        result = await client.call_tool(name, args or {})
    assert not result.is_error, result.content
    return result.structured_content


def _snapshot(store, label_set: str, version: int = 1) -> list[tuple]:
    with store() as session:
        repo = SyncRegimeRepository(session)
        found = repo.find_version("SPY", "firstrate-etf", label_set, version)
        read = repo.ground_truth_labels if label_set == "ground_truth" else repo.detector_labels
        return [(r.session_date, r.labels, r.bar_hash, r.inserted_in_build) for r in read(found)]


async def test_first_build_creates_both_versions(server, store, bars):
    data = await _call(server, "build_regime_calendar")
    assert data["ok"] is True
    for label_set in ("ground_truth", "detector"):
        version = data["versions"][label_set]
        assert (version["version"], version["input_revision"], version["build_no"]) == (1, 1, 1)
        assert version["action"] == "created" and version["rows"] == 700
        assert version["data_start"] == "2000-01-03"
    assert "volatility_median" in data["versions"]["ground_truth"]["frozen_stats"]


async def test_same_definition_again_is_a_no_op(server, store, bars):
    await _call(server, "build_regime_calendar")
    before = (_snapshot(store, "ground_truth"), _snapshot(store, "detector"))
    data = await _call(server, "build_regime_calendar")
    assert {v["action"] for v in data["versions"].values()} == {"up_to_date"}
    assert {v["build_no"] for v in data["versions"].values()} == {1}
    assert (_snapshot(store, "ground_truth"), _snapshot(store, "detector")) == before


async def test_new_bars_append_rows_and_fill_ground_truth(server, store, bars):
    full = make_bars(760)
    bars["frame"] = full.iloc[:700]
    await _call(server, "build_regime_calendar")
    detector_before = _snapshot(store, "detector")
    truth_before = _snapshot(store, "ground_truth")
    bars["frame"] = full
    data = await _call(server, "build_regime_calendar")
    for label_set in ("ground_truth", "detector"):
        version = data["versions"][label_set]
        assert (version["action"], version["rows_added"], version["build_no"]) == (
            "appended",
            60,
            2,
        )
    assert data["versions"]["ground_truth"]["values_filled"] > 0
    assert _snapshot(store, "detector")[:700] == detector_before
    truth_after = _snapshot(store, "ground_truth")[:700]
    for (day, old, hash_, built), (day2, new, hash2, built2) in zip(truth_before, truth_after):
        assert (day, hash_, built) == (day2, hash2, built2)
        assert all(new[k] == v for k, v in old.items() if v is not None)


async def test_changed_past_bar_is_refused_then_relabelled_as_revision_two(server, store, bars):
    await _call(server, "build_regime_calendar")
    changed = bars["frame"].copy()
    changed.iloc[100, changed.columns.get_loc("close")] += 1.0
    bars["frame"] = changed
    data = await _call(server, "build_regime_calendar")
    assert data["ok"] is False
    assert data["error"]["code"] == "reference_bars_changed"
    assert data["error"]["details"]["date"] == changed.index[100].isoformat()
    assert "new_revision" in data["error"]["fix"]
    before = _snapshot(store, "detector")
    data = await _call(
        server,
        "build_regime_calendar",
        {"new_revision": True, "revision_reason": "reference_bars_changed"},
    )
    assert data["ok"] is True
    for version in data["versions"].values():
        assert (version["version"], version["input_revision"]) == (2, 2)
        assert version["revision_reason"] == "reference_bars_changed"
    assert _snapshot(store, "detector", 1) == before


async def test_mid_year_extension_changes_no_stored_hash(server, store, bars):
    full = make_bars(720)
    bars["frame"] = full.iloc[:705]
    await _call(server, "build_regime_calendar")
    hashes = [(d, h) for d, _, h, _ in _snapshot(store, "detector")]
    bars["frame"] = full
    data = await _call(server, "build_regime_calendar")
    assert data["versions"]["detector"]["rows_added"] == 15
    assert [(d, h) for d, _, h, _ in _snapshot(store, "detector")][:705] == hashes


@pytest.mark.parametrize("kind", ["missing_from_catalog", "new_session_in_stored_range"])
async def test_historical_session_missing_or_inserted_is_refused(server, store, bars, kind):
    await _call(server, "build_regime_calendar")
    frame = bars["frame"]
    if kind == "missing_from_catalog":
        bars["frame"], day = frame.drop(frame.index[300]), frame.index[300]
    else:
        day = date(2000, 1, 8)  # a Saturday inside the stored range
        extra = frame.iloc[[3]].copy()
        extra.index = pd.Index([day], name="session_date")
        bars["frame"] = pd.concat([frame, extra]).sort_index()
    data = await _call(server, "build_regime_calendar")
    assert data["error"]["code"] == "reference_bars_changed"
    details = data["error"]["details"]
    assert (details["date"], details["kind"]) == (day.isoformat(), kind)


async def test_changed_definition_is_a_new_version(server, store, bars):
    await _call(server, "build_regime_calendar")
    before = _snapshot(store, "detector")
    overrides = {"detector": {"sma200": {"window": 150}}}
    data = await _call(server, "build_regime_calendar", {"definition_overrides": overrides})
    detector = data["versions"]["detector"]
    assert (detector["version"], detector["action"]) == (2, "created")
    assert detector["definition"]["sma200"] == {"window": 150}
    assert data["versions"]["ground_truth"]["action"] == "up_to_date"
    assert _snapshot(store, "detector", 1) == before


async def test_get_regime_labels_returns_dated_labels(server, store, bars):
    await _call(server, "build_regime_calendar")
    data = await _call(
        server, "get_regime_labels", {"start": "2001-03-01", "end": "2001-03-09", "limit": 3}
    )
    assert data["hindsight"] is False and "stamp" not in data
    assert [row["date"] for row in data["labels"]] == ["2001-03-01", "2001-03-02", "2001-03-05"]
    assert data["next_start"] == "2001-03-06"
    assert data["labels"][0]["sma200"] in ("up", "down")
    assert data["calendar"]["version"] == 1


async def test_ground_truth_output_is_stamped(server, store, bars):
    await _call(server, "build_regime_calendar")
    data = await _call(server, "get_regime_labels", {"label_set": "ground_truth", "limit": 5})
    assert data["hindsight"] is True
    assert data["stamp"] == "hindsight, not tradeable"
    assert set(data["labels"][0]) == {
        "date", "leg_minor", "leg_intermediate", "leg_major", "ranging", "volatility",
    }  # fmt: skip


async def test_read_as_of_an_earlier_build(server, store, bars):
    full = make_bars(760)
    bars["frame"] = full.iloc[:700]
    await _call(server, "build_regime_calendar")
    bars["frame"] = full
    await _call(server, "build_regime_calendar")
    args = {"label_set": "ground_truth", "as_of_build": 1, "start": "2002-08-01", "limit": 5000}
    then = await _call(server, "get_regime_labels", args)
    now = await _call(server, "get_regime_labels", {**args, "as_of_build": None})
    assert then["calendar"]["data_end"] == full.index[699].isoformat()
    assert now["calendar"]["data_end"] == full.index[-1].isoformat()
    assert len(then["labels"]) < len(now["labels"])


@pytest.mark.parametrize(
    ("tool", "args", "code"),
    [
        ("get_regime_labels", {}, "no_calendar"),
        ("get_regime_labels", {"label_set": "hindsight"}, "invalid_label_set"),
        ("build_regime_calendar", {"definition_overrides": {"x": {}}}, "invalid_definition"),
        (
            "build_regime_calendar",
            {"definition_overrides": {"detector": {"sma50": {}}}},
            "invalid_definition",
        ),  # fmt: skip
        ("build_regime_calendar", {"new_revision": True}, "invalid_revision"),
        ("build_regime_calendar", {"revision_reason": "median_recut"}, "invalid_revision"),
        (
            "build_regime_calendar",
            {"new_revision": True, "revision_reason": "median_recut"},
            "no_version_to_revise",
        ),  # fmt: skip
    ],
)
async def test_refusals_carry_a_fix(server, store, bars, tool, args, code):
    data = await _call(server, tool, args)
    assert data["ok"] is False
    assert data["error"]["code"] == code
    assert data["error"]["fix"]


async def test_versions_record_the_building_commit(server, store, bars):
    data = await _call(server, "build_regime_calendar")
    commit = data["versions"]["detector"]["git_commit"]
    assert commit and len(commit) == 40
    read = await _call(server, "get_regime_labels", {"limit": 1})
    assert read["calendar"]["git_commit"] == commit
    assert read["calendar"]["build_git_commit"] == commit


async def test_default_read_ignores_an_override_version(server, store, bars):
    await _call(server, "build_regime_calendar")
    overrides = {"detector": {"sma200": {"window": 150}}}
    await _call(server, "build_regime_calendar", {"definition_overrides": overrides})
    default = await _call(server, "get_regime_labels", {"limit": 1})
    pinned = await _call(server, "get_regime_labels", {"limit": 1, "version": 2})
    assert default["calendar"]["version"] == 1
    assert pinned["calendar"]["definition"]["sma200"] == {"window": 150}


@pytest.mark.parametrize(("args", "code"), [({"version": 0}, "no_calendar"),
                                            ({"as_of_build": 0}, "unknown_build")])  # fmt: skip
async def test_zero_is_not_unset(server, store, bars, args, code):
    await _call(server, "build_regime_calendar")
    data = await _call(server, "get_regime_labels", args)
    assert data["error"]["code"] == code


async def test_median_recut_revises_ground_truth_only(server, store, bars):
    await _call(server, "build_regime_calendar")
    data = await _call(
        server, "build_regime_calendar", {"new_revision": True, "revision_reason": "median_recut"}
    )
    truth, detector = data["versions"]["ground_truth"], data["versions"]["detector"]
    assert (truth["version"], truth["input_revision"], truth["action"]) == (2, 2, "created")
    assert (detector["version"], detector["action"]) == (1, "up_to_date")


def _stamped(hour_utc: int) -> pd.DataFrame:
    times = pd.DatetimeIndex(["2024-01-02", "2024-01-03"], tz="UTC") + pd.Timedelta(hours=hour_utc)
    return pd.DataFrame(
        {"open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0], "close": [1.0, 2.0],
         "volume": [0.0, 0.0]},
        index=times,
    )  # fmt: skip


def test_session_date_is_the_new_york_midnight_stamp(monkeypatch):
    monkeypatch.setattr(regime_calendar, "read_bars", lambda *a: _stamped(5))  # 00:00 EST
    frame = regime_calendar.load_reference_bars("firstrate-etf", "SPY")
    assert list(frame.index) == [date(2024, 1, 2), date(2024, 1, 3)]


def test_bars_stamped_at_the_futures_open_are_refused(monkeypatch):
    monkeypatch.setattr(regime_calendar, "read_bars", lambda *a: _stamped(22))  # 17:00 EST
    with pytest.raises(regime_calendar.ToolFailure) as caught:
        regime_calendar.load_reference_bars("firstrate-futures", "ES")
    assert caught.value.code == "unsupported_session_stamp"
