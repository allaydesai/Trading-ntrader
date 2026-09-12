"""Migration-truth tests: what ``alembic upgrade head`` actually builds.

These exist because the ORM and the migrations had drifted. Every other DB test
builds its schema with ``Base.metadata.create_all``, which uses the *ORM's*
nullability — so a migration that disagrees with the ORM is invisible to the whole
suite and only bites when a fresh environment (new dev box, CI, a rebuilt prod DB)
runs the real migration chain.

All the work happens in ``_migration_probe.py``, a subprocess. That is both the
faithful way to ask "what would a clean environment get" and the only fork-safe way
to ask it: integration tests run under ``--forked``, and a psycopg2 connection
opened inside a forked child segfaults on macOS. The async siblings in this
directory survive because asyncpg does not go through libpq.

Schema isolation rather than a scratch database because the ``ntrader`` role has no
CREATEDB privilege.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.config import get_settings

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROBE = Path(__file__).parent / "_migration_probe.py"

# catalog_instruments columns that sync_qualification writes NULL to whenever a
# ticker is not RESOLVED (src/services/metadata/qualification_sync.py).
_MUST_BE_NULLABLE = ("nautilus_id", "exchange", "name")

_EXPECTED_RESOLUTION_STATUSES = [
    "EXCLUDED",
    "RESOLVED",
    "UNRESOLVED",
    "VENUE_UNRESOLVED",
]


def _worker_id(request) -> str:
    """pytest-xdist worker id, or 'master' when running single-process."""
    return getattr(request.config, "workerinput", {}).get("workerid", "master")


def _base_url() -> str:
    """The configured database URL, or skip — these tests need a live Postgres."""
    url = get_settings().database_url
    if not url:
        pytest.skip("DATABASE_URL is not configured")
    return url


def _scratch_schema_url(base: str, schema: str) -> str:
    """A DATABASE_URL that routes all DDL into ``schema``.

    The ``=`` inside the option value is deliberately NOT percent-encoded: alembic
    feeds this URL through configparser, which treats ``%`` as interpolation syntax
    and rejects ``%3D``. The query parser splits on the first ``=``, so the value
    keeps its own ``=`` intact.
    """
    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}options=-csearch_path={schema}"


@pytest.fixture(scope="module")
def migrated(request) -> dict:
    """Describe the schema a clean ``alembic upgrade head`` produces.

    Module-scoped: the migration chain is the expensive part and every test here
    asks a different read-only question about the same result. Safe to share
    because the fixture holds plain data — no engine, no connection, nothing that
    could be inherited across a fork.
    """
    base = _base_url()
    schema = f"migtest_{_worker_id(request)}".replace("-", "_")
    completed = subprocess.run(
        [sys.executable, str(_PROBE)],
        cwd=_REPO_ROOT,
        env={
            **os.environ,
            "MIGTEST_SCHEMA": schema,
            "MIGTEST_BASE_URL": base,
            "DATABASE_URL": _scratch_schema_url(base, schema),
        },
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        pytest.fail(
            f"migration probe failed (exit {completed.returncode})\n"
            f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
        )
    line = next((ln for ln in completed.stdout.splitlines() if ln.startswith("JSON:")), None)
    if line is None:
        pytest.fail(f"migration probe produced no JSON line\nstdout:\n{completed.stdout}")
    return json.loads(line[len("JSON:") :])


def test_catalog_instruments_identity_columns_are_nullable(migrated):
    """A migrated catalog_instruments must accept NULL identity columns.

    ``sync_qualification`` writes ``nautilus_id=None``/``exchange=None`` for every
    ticker that is not RESOLVED — that null IS the exclusion mechanism the backtest
    loader honors. If the migration leaves these NOT NULL, importing an
    unresolved-venue ticker raises IntegrityError on any freshly-migrated database.
    """
    columns = migrated["columns"]
    offenders = [
        name for name in _MUST_BE_NULLABLE if name not in columns or columns[name] is not True
    ]

    assert not offenders, (
        f"catalog_instruments {offenders} are NOT NULL after 'alembic upgrade head', "
        f"but the ORM declares them nullable and sync_qualification writes NULL to them."
    )


def test_owner_epoch_is_not_null_with_a_zero_default(migrated):
    """Story 3.6 (AC #7): every row, including the two pre-existing ones,
    must have a comparable epoch the moment this column exists — a NULL
    would make the first qualified UPDATE's WHERE clause unmatchable.
    """
    column = migrated["trading_sessions_columns"]["owner_epoch"]
    assert column["nullable"] is False
    # The reflected server_default text varies by dialect wrapping
    # ("0" vs "'0'::bigint") -- match the whole token, so `10`, `100` or a
    # `nextval(...)` string fail (review 2026-09-12).
    import re

    assert column["default"] is not None
    assert re.fullmatch(r"'?0'?(::bigint)?", str(column["default"]).strip())


def test_uq_trades_session_trade_key_is_unique_over_the_right_columns(migrated):
    """Story 3.6 (AC #7): idempotent trade writes key on this exact triple,
    in this exact order — no new column, per fact 2 (`trade_id` alone is not
    unique per round trip under NETTING; `(trade_id, client_order_id)` is).
    """
    index = migrated["trade_key_index"]
    assert [entry["column"] for entry in index] == [
        "session_id",
        "trade_id",
        "client_order_id",
    ]
    assert all(entry["unique"] for entry in index)


def test_resolution_status_enum_includes_excluded(migrated):
    """The resolution_status enum must carry EXCLUDED for the venue exclusion register.

    A ticker leaves VENUE_UNRESOLVED either by resolving a venue or by an audited
    entry in venue_exclusions.csv. The latter needs a persisted, queryable state so
    the coverage gate can count it separately instead of hiding it in the
    denominator.
    """
    assert migrated["resolution_status"] == _EXPECTED_RESOLUTION_STATUSES
