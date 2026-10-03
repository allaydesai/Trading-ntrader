"""Fixtures for MCP integration tests: real catalog + Postgres, results in a throwaway schema.

Instrument metadata is read from the real ``public`` schema (the ``e2e-test``
catalog's rows); runs, metrics and trades are written to a per-worker schema
that is dropped afterwards, so tests never add rows to the results database.
"""

import json
import os
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.config import CatalogSettings, get_settings
from src.db.base import Base
from src.db.models.backtest import BacktestRun, PerformanceMetrics
from src.db.models.trade import Trade

E2E_CATALOG = "e2e-test"

# libpq's GSSAPI negotiation segfaults inside a ``--forked`` child on macOS
# (measured: psycopg2.connect, signal 11, before any query). These tests never
# use GSS, so turn the negotiation off. The real MCP worker is exec'd, not forked.
os.environ.setdefault("PGGSSENCMODE", "disable")


def _e2e_catalog_present() -> bool:
    base = CatalogSettings().catalog_base_path
    return bool(base) and (Path(base) / E2E_CATALOG).is_dir()


def _postgres_up() -> bool:
    """Probe with asyncpg on a scratch loop.

    Not psycopg2: a libpq connection opened here, at collection time in the
    parent, segfaults the ``--forked`` children on macOS.
    """
    from tests.integration.db.conftest import is_postgres_available

    return is_postgres_available()


requires_e2e_data = pytest.mark.skipif(
    not (_e2e_catalog_present() and _postgres_up()),
    reason="needs Postgres and the local e2e-test catalog",
)


@pytest.fixture
def isolated_results(request, monkeypatch):
    """Patch NTrader's session factories onto a throwaway results schema; yields a sync maker."""
    worker = getattr(request.config, "workerinput", {}).get("workerid", "master")
    schema = f"test_mcp_{worker}".replace("-", "_")
    url = get_settings().database_url

    # search_path is set at connection startup, not with a SET statement: a SET
    # runs inside the first implicit transaction and the first rollback reverts
    # it, after which pooled connections would write to ``public`` (measured).
    search_path = f"{schema},public"
    sync_engine = create_engine(url, connect_args={"options": f"-csearch_path={search_path}"})
    with sync_engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {schema}"))
        tables = [BacktestRun.__table__, PerformanceMetrics.__table__, Trade.__table__]
        # checkfirst=False: with public on the search_path, the existence check
        # would find the real tables and silently create nothing here.
        Base.metadata.create_all(conn, tables=tables, checkfirst=False)
    sync_maker = sessionmaker(sync_engine, expire_on_commit=False)

    async_engine = create_async_engine(
        url.replace("postgresql://", "postgresql+asyncpg://"),
        connect_args={"server_settings": {"search_path": search_path}},
    )
    async_maker = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

    @contextmanager
    def sync_session():
        session = sync_maker()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @asynccontextmanager
    async def async_session():
        async with async_maker() as session:
            yield session

    monkeypatch.setattr("src.db.session_sync.get_sync_session", sync_session)
    for module in ("catalogs", "runs", "export"):
        monkeypatch.setattr(f"src.mcp_server.{module}.get_sync_session", sync_session)
    monkeypatch.setattr("src.core.backtest_orchestrator.get_session", async_session)
    yield sync_maker

    sync_engine.dispose()
    with create_engine(url).begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


@pytest.fixture
def job_dir(tmp_path):
    """Write a worker job directory for a spec dict."""

    def make(spec: dict, name: str = "job") -> Path:
        path = tmp_path / name
        path.mkdir()
        (path / "request.json").write_text(json.dumps({"kind": "backtest", "spec": spec}))
        return path

    return make
