"""Unit tests for AR39's phase vocabulary and its logging helper (Story 2.5).

Unit tier: ``src/core/live_session_phases.py`` is framework-free by contract —
no ``nautilus_trader``, no ``sqlalchemy`` — and this file must stay importable
without either. ``live_account_gate`` is therefore imported **inside** the one
test function that needs it, never at module scope.

What is under test is the *contract* the runner and Epic 4 both depend on: the
eight names, their order, and the ``started -> ok`` / ``started -> failed``
record pair the ordering assertions in ``test_session_runner_phases.py`` read.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest
import structlog
from structlog.testing import capture_logs

from src.core import live_session_phases as phases_module
from src.core.live_check import GATE_PHASE
from src.core.live_session_phases import ACCOUNT_GATE_PHASE, PHASE_SEQUENCE, phase

pytestmark = pytest.mark.unit

SESSION_ID = "11111111-1111-1111-1111-111111111111"


def _log():
    return structlog.get_logger("test_live_session_phases").bind(session_id=SESSION_ID)


class TestPhaseSequence:
    """AC #2: the eight AR39 names, in exactly this order."""

    def test_the_sequence_is_the_eight_ar39_names_in_order(self):
        assert PHASE_SEQUENCE == (
            "gate:static",
            "node:build",
            "node:connect",
            "gate:account",
            "reconcile",
            "warmup",
            "subscribe",
            "trading",
        )

    def test_the_sequence_has_exactly_eight_entries_and_no_duplicates(self):
        assert len(PHASE_SEQUENCE) == 8
        assert len(set(PHASE_SEQUENCE)) == 8

    def test_the_sequence_is_an_immutable_tuple(self):
        """A list would let a caller reorder the contract at runtime."""
        assert isinstance(PHASE_SEQUENCE, tuple)

    def test_the_static_gate_phase_is_the_very_object_live_check_exports(self):
        """Identity, not equality — ``live_check`` is import-pure, so this module
        imports the constant rather than re-spelling it, and identity is what
        proves that.
        """
        assert PHASE_SEQUENCE[0] is GATE_PHASE

    def test_the_account_gate_phase_equals_the_one_live_account_gate_logs(self):
        """Equality, **not** identity, and the difference is measured.

        ``"gate:account"`` contains a colon, so CPython does not intern it: two
        separately-compiled literals of that text are never the same object
        (cross-module ``is`` returns ``False``). This module cannot import
        ``live_account_gate`` either — that module imports ``nautilus_trader``
        at its line 18 and would destroy this file's unit-tier placement — so
        the import lives inside the test body and the assertion is ``==``.
        """
        from src.core.live_account_gate import STARTUP_PHASE

        assert ACCOUNT_GATE_PHASE == STARTUP_PHASE
        assert PHASE_SEQUENCE[3] == STARTUP_PHASE


class TestPhaseLogging:
    """AC #2/#4: ``phase=<name> status=started|ok|failed``, carrying session_id."""

    def test_a_clean_phase_logs_started_then_ok(self):
        with capture_logs() as logs:
            with phase(_log(), "node:build"):
                pass

        assert [entry["status"] for entry in logs] == ["started", "ok"]
        assert {entry["phase"] for entry in logs} == {"node:build"}
        assert {entry["session_id"] for entry in logs} == {SESSION_ID}

    def test_a_raising_phase_logs_started_then_failed_and_re_raises(self):
        with capture_logs() as logs:
            with pytest.raises(RuntimeError, match="boom"):
                with phase(_log(), "node:connect"):
                    raise RuntimeError("boom")

        assert [entry["status"] for entry in logs] == ["started", "failed"]
        assert logs[-1]["phase"] == "node:connect"
        assert logs[-1]["log_level"] == "error"

    def test_the_failed_record_carries_only_the_error_type_never_its_message(self):
        """NFR26: third-party error text routinely embeds the account id."""
        secret = "DU4076626 is not managed"

        with capture_logs() as logs:
            with pytest.raises(ValueError):
                with phase(_log(), "gate:account"):
                    raise ValueError(secret)

        assert logs[-1]["error_type"] == "ValueError"
        assert secret not in repr(logs)

    def test_the_started_record_is_emitted_before_the_body_runs(self):
        """A phase that never returns must still have announced itself."""
        seen: list[int] = []

        with capture_logs() as logs:
            with phase(_log(), "trading"):
                seen.append(len(logs))

        assert seen == [1]

    def test_a_base_exception_also_produces_a_failed_record_and_propagates(self):
        """``KeyboardInterrupt`` is not an ``Exception``; a phase must still close."""
        with capture_logs() as logs:
            with pytest.raises(KeyboardInterrupt):
                with phase(_log(), "subscribe"):
                    raise KeyboardInterrupt

        assert [entry["status"] for entry in logs] == ["started", "failed"]
        assert logs[-1]["error_type"] == "KeyboardInterrupt"

    def test_an_unknown_phase_name_is_refused(self):
        """AR39 says agents must not invent, reorder, merge or skip phases."""
        with pytest.raises(ValueError, match="not an AR39 phase"):
            with phase(_log(), "node:warmup"):
                pass


class TestImportPurity:
    """AC #6's polarity, one level stricter: this module is framework-free.

    Two forms, because Story 2.1's review proved the AST scan alone is blind to
    *transitive* loading — a ``src.*`` import that itself drags in Nautilus
    passes an AST check and fails the real requirement.
    """

    FORBIDDEN = ("nautilus_trader", "ibapi", "sqlalchemy", "src.db", "src.services")

    def test_top_level_imports_contain_no_framework_or_database_library(self):
        tree = ast.parse(Path(phases_module.__file__).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        offenders = [name for name in imported if name.startswith(self.FORBIDDEN)]
        assert offenders == [], f"live_session_phases must stay pure, found: {offenders}"

    def test_importing_the_module_loads_no_framework_or_database_library(self):
        code = (
            "import sys, src.core.live_session_phases;"
            "print(','.join(sorted(m for m in "
            "('nautilus_trader', 'ibapi', 'sqlalchemy') if m in sys.modules)))"
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(phases_module.__file__).parents[2],
            capture_output=True,
            text=True,
            check=True,
        )

        assert result.stdout.strip() == ""
