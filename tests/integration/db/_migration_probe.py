"""Out-of-process probe: build a schema with the real migration chain and describe it.

Run as a subprocess by ``test_migration_schema.py``, never imported. Two reasons it
lives out of process rather than in the test:

1. **Fork safety.** Integration tests run under ``--forked``, and a psycopg2
   connection opened inside a forked child segfaults on macOS (the async siblings in
   this directory survive because asyncpg does not use libpq). Keeping every
   connection in a fresh subprocess sidesteps that entirely.
2. **Faithfulness.** The question is "what does a clean ``alembic upgrade head``
   produce", and a separate process with its own settings, engine, and import graph
   is a closer approximation of a new environment than anything in-process.

Prints one ``JSON:{...}`` line on success; any failure exits non-zero with the
traceback on stderr, which the caller surfaces verbatim.

Environment:
    MIGTEST_SCHEMA    scratch schema to create, migrate, inspect, and drop
    MIGTEST_BASE_URL  admin URL (no search_path) for schema create/drop
    DATABASE_URL      the same URL with search_path set, used by alembic
"""

import json
import os
import sys

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command

_ENUM_QUERY = text(
    "SELECT e.enumlabel FROM pg_enum e "
    "JOIN pg_type t ON t.oid = e.enumtypid "
    "JOIN pg_namespace n ON n.oid = t.typnamespace "
    "WHERE t.typname = 'resolution_status' AND n.nspname = :schema"
)


def _exec(url: str, statement: str) -> None:
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(statement))
    finally:
        engine.dispose()


def main() -> int:
    schema = os.environ["MIGTEST_SCHEMA"]
    base_url = os.environ["MIGTEST_BASE_URL"]
    scoped_url = os.environ["DATABASE_URL"]

    _exec(base_url, f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    _exec(base_url, f"CREATE SCHEMA {schema}")
    try:
        command.upgrade(Config("alembic.ini"), "head")

        engine = create_engine(scoped_url)
        try:
            columns = {
                col["name"]: bool(col["nullable"])
                for col in inspect(engine).get_columns("catalog_instruments", schema=schema)
            }
            with engine.connect() as conn:
                enum_values = sorted(conn.execute(_ENUM_QUERY, {"schema": schema}).scalars())
        finally:
            engine.dispose()

        print("JSON:" + json.dumps({"columns": columns, "resolution_status": enum_values}))
        return 0
    finally:
        _exec(base_url, f"DROP SCHEMA IF EXISTS {schema} CASCADE")


if __name__ == "__main__":
    sys.exit(main())
