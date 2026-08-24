"""Nothing on the node-facing path exits the process (Story 2.7, AC #3, AR38).

Unit tier: an ``ast`` walk over source text imports nothing and touches no
Nautilus, no broker and no event loop. Structural rather than textual on
purpose — Stories 2.1, 2.3, 2.5 and 2.6 each recorded the same trap, that a
docstring merely *mentioning* a forbidden name satisfies a substring search
while no code ever calls it. This module's own docstring says ``os._exit``
twice, and :class:`TestTheScanIsNotVacuous` proves that does not fire it.

⚠️ **A passing scan here is not, on its own, evidence of anything.** It passed
green for the whole of Epics 1 and 2 while a raising strategy still killed the
process at ``os._exit(1)`` — because the call was **Nautilus's**, in
``live/data_engine.py:347-365``, not ours. AC #3 therefore pairs this scan with
a **positive** proof that the third-party ``os._exit`` is unreachable for
strategy code: ``tests/integration/core/test_live_strategy_failure_survives.py``
runs a real ``LiveDataEngine`` with a raising strategy in a fresh interpreter
and asserts the return code is ``0``, and the same probe with the guard removed
exits ``1``. Read the two together; a scan alone would ship a false pass.
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: Every way a Python process can be ended from inside, matched on
#: **identifiers**: ``sys.exit`` and a bare ``exit`` both contribute ``exit``,
#: ``os._exit`` contributes ``_exit``, ``os.abort`` contributes ``abort``, and
#: ``raise SystemExit(...)`` contributes ``SystemExit`` whether it is raised
#: bare or called.
FORBIDDEN_EXIT_NAMES = frozenset({"exit", "_exit", "abort", "quit", "SystemExit"})

#: The node-facing modules: everything the runner builds, drives or is driven
#: by, plus the two service-layer modules its record port reaches through. A
#: failure anywhere here must reach the runner's ``finally`` — which is the
#: whole point, because that ``finally`` is what moves the session's row to
#: ``stopped`` instead of stranding it at ``running``.
NODE_FACING_MODULES = (
    "src/core/live_session_runner.py",
    "src/core/live_session_node.py",
    "src/core/live_session_steady_state.py",
    "src/core/live_strategy_guard.py",
    "src/core/live_node_builder.py",
    "src/core/live_bar_observer.py",
    "src/core/live_session_phases.py",
    "src/core/live_session_record.py",
    "src/core/live_session_controller.py",
    "src/core/live_account_gate.py",
    "src/core/live_connection_monitor.py",
    "src/core/live_connection_probe.py",
    "src/core/live_cache.py",
    "src/services/session_record.py",
    "src/services/session_service.py",
)

#: The two documented exemptions, by name and with the reason inline.
#:
#: ``src/core/live_session_signals.py`` — Story 2.6's sanctioned
#: ``os._exit(1)`` on a **second** stop signal. It is a *signal* path, not a
#: failure path: the operator has asked twice for the process to end, and the
#: whole point is that it is uncatchable. ``sys.exit(130)`` was considered and
#: rejected there, because ``live_check_node.shutdown`` catches
#: ``BaseException`` at every step and would swallow a ``SystemExit`` raised
#: from a handler that fired inside the teardown.
#:
#: ``src/cli/commands/live.py`` and ``live_start.py`` — the CLI boundary. AR28
#: gives ``ntrader live`` an exit-code table, and ``raise SystemExit(code)`` is
#: how a Click command reports one. Ending the *process* is exactly this
#: layer's job; the rule is that nothing **below** it may.
EXEMPT_MODULES = (
    "src/core/live_session_signals.py",
    "src/cli/commands/live.py",
    "src/cli/commands/live_start.py",
)


def _source(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def _called_names(source: str) -> set[str]:
    """Every function/attribute name that appears as the callee of a Call node.

    Copied verbatim from ``tests/unit/core/test_live_stop_path_is_inert.py:82-99``
    so the two scans cannot drift. Covers ``sys.exit(1)``, ``os._exit(1)`` and a
    bare ``exit()`` alike: an ``ast.Attribute`` callee contributes its ``.attr``,
    an ``ast.Name`` callee contributes its ``.id``.
    """
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            names.add(func.attr)
        elif isinstance(func, ast.Name):
            names.add(func.id)
    return names


def _raised_names(source: str) -> set[str]:
    """Every exception name that appears in a ``raise`` statement.

    ``_called_names`` alone would miss a bare ``raise SystemExit`` (no Call node
    at all) and would catch ``raise SystemExit(3)`` only incidentally, as a
    call. This scan is what makes the ``SystemExit`` half of
    :data:`FORBIDDEN_EXIT_NAMES` mean what it says.
    """
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        raised = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        if isinstance(raised, ast.Name):
            names.add(raised.id)
        elif isinstance(raised, ast.Attribute):
            names.add(raised.attr)
    return names


def _exit_hits(source: str) -> set[str]:
    return (_called_names(source) | _raised_names(source)) & FORBIDDEN_EXIT_NAMES


class TestNothingNodeFacingEndsTheProcess:
    """AC #3 — a failure must reach the runner's ``finally``, not ``os._exit``."""

    @pytest.mark.parametrize("relative_path", NODE_FACING_MODULES)
    def test_no_module_calls_or_raises_an_exit(self, relative_path):
        hits = _exit_hits(_source(relative_path))

        assert not hits, (
            f"{relative_path} ends the process ({sorted(hits)}). Everything on this path must "
            "raise instead, so the runner's teardown runs and the session's row reaches "
            "`stopped` rather than being stranded at `running`. If this is deliberate, it needs "
            "an entry in EXEMPT_MODULES with its reason, not a weakened scan."
        )

    def test_the_scanned_set_is_not_empty(self):
        """Mutation #10: pointing the scan at an empty tuple must fail loudly.

        Story 2.3's AR37 guard examined 4 of 192 files and passed a planted
        probe, which is why every AST scan in this repo carries one of these.
        """
        assert len(NODE_FACING_MODULES) >= 10

    @pytest.mark.parametrize("relative_path", NODE_FACING_MODULES)
    def test_every_scanned_module_exists_and_parses(self, relative_path):
        """A renamed module would silently drop out of the scan otherwise —
        exactly how ``live_start.py`` escaped two guards when Story 2.6 split
        it out of ``live.py``.
        """
        assert (PROJECT_ROOT / relative_path).is_file()
        ast.parse(_source(relative_path))


class TestTheExemptionsAreRealAndStillNeeded:
    """An exemption nobody uses is a hole waiting for the next author."""

    @pytest.mark.parametrize("relative_path", EXEMPT_MODULES)
    def test_each_exempt_module_genuinely_exits(self, relative_path):
        """If one stops exiting, delete its exemption rather than leaving a
        pre-approved gap in the scan.
        """
        assert _exit_hits(_source(relative_path))

    def test_no_module_is_both_scanned_and_exempt(self):
        assert not set(NODE_FACING_MODULES) & set(EXEMPT_MODULES)

    def test_the_signal_modules_exit_is_the_os_level_one(self):
        """Story 2.6's force exit is ``os._exit``, deliberately — ``sys.exit``
        raises ``SystemExit``, which ``live_check_node.shutdown``'s
        ``BaseException`` handlers would swallow if a second signal arrived
        during teardown.
        """
        assert "_exit" in _called_names(_source("src/core/live_session_signals.py"))


class TestTheScanIsNotVacuous:
    """Planted probes for **every** forbidden name, plus a prose negative."""

    @pytest.mark.parametrize(
        "probe, expected",
        [
            ("import sys\nsys.exit(1)", "exit"),
            ("exit(1)", "exit"),
            ("import os\nos._exit(1)", "_exit"),
            ("import os\nos.abort()", "abort"),
            ("quit()", "quit"),
            ("raise SystemExit(3)", "SystemExit"),
            ("raise SystemExit", "SystemExit"),
            ("raise builtins.SystemExit(3)", "SystemExit"),
        ],
    )
    def test_the_scan_detects_every_forbidden_name_it_claims_to(self, probe, expected):
        assert expected in _exit_hits(probe)

    def test_the_scan_does_not_fire_on_prose_that_merely_names_one(self):
        """The reason it is structural. This repo's live modules discuss
        ``os._exit`` at length — this very file does — and a substring check
        would fire on all of it.
        """
        prose = (
            '"""Nautilus ends the process at os._exit(1), so sys.exit and\n'
            'raise SystemExit are both unreachable here."""\n'
            'x = "the queue loop calls os._exit(1)"  # and quit() and os.abort()\n'
        )

        assert not _exit_hits(prose)

    def test_the_guards_own_module_docstring_is_a_live_prose_probe(self):
        """``live_strategy_guard.py`` names ``os._exit(1)`` in its docstring and
        is in the scanned set, so the prose negative above is not hypothetical.
        """
        source = _source("src/core/live_strategy_guard.py")

        assert "os._exit" in source
        assert not _exit_hits(source)
