"""Migration-truth tests: what ``alembic upgrade head`` actually builds.

These tests exist because the ORM and the migrations had drifted. Every other DB
test builds its schema with ``Base.metadata.create_all``, which uses the *ORM's*
nullability — so a migration that disagrees with the ORM is invisible to the whole
suite and only bites when a fresh environment (new dev box, CI, a rebuilt prod DB)
runs the real migration chain.

Each test runs the real ``alembic upgrade head`` in a subprocess against a scratch
Postgres schema, then inspects the result. Subprocess rather than in-process so the
run is faithful — no cached ``get_settings()``, no shared engine, no import-order
tricks. Schema isolation rather than a scratch database because the ``ntrader`` role
has no CREATEDB privilege.
"""

import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from src.config import get_settings

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[3]

# catalog_instruments columns that sync_qualification writes NULL to when a
# ticker's venue is unresolved (src/services/firstrate/instrument_mapper.py).
_MUST_BE_NULLABLE = ("nautilus_id", "exchange", "name")


def _worker_id(request) -> str:
    """pytest-xdist worker id, or 'master' when running single-process."""
    return getattr(request.config, "workerinput", {}).get("workerid", "master")


def _base_url() -> str:
    """The configured database URL, or skip — these tests need a live Postgres."""
    url = get_settings().database_url
    if not url:
        pytest.skip("DATABASE_URL is not configured")
    return url


def _scratch_schema_url(schema: str) -> str:
    """Build a DATABASE_URL that routes all DDL into ``schema``.

    The ``=`` inside the option value is deliberately NOT percent-encoded: alembic
    feeds this URL through configparser, which treats ``%`` as interpolation syntax
    and rejects ``%3D``. The query parser splits on the first ``=``, so the value
    keeps its own ``=`` intact.
    """
    base = _base_url()
    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}options=-csearch_path={schema}"


@pytest.fixture(scope="module")
def migrated_schema(request):
    """Run the real migration chain into a scratch schema; yield an inspectable engine.

    Module-scoped: ``alembic upgrade head`` takes a few seconds and every test here
    asks a read-only question of the same result.
    """
    schema = f"migtest_{_worker_id(request)}".replace("-", "_")
    admin = create_engine(_base_url())

    with admin.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {schema}"))

    completed = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=_REPO_ROOT,
        env={**os.environ, "DATABASE_URL": _scratch_schema_url(schema)},
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        pytest.fail(
            f"alembic upgrade head failed (exit {completed.returncode})\n"
            f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
        )

    engine = create_engine(_scratch_schema_url(schema))
    try:
        yield engine, schema
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        admin.dispose()


@pytest.mark.parametrize("column_name", _MUST_BE_NULLABLE)
def test_catalog_instruments_identity_columns_are_nullable(migrated_schema, column_name):
    """A migrated catalog_instruments must accept NULL identity columns.

    ``sync_qualification`` writes ``nautilus_id=None``/``exchange=None`` for every
    VENUE_UNRESOLVED ticker — that null IS the exclusion mechanism. If the migration
    leaves these NOT NULL, importing an unresolved ticker raises IntegrityError on any
    freshly-migrated database.
    """
    engine, schema = migrated_schema
    columns = {
        c["name"]: c for c in inspect(engine).get_columns("catalog_instruments", schema=schema)
    }

    assert column_name in columns, f"catalog_instruments.{column_name} missing after migration"
    assert columns[column_name]["nullable"] is True, (
        f"catalog_instruments.{column_name} is NOT NULL after 'alembic upgrade head', "
        f"but the ORM declares it nullable and sync_qualification writes NULL to it."
    )
