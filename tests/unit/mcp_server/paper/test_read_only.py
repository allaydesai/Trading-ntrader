"""The paper reads never write: no ORM or SQL write, no session-service code (safety boundary).

Postgres refuses a write inside ``read_only_session`` at run time (integration
tests prove it); this scan catches one at review time, in every paper module
except the two that are not reads: ``handoff`` (settles the study ledger, like
every study tool) and ``record`` (writes vault files, not rows). Globbed, so a
new paper module is scanned the day it appears; the planted probes prove the
scan can fail.
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PAPER_DIR = Path(__file__).resolve().parents[4] / "src" / "mcp_server" / "paper"
NOT_READS = {"handoff.py", "record.py"}
READ_FILES = sorted(p for p in PAPER_DIR.glob("*.py") if p.name not in NOT_READS)

WRITE_METHODS = {"add", "add_all", "delete", "merge", "flush", "commit", "settle", "execute_write"}
WRITE_CONSTRUCTS = {"insert", "update", "delete"}
FORBIDDEN_IMPORTS = ("src.services.session_service", "src.db.repositories.trading_session")


def writes_in(source: str) -> list[str]:
    """Every write-shaped call or forbidden import in a source file."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in WRITE_METHODS:
                found.append(f".{func.attr}()")
            if isinstance(func, ast.Name) and func.id in WRITE_CONSTRUCTS:
                found.append(f"{func.id}()")
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(FORBIDDEN_IMPORTS):
            found.append(f"import {node.module}")
        if isinstance(node, ast.ImportFrom) and node.module == "sqlalchemy":
            found += [f"import {a.name}" for a in node.names if a.name in WRITE_CONSTRUCTS]
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
    ],
)
def test_the_scan_catches_a_planted_write(planted):
    assert writes_in(planted) != []


def test_the_session_tools_open_a_read_only_transaction():
    source = (PAPER_DIR / "sessions.py").read_text(encoding="utf-8")
    assert "read_only_session" in source and "get_sync_session" not in source
    reads = (PAPER_DIR / "reads.py").read_text(encoding="utf-8")
    assert 'text("SET TRANSACTION READ ONLY")' in reads
