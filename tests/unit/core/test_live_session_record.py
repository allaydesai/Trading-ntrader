"""Unit tests for AR32's session record port (Story 2.5).

Unit tier: ``src/core/live_session_record.py`` is a ``typing.Protocol`` and
nothing else — standard library only. That is what lets the runner depend on a
*port* while the SQLAlchemy adapter that satisfies it lives in
``src/services/``, which is the whole of AC #6.
"""

import ast
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.core import live_session_record as record_module
from src.core.live_session_record import SessionRecordPort

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)


class _HandWrittenRecord:
    """The narrowest thing that should satisfy the port."""

    def __init__(self) -> None:
        self.activity: list[tuple[datetime, datetime | None]] = []
        self.stopped = 0
        self.failures: list[str] = []

    def record_activity(self, *, at: datetime, bar_seen_at: datetime | None = None) -> None:
        self.activity.append((at, bar_seen_at))

    def mark_stopped(self) -> None:
        self.stopped += 1

    def record_strategy_failure(
        self,
        *,
        strategy_id: str,
        spec_strategy_id: str,
        error_type: str,
        handler: str,
        at: datetime,
        detail: str | None = None,
        all_failed: bool = False,
    ) -> None:
        self.failures.append(spec_strategy_id)


class _MissingMarkStopped:
    def record_activity(self, *, at: datetime, bar_seen_at: datetime | None = None) -> None:
        """Half a port is not a port."""


class TestTheProtocolShape:
    """AR32's *"record port"*, in the narrowest shape that satisfies AR38."""

    def test_the_port_declares_exactly_three_methods(self):
        """An exact set, not a subset: a further method would be a design change
        the runner and every adapter would silently inherit.

        ``record_strategy_failure`` is Story 2.7's — the third and, so far, last
        thing a *running* session needs from its own row.
        """
        declared = {name for name in vars(SessionRecordPort) if not name.startswith("_")}
        assert declared == {"record_activity", "mark_stopped", "record_strategy_failure"}
        # `Protocol` synthesises `__init__` and `_is_protocol`; the public
        # surface is what a caller and an adapter have to agree on.
        assert SessionRecordPort._is_protocol is True

    def test_a_hand_written_stub_satisfies_isinstance(self):
        """``@runtime_checkable`` is what lets a test double be *checked*, not
        merely duck-typed at the call site.
        """
        assert isinstance(_HandWrittenRecord(), SessionRecordPort)

    def test_a_partial_implementation_does_not_satisfy_isinstance(self):
        assert not isinstance(_MissingMarkStopped(), SessionRecordPort)

    def test_record_activity_is_keyword_only_and_bar_seen_at_defaults_to_none(self):
        import inspect

        signature = inspect.signature(SessionRecordPort.record_activity)
        parameters = list(signature.parameters.values())[1:]  # drop `self`

        assert [p.name for p in parameters] == ["at", "bar_seen_at"]
        assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parameters)
        assert parameters[1].default is None

    def test_the_port_takes_no_session_id_and_no_started_at(self):
        """Both are bound into the adapter at construction.

        A port that took a ``session_id`` would let a runner write to the wrong
        row; one that took a ``started_at`` would let it forge its own claim to
        ownership, which is exactly what the mid-run reclaim guard reads.
        """
        import inspect

        names = set(inspect.signature(SessionRecordPort.record_activity).parameters)
        assert "session_id" not in names
        assert "started_at" not in names


class TestImportPurity:
    """The port must reach a module that may import neither SQLAlchemy nor
    Nautilus, so it may import neither itself.
    """

    FORBIDDEN = ("sqlalchemy", "nautilus_trader", "ibapi", "src.db", "src.services")

    def test_top_level_imports_are_standard_library_only(self):
        tree = ast.parse(Path(record_module.__file__).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        offenders = [name for name in imported if name.startswith(self.FORBIDDEN)]
        assert offenders == [], f"the record port must stay pure, found: {offenders}"

    def test_importing_the_module_loads_neither_sqlalchemy_nor_nautilus(self):
        code = (
            "import sys, src.core.live_session_record;"
            "print(','.join(sorted(m for m in "
            "('sqlalchemy', 'nautilus_trader', 'ibapi') if m in sys.modules)))"
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(record_module.__file__).parents[2],
            capture_output=True,
            text=True,
            check=True,
        )

        assert result.stdout.strip() == ""
