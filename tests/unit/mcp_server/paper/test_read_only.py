"""The paper reads never write: no ORM or SQL write, no session-service code (safety boundary).

Postgres refuses a write inside ``read_only_session`` at run time (integration
tests prove it); this scan catches one at review time, in every paper module
except the two that are not reads: ``handoff`` (settles the study ledger, like
every study tool, and is held to exactly that one write below) and ``record``
(writes vault files, not rows). The scan matters most where Postgres is not
the guard: the scorecard and ``paper_commands`` read sessions inside the
study's own read-write transaction. Globbed, so a
new paper module is scanned the day it appears; the planted probes prove the
scan can fail.
"""

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PAPER_DIR = Path(__file__).resolve().parents[4] / "src" / "mcp_server" / "paper"
NOT_READS = {"handoff.py", "record.py"}
READ_FILES = sorted(p for p in PAPER_DIR.glob("*.py") if p.name not in NOT_READS)

WRITE_METHODS = {"add", "add_all", "delete", "merge", "flush", "commit", "settle", "execute_write"}
WRITE_METHODS |= {"insert", "update", "bulk_save_objects", "bulk_insert_mappings"}
WRITE_CONSTRUCTS = {"insert", "update", "delete", "setattr"}
WRITE_SQL = re.compile(
    r"^\s*(insert\s+into|update\s+\S+\s+set|delete\s+from|truncate\s|merge\s+into"
    r"|(drop|alter|create)\s+(table|index|schema|view|sequence|type)|grant\s|copy\s)",
    re.IGNORECASE,
)
FORBIDDEN_IMPORTS = ("src.services.session_service", "src.db.repositories.trading_session")


def _imports(node: ast.AST) -> list[str]:
    """A forbidden module however it is imported, or a write construct under any alias."""
    if isinstance(node, ast.Import):
        return [f"import {a.name}" for a in node.names if a.name.startswith(FORBIDDEN_IMPORTS)]
    if not isinstance(node, ast.ImportFrom):
        return []
    module = node.module or ""
    if module.startswith(FORBIDDEN_IMPORTS):
        return [f"import {module}"]
    if module.split(".")[0] == "sqlalchemy":
        return [f"import {a.name}" for a in node.names if a.name in WRITE_CONSTRUCTS]
    return []


def writes_in(source: str) -> list[str]:
    """Every write-shaped call, statement, SQL string or forbidden import in a source file.

    An attribute assignment counts: on a loaded row it is an UPDATE at the next
    flush, and the scorecard reads sessions inside a transaction that commits.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in WRITE_METHODS:
                found.append(f".{func.attr}()")
            if isinstance(func, ast.Name) and func.id in WRITE_CONSTRUCTS:
                found.append(f"{func.id}()")
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            found += [f".{t.attr} =" for t in targets if isinstance(t, ast.Attribute)]
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if WRITE_SQL.match(node.value):
                found.append(f"sql {node.value.split()[0].lower()}")
        found += _imports(node)
    return found


def test_the_scan_covers_the_read_modules():
    names = {p.name for p in READ_FILES}
    assert {"reads.py", "report.py", "sessions.py", "evidence.py"} <= names


@pytest.mark.parametrize("path", READ_FILES, ids=lambda p: p.name)
def test_a_paper_read_module_never_writes(path):
    assert writes_in(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "planted",
    [
        "def f(s, row):\n    s.add(row)\n",
        "def f(s):\n    s.commit()\n",
        "from sqlalchemy import update\n",
        "def f(t):\n    return insert(t)\n",
        "from src.services.session_service import SessionService\n",
        "from src.db.repositories.trading_session_repository_sync import X\n",
        "import sqlalchemy as sa\ndef f(t):\n    return sa.update(t)\n",
        "def f(s):\n    s.execute(text('UPDATE trading_sessions SET status = 1'))\n",
        "def f(s):\n    s.execute(text('  delete from trades'))\n",
        "from sqlalchemy.dialects.postgresql import insert as pg_insert\n",
        "import src.services.session_service as svc\n",
        "def f(row):\n    row.status = 'stopped'\n",
        "def f(row):\n    row.owner_epoch += 1\n",
        "def f(row):\n    setattr(row, 'status', 'stopped')\n",
    ],
)
def test_the_scan_catches_a_planted_write(planted):
    assert writes_in(planted) != []


def test_the_session_tools_open_a_read_only_transaction():
    source = (PAPER_DIR / "sessions.py").read_text(encoding="utf-8")
    assert "read_only_session" in source and "get_sync_session" not in source
    reads = (PAPER_DIR / "reads.py").read_text(encoding="utf-8")
    assert 'text("SET TRANSACTION READ ONLY")' in reads


def test_the_handoff_writes_only_the_study_ledger():
    """``paper_commands`` settles the ledger like every study tool, and nothing else."""
    source = (PAPER_DIR / "handoff.py").read_text(encoding="utf-8")
    assert writes_in(source) == [".settle()"]


def test_the_paper_tool_module_never_writes():
    source = (PAPER_DIR.parent / "tools" / "paper.py").read_text(encoding="utf-8")
    assert writes_in(source) == []
