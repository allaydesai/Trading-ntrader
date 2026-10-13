"""Unit tests for the stop-signal policy (Story 2.6).

Unit tier: ``src/core/live_session_signals.py`` imports only the standard
library and ``structlog`` — no Nautilus, no asyncio event-loop machinery beyond
typing. That purity is what lets this module be tested with no broker, no
event loop and no C extension, and it is pinned below the same way
``test_live_session_record.py`` pins its own port.

Every test here injects ``force_exit`` — never a real ``os._exit`` — so a
mutation in this module cannot kill the pytest worker it runs in
(``-n auto``). Every handler-manipulating test restores whatever it armed in a
``finally``, because a leaked ``signal.signal`` registration poisons every
later test sharing this xdist worker.
"""

import ast
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from src.core import live_session_signals as signals_module
from src.core.live_session_signals import (
    FORCE_EXIT_CODE,
    SessionStopRequested,
    SessionStopSignals,
)

pytestmark = pytest.mark.unit


class _NullLog:
    """A structlog-shaped double that records nothing but never raises."""

    def info(self, *args, **kwargs) -> None:
        return None

    def error(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None


@pytest.fixture
def snapshot_handlers():
    """Restore SIGINT/SIGTERM/SIGABRT to whatever they were before the test.

    A test in this file that leaks a handler poisons every later test sharing
    this xdist worker (Testing standards, this story).
    """
    before = {
        sig: signal.getsignal(sig)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGABRT)
    }
    yield
    for sig, handler in before.items():
        signal.signal(sig, handler)


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __call__(self, *args, **kwargs) -> None:
        self.calls.append(("call", args, kwargs))


def _signals(**overrides) -> SessionStopSignals:
    options = {"log": _NullLog(), "on_stop": _Recorder(), "force_exit": _Recorder()}
    options.update(overrides)
    return SessionStopSignals(**options)


class TestImportPurity:
    """The module must stay reachable from a unit-tier test: no Nautilus."""

    #: Exactly what the module imports — no wider (review fix, 2026-08-22).
    #: The old set permitted `asyncio`, `structlog` and `typing` that the
    #: module never imported, so an edit pulling any of them in would have
    #: passed a guard whose whole job is to notice. An allowlist wider than the
    #: module's real surface is not a guard, it is a comment.
    PERMITTED_TOP_LEVEL = frozenset(
        {"os", "signal", "sys", "itertools", "logging", "collections", "typing"}
    )

    def _top_level_imports(self) -> list[str]:
        tree = ast.parse(Path(signals_module.__file__).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module.split(".")[0])
        return imported

    def test_top_level_imports_are_the_permitted_set_only(self):
        offenders = [
            name for name in self._top_level_imports() if name not in self.PERMITTED_TOP_LEVEL
        ]
        assert offenders == [], f"live_session_signals must stay pure, found: {offenders}"

    def test_the_allowlist_is_not_wider_than_the_module(self):
        """Non-vacuity, in the other direction: every permitted name is used.

        A permitted-but-unused entry is a hole — it waves a future import
        through in silence. If this goes red because an import was legitimately
        removed, delete the name from ``PERMITTED_TOP_LEVEL`` too.
        """
        unused = self.PERMITTED_TOP_LEVEL - set(self._top_level_imports())
        assert unused == set(), (
            f"PERMITTED_TOP_LEVEL permits names live_session_signals does not import: {unused} — "
            "narrow the allowlist rather than leaving a hole open"
        )

    def test_importing_the_module_loads_no_nautilus(self):
        code = (
            "import sys, src.core.live_session_signals;"
            "print(','.join(sorted(m for m in "
            "('nautilus_trader', 'sqlalchemy', 'ibapi') if m in sys.modules)))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(signals_module.__file__).parents[2],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == ""


class TestArmingIsIdempotent:
    """``arm()`` twice installs one handler and restores one previous handler."""

    def test_arm_twice_then_restore_once_recovers_the_true_original(self, snapshot_handlers):
        original_int = signal.getsignal(signal.SIGINT)
        original_term = signal.getsignal(signal.SIGTERM)
        original_abrt = signal.getsignal(signal.SIGABRT)
        stop = _signals()
        loop = _FakeLoop()

        stop.arm(loop)
        after_first_int = signal.getsignal(signal.SIGINT)
        stop.arm(loop)

        # `==` rather than `is`: each `self._handle` attribute access creates
        # a fresh bound-method wrapper object, so identity is the wrong tool
        # here — bound methods compare equal when `__self__` and `__func__`
        # match, which is what "the same displacement" actually means.
        assert signal.getsignal(signal.SIGINT) == after_first_int, (
            "a second arm() should re-run the same displacement, not change who is armed"
        )

        stop.restore()

        assert signal.getsignal(signal.SIGINT) is original_int
        assert signal.getsignal(signal.SIGTERM) is original_term
        assert signal.getsignal(signal.SIGABRT) is original_abrt

    def test_a_mutated_unconditional_snapshot_fails_this_test(self, snapshot_handlers):
        """Non-vacuity: prove the test above can fail.

        With the "only snapshot on the first arm" guard removed, a second
        ``arm()`` would re-snapshot *our own* handler and ``restore()`` would
        put that back instead of the true original — a no-op that looks like
        it worked.
        """
        stop = _signals()
        loop = _FakeLoop()
        stop.arm(loop)
        # Simulate the mutation directly: force a second snapshot the way a
        # buggy `arm()` without the `if not self._previous` guard would.
        stop._previous = {
            sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGABRT)
        }
        stop.restore()

        # The mutated code restores OUR OWN handler, not the interpreter's.
        assert signal.getsignal(signal.SIGINT) == stop._handle


class _FakeLoop:
    """The narrowest thing ``arm()`` needs from an event loop."""

    def __init__(self) -> None:
        self.added: list[int] = []
        self.callbacks: list[object] = []

    def add_signal_handler(self, sig, callback, *args) -> None:
        self.added.append(sig)
        self.callbacks.append(callback)
        # Invoked immediately, so a callback that is not harmless to run would
        # be caught here. (The old comment claimed this "never actually fires";
        # it fires on every call — `_noop` is simply harmless. Review fix,
        # 2026-08-22.)
        callback(*args)


class TestTheLoopLevelDisplacement:
    """The ``loop.add_signal_handler`` half of ``arm()``, which nothing asserted.

    Review fix, 2026-08-22. ``_FakeLoop.added`` was recorded and never read by
    a single test, so deleting the whole
    ``for sig in _DISPLACED: loop.add_signal_handler(sig, _noop)`` loop — the
    displacement the module docstring calls load-bearing, and half of the
    "two mechanisms" this module exists for — left the unit suite green.
    """

    def test_arm_displaces_the_loop_callback_for_every_signal_in_displaced(self, snapshot_handlers):
        stop = SessionStopSignals(log=_NullLog(), on_stop=lambda name: None)
        loop = _FakeLoop()

        stop.arm(loop)
        stop.restore()

        assert list(loop.added) == list(signals_module._DISPLACED), (
            "arm() must displace the kernel's loop-level callback for every signal in "
            f"_DISPLACED; got {loop.added}"
        )

    def test_the_displacing_callback_is_the_modules_noop(self, snapshot_handlers):
        """Not an arbitrary callable: it must be inert when the loop runs it."""
        stop = SessionStopSignals(log=_NullLog(), on_stop=lambda name: None)
        loop = _FakeLoop()

        stop.arm(loop)
        stop.restore()

        assert loop.callbacks and all(cb is signals_module._noop for cb in loop.callbacks)

    def test_sigabrt_is_displaced_on_the_loop_but_handed_to_sig_dfl(self, snapshot_handlers):
        """The asymmetry that is easy to undo: SIGABRT must be taken away from
        the kernel at the loop level *and* handed back to the OS at the
        process level — `remove_signal_handler` does not do the latter on
        uvloop.
        """
        stop = SessionStopSignals(log=_NullLog(), on_stop=lambda name: None)
        loop = _FakeLoop()

        stop.arm(loop)
        try:
            assert signal.SIGABRT in loop.added
            assert signal.getsignal(signal.SIGABRT) == signal.SIG_DFL
            assert signal.getsignal(signal.SIGINT) == stop._handle
        finally:
            stop.restore()


class TestTheFirstSignal:
    def test_sets_requested_and_records_the_signal_name(self, snapshot_handlers):
        stop = _signals()
        loop = _FakeLoop()
        stop.arm(loop)
        try:
            assert stop.requested is False
            assert stop.signal_name is None

            stop._handle(signal.SIGINT, None)

            assert stop.requested is True
            assert stop.signal_name == "SIGINT"
            assert stop.count == 1
        finally:
            stop.restore()

    def test_invokes_the_injected_on_stop_callback_exactly_once(self, snapshot_handlers):
        on_stop = _Recorder()
        stop = _signals(on_stop=on_stop)
        loop = _FakeLoop()
        stop.arm(loop)
        try:
            stop._handle(signal.SIGTERM, None)
            assert len(on_stop.calls) == 1
            assert on_stop.calls[0][1] == ("SIGTERM",)
        finally:
            stop.restore()

    def test_the_force_exit_seam_is_not_called_on_the_first_signal(self, snapshot_handlers):
        force_exit = _Recorder()
        stop = _signals(force_exit=force_exit)
        loop = _FakeLoop()
        stop.arm(loop)
        try:
            stop._handle(signal.SIGINT, None)
            assert force_exit.calls == []
        finally:
            stop.restore()


class TestASecondSignal:
    def test_calls_the_injected_force_exit_seam_with_exit_code_one(self, snapshot_handlers):
        force_exit = _Recorder()
        stop = _signals(force_exit=force_exit)
        loop = _FakeLoop()
        stop.arm(loop)
        try:
            stop._handle(signal.SIGINT, None)
            stop._handle(signal.SIGINT, None)

            assert len(force_exit.calls) == 1
            assert force_exit.calls[0][1] == (FORCE_EXIT_CODE,)
        finally:
            stop.restore()

    def test_force_exit_code_is_one_not_a_synthesised_130(self):
        """AR28's table has no 130. Story 1.7's own words: an invented code is
        worse than a documented generic failure.
        """
        assert FORCE_EXIT_CODE == 1

    def test_on_stop_is_not_called_again_on_the_second_signal(self, snapshot_handlers):
        on_stop = _Recorder()
        stop = _signals(on_stop=on_stop)
        loop = _FakeLoop()
        stop.arm(loop)
        try:
            stop._handle(signal.SIGINT, None)
            stop._handle(signal.SIGINT, None)
            assert len(on_stop.calls) == 1
        finally:
            stop.restore()

    def test_a_third_signal_also_forces_exit(self, snapshot_handlers):
        """Every signal past the first is treated the same way — there is no
        third state.
        """
        force_exit = _Recorder()
        stop = _signals(force_exit=force_exit)
        loop = _FakeLoop()
        stop.arm(loop)
        try:
            stop._handle(signal.SIGINT, None)
            stop._handle(signal.SIGTERM, None)
            stop._handle(signal.SIGINT, None)
            assert len(force_exit.calls) == 2
        finally:
            stop.restore()


class TestRaiseIfRequested:
    def test_returns_none_before_any_signal(self, snapshot_handlers):
        stop = _signals()
        assert stop.raise_if_requested() is None

    def test_raises_session_stop_requested_after_a_signal(self, snapshot_handlers):
        stop = _signals()
        loop = _FakeLoop()
        stop.arm(loop)
        try:
            stop._handle(signal.SIGINT, None)
            with pytest.raises(SessionStopRequested):
                stop.raise_if_requested()
        finally:
            stop.restore()


class TestRestore:
    def test_puts_back_sigint_sigterm_and_sigabrt(self, snapshot_handlers):
        original = {
            sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGABRT)
        }
        stop = _signals()
        loop = _FakeLoop()
        stop.arm(loop)

        stop.restore()

        for sig, handler in original.items():
            assert signal.getsignal(sig) is handler

    def test_is_safe_to_call_twice(self, snapshot_handlers):
        stop = _signals()
        loop = _FakeLoop()
        stop.arm(loop)
        stop.restore()
        stop.restore()  # must not raise, must not re-snapshot our own handler

    def test_is_safe_to_call_when_arm_never_ran(self, snapshot_handlers):
        stop = _signals()
        stop.restore()  # must not raise


class TestSessionStopRequestedIsAPlainException:
    def test_is_an_exception_subclass_not_a_base_exception(self):
        assert issubclass(SessionStopRequested, Exception)
