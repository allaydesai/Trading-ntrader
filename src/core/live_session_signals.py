"""The stop-signal policy: who owns SIGINT/SIGTERM/SIGABRT, and when (Story 2.6).

Owns: :class:`SessionStopSignals`, the object that takes over the process's
signal disposition for the life of a session's ``run()`` and turns the first
``SIGINT``/``SIGTERM`` into one call to an injected callback and the second
into a forced exit; :class:`SessionStopRequested`, the marker
``LiveSessionRunner.run()`` catches to end the startup sequence without
raising; and :data:`FORCE_EXIT_CODE`, AR28's answer for "a second signal
arrived".

Does not own: *when* to arm, re-arm or restore — that is
``src/core/live_session_runner.py``'s wiring (Judgment call #8: the runner
holds the wiring, this module holds the policy). Nor does it own stopping the
node: the injected ``on_stop`` callback does that, through
``loop.call_soon_threadsafe``, never this module calling ``node.stop()``
directly.

**Why two mechanisms, armed twice.** Measured directly against the installed
``nautilus-trader 1.220.0`` while drafting this story (see its Dev Notes,
*Pre-verified findings* #1-#5): the process runs on uvloop, and
``NautilusKernel`` takes the process's SIGINT/SIGTERM/SIGABRT unconditionally
whenever it builds a node. ``loop.add_signal_handler`` alone loses every
signal delivered while the loop is stopped — exactly the window
``node:build``'s synchronous connect attempt spends. ``signal.signal`` alone
is stolen back by Nautilus's own handler on the first signal, while the loop
is running. Only both together, in this order, answered in every window:

.. code-block:: python

    for sig in (SIGINT, SIGTERM, SIGABRT):
        loop.add_signal_handler(sig, _noop)   # displaces the kernel's callback
        signal.signal(SIGINT, self._handle)   # covers the synchronous windows
        signal.signal(SIGTERM, self._handle)
        signal.signal(SIGABRT, signal.SIG_DFL)  # hand the abort back to the OS

Node construction clobbers whatever was armed before it (measured, finding
#3), so :meth:`SessionStopSignals.arm` must be called again immediately after
the node is built — that re-arm is the runner's to make, not this module's.

**What the handler may and may not do.** It runs on the main thread, between
two arbitrary bytecodes, possibly inside third-party code. It is kept to:
increment a counter, stamp the signal name, and call one injected callback —
nothing else. It must never call ``node.stop()`` directly (re-entering
``TradingNode.stop()``'s ``create_task``/``run_until_complete`` branch from a
handler that interrupted the loop's own code is not safe) and must never take
a lock or touch a database.

Known, accepted limit, stated rather than implied: a structlog emission
inside a signal handler is not async-signal-safe in the strict POSIX sense.
This is the risk CPython accepts for every Python-level signal handler; the
alternative — a silent first Ctrl-C — is the defect this module exists to
fix. Because it is not safe, every emission on this path is **guarded**: an
exception raised inside a handler surfaces at an arbitrary bytecode in
whatever code was interrupted, where nothing expects it (review fix,
2026-08-22).

**Standard library only.** No ``nautilus_trader``, and no ``structlog``
either — the logger arrives injected. That is what keeps this module
unit-tier: it can be imported, and its handler exercised, with no event loop
running and no C extension loaded.
"""

import itertools
import logging
import os
import signal
import sys
from collections.abc import Callable
from typing import Any, NoReturn

#: AR28's exit codes have no ``130``. Story 1.7 recorded that "a CLI that
#: invents an exit code outside its own documented table is worse than one
#: that reports a generic failure" — ``1`` is that generic failure, honestly.
FORCE_EXIT_CODE: int = 1

#: Signals this module's ``signal.signal`` handler is installed for. SIGABRT
#: is deliberately excluded here — it is handed back to the OS (SIG_DFL), not
#: to our handler; see :data:`_DISPLACED`.
#:
#: ``SIGHUP`` joined the list at review (2026-08-22, decision D5). It is
#: outside the ACs' literal "SIGINT or SIGTERM", but it is the most likely
#: *involuntary* end of the session this command is designed for: a 6.5-hour
#: foreground run over SSH, whose controlling terminal going away delivers
#: SIGHUP. At ``SIG_DFL`` that killed the process outright — no ``node.stop()``,
#: no ``dispose()``, no ``mark_stopped()``, the row left ``running`` and the
#: broker link dropped without a graceful disconnect. Treated exactly like the
#: other two: a dropped terminal is a stop, not a crash.
_STOP_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)

#: Every signal whose loop-level callback must be displaced so the kernel's
#: own ``_loop_sig_handler`` never runs. SIGABRT is included here (it must be
#: taken away from Nautilus) even though it is not in :data:`_STOP_SIGNALS`
#: (our handler never receives it — the OS does, via SIG_DFL).
_DISPLACED: tuple[signal.Signals, ...] = (*_STOP_SIGNALS, signal.SIGABRT)


class SessionStopRequested(Exception):
    """A stop was requested; the startup sequence must not continue.

    Caught inside ``LiveSessionRunner.run()`` and never propagated further: a
    stop is a success, and the CLI's exit-code table must never see it.
    """


def _noop(*_args: object) -> None:
    """Displaces the kernel's own loop-level signal callback.

    Registered via ``loop.add_signal_handler`` so that, while the loop is
    running, the signal's wakeup dispatch reaches this instead of
    ``NautilusKernel._loop_sig_handler`` — which would otherwise re-arm
    ``SIGINT`` to a no-op of its own and steal the handler back (measured,
    this story's Dev Notes, finding #4). Harmless when it fires: the real
    work happens in the ``signal.signal`` handler installed right after it.
    """


class SessionStopSignals:
    """Own SIGINT/SIGTERM/SIGABRT for the life of one session's ``run()``.

    Args:
        log: Bound structlog logger; used only by the default force_exit.
        on_stop: Called once, with the signal's name, on the first signal.
        force_exit: Called with :data:`FORCE_EXIT_CODE` on every signal after
            the first. Both injected so a test needs no real node, loop or
            ``os._exit``.
    """

    def __init__(
        self,
        *,
        log: Any,
        on_stop: Callable[[str], None],
        force_exit: Callable[[int], NoReturn] | None = None,
    ) -> None:
        self._log = log
        self._on_stop = on_stop
        self._force_exit: Callable[[int], NoReturn] = (
            force_exit if force_exit is not None else self._default_force_exit
        )
        self._previous: dict[signal.Signals, Any] = {}
        self._requested = False
        self._signal_name: str | None = None
        self._count = 0
        #: Hands out 0, 1, 2, … one ordinal per signal. ``next()`` on an
        #: ``itertools.count`` is implemented in C and completes in a single
        #: bytecode, so it cannot be re-entered part-way — which is exactly
        #: what a ``self._count += 1`` read-modify-write can be; see
        #: :meth:`_handle` (review fix, 2026-08-22).
        self._ordinals = itertools.count()

    @property
    def requested(self) -> bool:
        """Whether a stop signal has been seen."""
        return self._requested

    @property
    def signal_name(self) -> str | None:
        """The name of the signal that requested the stop, or ``None``."""
        return self._signal_name

    @property
    def count(self) -> int:
        """How many stop signals have been observed, including the first."""
        return self._count

    def arm(self, loop: Any) -> None:
        """Take SIGINT/SIGTERM/SIGABRT. Idempotent; call again after node:build.

        The snapshot is taken **once**, on the first call only, or a re-arm
        would overwrite it with our own handler and make :meth:`restore` a
        no-op. Order is load-bearing — see the module docstring, finding #4.
        """
        if not self._previous:
            self._previous = {sig: signal.getsignal(sig) for sig in _DISPLACED}
        for sig in _DISPLACED:
            loop.add_signal_handler(sig, _noop)
        for sig in _STOP_SIGNALS:
            signal.signal(sig, self._handle)
        # uvloop does not restore SIG_DFL on `remove_signal_handler` (finding
        # #5); only an explicit `signal.signal` call does.
        signal.signal(signal.SIGABRT, signal.SIG_DFL)

    def rearm_process_handlers(self) -> None:
        """Re-take the process-level handlers **without** an event loop.

        Closing an event loop un-installs every handler it registered:
        ``BaseEventLoop.close()`` calls ``remove_signal_handler`` for each,
        which puts ``SIGINT`` back to ``signal.default_int_handler`` and
        ``SIGTERM``/``SIGHUP`` back to ``SIG_DFL`` (measured). The runner's
        ``finally`` closes the loop inside ``shutdown()`` and then still has
        work to do — ``_finish_record()`` is a Postgres round trip — so
        without this the tail of every teardown ran unprotected: a SIGTERM
        there killed the process outright with the row left ``running``, and a
        SIGINT raised ``KeyboardInterrupt`` out of ``run()`` past
        ``except SessionStopRequested``, making a clean stop print
        ``live start failed:`` (review fix, 2026-08-22).

        No ``loop.add_signal_handler`` half, and none is wanted: there is no
        loop left to dispatch it, and the ``signal.signal`` handler is the one
        that covers a synchronous window anyway. A no-op before the first
        :meth:`arm`, so a teardown that never armed cannot install anything.
        """
        if not self._previous:
            return
        for sig in _STOP_SIGNALS:
            signal.signal(sig, self._handle)
        signal.signal(signal.SIGABRT, signal.SIG_DFL)

    def restore(self) -> None:
        """Put back what was there before the first :meth:`arm`. Idempotent."""
        if not self._previous:
            return
        for sig, handler in self._previous.items():
            signal.signal(sig, handler)
        self._previous = {}

    def raise_if_requested(self) -> None:
        """Raise :class:`SessionStopRequested` once a signal has been seen."""
        if self._requested:
            raise SessionStopRequested(
                f"a stop was requested via {self._signal_name}; the startup sequence must not "
                "continue"
            )

    def _handle(self, signum: int, _frame: object) -> None:
        """The main-thread signal handler — minimal and re-entrancy-safe; see
        the module docstring for what it may and may not do.

        **The branch is claimed atomically** (review fix, 2026-08-22). The
        previous ``self._count += 1`` was a load-add-store: a second signal
        delivered between the load and the store re-entered this handler, read
        the stale ``0``, took the *first-signal* branch, and the outer frame
        then stored its stale ``1`` over the nested one — so the operator's
        second Ctrl-C performed a second graceful stop instead of the force
        exit, and a third signal was needed to escape. ``next()`` on an
        ``itertools.count`` cannot be split that way.

        **The callback is guarded.** This runs between two arbitrary bytecodes
        of whatever the main thread was doing; an exception raised out of here
        surfaces there, where nothing expects it — including, at the worst
        moment, inside the runner's own ``finally``.
        """
        ordinal = next(self._ordinals)
        self._count = ordinal + 1
        name = signal.Signals(signum).name
        if ordinal:
            # `if/else`, not an early `return`: the production `force_exit` is
            # `NoReturn`, but every test injects one that returns, and falling
            # through to the first-signal branch would be wrong for both.
            self._force_exit(FORCE_EXIT_CODE)
        else:
            self._first_signal(name)

    def _first_signal(self, name: str) -> None:
        """Record the stop and hand off to the injected callback, guarded."""
        self._requested = True
        self._signal_name = name
        try:
            self._on_stop(name)
        except BaseException as exc:  # noqa: BLE001 - must never raise into interrupted code
            # `_requested` is already set, so the startup sequence still stops
            # at its next boundary even though the node was never asked to.
            self._safely_report(f"stop callback failed: {type(exc).__name__}")

    def _safely_report(self, detail: str) -> None:
        """Best-effort structlog record that cannot itself raise from a handler."""
        try:
            self._log.error("session.stop_callback_failed", detail=detail)
        except BaseException:  # noqa: BLE001 - reporting a failure must not become one
            pass

    def _default_force_exit(self, code: int) -> NoReturn:
        """Announce the abandonment, then exit uncatchably via ``os._exit``
        — see the module docstring for why not ``sys.exit``. No best-effort
        record write: the row stays ``running``; AC #6's reclaim recovers it.

        **``os._exit`` runs from a ``finally``** (review fix, 2026-08-22).
        Everything above it is I/O that can raise — a ``BrokenPipeError`` when
        the documented ``| tee`` consumer has exited, a reentrant-writer
        ``RuntimeError``, a full disk. A raise there used to skip the exit
        entirely, and permanently: ``_count`` is already ≥ 2, so every later
        signal retook the same failing path and the operator's last-resort
        escape hatch was gone.

        ``logging.shutdown()`` is what flushes the record to disk.
        ``os._exit`` bypasses ``atexit`` and every ``Handler.flush()``, and
        this repo's file sink is a stdlib ``RotatingFileHandler``
        (``src/utils/logging.py:82``) — so without this the post-mortem for the
        one path that leaves the row inconsistent is the line most likely to be
        missing from ``logs/ntrader.log``.
        """
        try:
            self._log.error(
                "session.force_exit",
                exit_code=code,
                reason=(
                    "a second stop signal arrived; abandoning the graceful teardown — the "
                    "session's row is left running and will be reclaimed by a later `live start`"
                ),
            )
            print(
                f"Force exit: a second stop signal arrived; abandoning the teardown (exit {code}).",
                file=sys.stderr,
            )
            sys.stdout.flush()
            sys.stderr.flush()
            logging.shutdown()
        except BaseException:  # noqa: BLE001 - nothing may stand between a signal and the exit
            pass
        finally:
            os._exit(code)
