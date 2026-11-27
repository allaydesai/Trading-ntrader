"""SPY regime calendar from the real ``firstrate-etf`` catalog (tasks.md 1.13, exit check).

**Rebuild reproduces**: SPY is built into one scratch schema, rebuilt from scratch into a
second, and every stored label, bar hash and frozen statistic must be equal. A second
build into the first schema is a verified no-op (it recomputes every label and refuses
on any difference). Results never touch the real ``public`` schema.
"""

from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.config import CatalogSettings, get_settings
from src.db.base import Base
from src.db.models.regime import RegimeCalendarBuild, RegimeCalendarVersion, RegimeLabel
from src.db.repositories.regime_repository_sync import SyncRegimeRepository
from src.mcp_server.analysis import regime_calendar
from tests.integration.mcp_server.conftest import requires_postgres

pytestmark = [pytest.mark.integration, requires_postgres]

CATALOG = "firstrate-etf"
TABLES = [t.__table__ for t in (RegimeCalendarVersion, RegimeLabel, RegimeCalendarBuild)]


def _catalog_present() -> bool:
    base = CatalogSettings().catalog_base_path
    return bool(base) and (Path(base) / CATALOG / "data" / "bar").is_dir()


requires_etf = pytest.mark.skipif(not _catalog_present(), reason=f"needs the {CATALOG} catalog")


@contextmanager
def scratch(name: str):
    """A session factory on a throwaway schema with only the regime tables."""
    url = get_settings().database_url
    engine = create_engine(url, connect_args={"options": f"-csearch_path={name}"})
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {name} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {name}"))
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

    try:
        yield session
    finally:
        engine.dispose()
        with create_engine(url).begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {name} CASCADE"))


def _stored(session_factory) -> dict:
    with session_factory() as session:
        repo = SyncRegimeRepository(session)
        out = {}
        for label_set in ("ground_truth", "detector"):
            version = repo.latest_version("SPY", CATALOG, label_set)
            read = repo.ground_truth_labels if label_set == "ground_truth" else repo.detector_labels
            out[label_set] = {
                "definition_hash": version.definition_hash,
                "frozen_stats": version.frozen_stats,
                "range": (version.data_start, version.data_end),
                "rows": [(r.session_date, r.labels, r.bar_hash) for r in read(version)],
            }
        return out


@requires_etf
def test_spy_rebuild_reproduces_every_stored_label(monkeypatch):
    with scratch("test_regime_a") as first, scratch("test_regime_b") as second:
        monkeypatch.setattr(regime_calendar, "get_sync_session", first)
        built = regime_calendar.build_regime_calendar("SPY", CATALOG)
        again = regime_calendar.build_regime_calendar("SPY", CATALOG)
        monkeypatch.setattr(regime_calendar, "get_sync_session", second)
        regime_calendar.build_regime_calendar("SPY", CATALOG)
        a, b = _stored(first), _stored(second)

    sessions = built["bars"]["sessions"]
    assert built["bars"]["start"] == "2000-01-03"
    assert {v["action"] for v in again["versions"].values()} == {"up_to_date"}
    for label_set in ("ground_truth", "detector"):
        assert len(a[label_set]["rows"]) == sessions
        assert a[label_set] == b[label_set], label_set
    detector = [labels["sma200"] for _, labels, _ in a["detector"]["rows"]]
    assert detector[:272] == [None] * 272 and None not in detector[272:]
