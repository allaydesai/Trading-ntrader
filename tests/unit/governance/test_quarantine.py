"""The quarantine list: known-flaky tests the PR pipeline skips until they are fixed.

``@pytest.mark.quarantine(reason="...")`` takes a test out of the PR lane
(``-m "not quarantine"`` in ``.github/workflows/ci.yml``). The nightly workflow
still runs every quarantined test, so a fix shows up there before the marker is
removed. A quarantine is a debt, not a skip: each entry must say why, and the
list is capped so it cannot grow unnoticed.
"""

import ast
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[2]

#: Raise this only deliberately, in the same commit as the new quarantine.
MAX_QUARANTINED = 5


def _quarantine_calls() -> list[tuple[str, ast.Call]]:
    found = []
    for path in sorted(TESTS_ROOT.rglob("test_*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "quarantine"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "mark"
            ):
                found.append((f"{path.relative_to(TESTS_ROOT)}:{node.lineno}", node))
    return found


def _reason(call: ast.Call) -> str:
    for keyword in call.keywords:
        if keyword.arg == "reason" and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value).strip()
    return ""


def test_every_quarantine_says_why():
    missing = [where for where, call in _quarantine_calls() if not _reason(call)]
    assert not missing, f"quarantine without a reason= string: {missing}"


def test_the_quarantine_list_stays_small():
    count = len(_quarantine_calls())
    assert count <= MAX_QUARANTINED, (
        f"{count} quarantined tests (cap {MAX_QUARANTINED}): fix one before adding another"
    )


def test_the_marker_is_registered(pytestconfig: pytest.Config):
    registered = [line.split(":")[0].split("(")[0] for line in pytestconfig.getini("markers")]
    assert "quarantine" in registered
