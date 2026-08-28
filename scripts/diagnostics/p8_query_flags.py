"""P8 criterion 2 — read a running session's failure state from another process.

This is the `psql` query the procedure describes, expressed through the same
database layer the rest of the repo uses so it needs no interactive password.
It is deliberately read-only: `status` and `runtime_flags` are observed, never
written, and no reclaim or transition is triggered.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text  # noqa: E402

from src.db.session_sync import get_sync_session  # noqa: E402

QUERY = """
SELECT name, status, last_heartbeat_at, last_bar_at, runtime_flags
  FROM trading_sessions
 WHERE name = :name
"""


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "contain-test-2"
    with get_sync_session() as session:
        row = session.execute(text(QUERY), {"name": name}).one_or_none()
    if row is None:
        print(f"no session named {name!r}")
        return 1
    print(f"name:             {row[0]}")
    print(f"status:           {row[1]}")
    print(f"last_heartbeat_at:{row[2]}")
    print(f"last_bar_at:      {row[3]}")
    print("runtime_flags:", json.dumps(row[4], indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
