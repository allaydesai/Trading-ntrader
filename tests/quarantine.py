"""Quarantine rules for known-flaky tests, checked on the marks pytest applies.

``@pytest.mark.quarantine(reason="...")`` deselects a test from the PR lane
(``-m "not quarantine"``). Nightly still runs it. Wired in ``tests/conftest.py``;
documented in ``.github/workflows/README.md``.
"""

from collections.abc import Iterable
from typing import Any, Protocol

import pytest

#: Quarantined tests allowed in one pytest run. Raise only deliberately, in the
#: commit that adds the new entry: fix one before adding another.
MAX_QUARANTINED = 5


class _Item(Protocol):
    nodeid: str

    def get_closest_marker(self, name: str) -> Any: ...


def register(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "quarantine(reason): known-flaky; skipped on PRs, still run nightly. "
        "Fix the test and remove the marker (tests/quarantine.py).",
    )


def _reason(mark: Any) -> str:
    reason = mark.kwargs.get("reason")
    return reason.strip() if isinstance(reason, str) else ""


def check_quarantine(items: Iterable[_Item]) -> None:
    """Refuse the run if a quarantined test has no reason, or there are too many.

    Must run before ``-m`` deselection (``tryfirst``), so the PR lane sees the
    entries it is about to skip.
    """
    quarantined = [
        (item.nodeid, mark)
        for item in items
        if (mark := item.get_closest_marker("quarantine")) is not None
    ]
    missing = [nodeid for nodeid, mark in quarantined if not _reason(mark)]
    if missing:
        raise pytest.UsageError(f"quarantine without a reason= string: {missing}")
    if len(quarantined) > MAX_QUARANTINED:
        raise pytest.UsageError(
            f"{len(quarantined)} quarantined tests (cap {MAX_QUARANTINED}): "
            "fix one before adding another"
        )
