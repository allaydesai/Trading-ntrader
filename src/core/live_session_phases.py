"""AR39's startup-phase vocabulary and the record pair each phase emits.

Owns: ``PHASE_SEQUENCE`` — the eight ordered phase names a session start moves
through — and :func:`phase`, the context manager that turns a block of work into
the ``phase=<name> status=started`` / ``status=ok|failed`` pair AR39 specifies.

Does not own: the sequence's *execution* (``src/core/live_session_runner.py``),
the static gate's own decision or its terminal record (``src/core/live_check.py``,
which owns ``gate:static``'s ``ok``/``failed`` line), or the account gate, which
emits **both** halves of ``gate:account`` itself
(``src/core/live_account_gate.py``). Two of the eight names therefore already
have owners, and the runner must not double-log them — see the table in Story
2.5's Dev Notes.

**Framework-free by contract.** Standard library, ``structlog``, and
``src.core.live_check`` — itself AST-tested against every framework import. No
``nautilus_trader`` and no ``sqlalchemy``: this module is imported by
``live_session_runner`` (which may import Nautilus) *and* is meant to stay
readable from anything that may not. ``live_account_gate`` cannot be imported
here at all — it imports ``nautilus_trader`` at its line 18 — which is why
:data:`ACCOUNT_GATE_PHASE` is re-declared below and pinned by an equality test
rather than shared by import.

Known, accepted limit: this module logs *that* a phase started and how it
ended. It makes no claim about what the phase did. ``reconcile`` and ``warmup``
are no-op placeholders in Epic 2 and log ``ok`` having done nothing at all — a
clean phase log is not evidence that reconciliation happened.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from src.core.live_check import GATE_PHASE

#: Must equal ``live_account_gate.STARTUP_PHASE``. Re-declared rather than
#: imported because that module imports ``nautilus_trader``; the equality is
#: pinned by ``test_live_session_phases.py``, which imports it inside the test
#: body. Deliberately **not** compared by identity anywhere: ``"gate:account"``
#: contains a colon, so CPython does not intern it and two separately-compiled
#: literals are never the same object.
ACCOUNT_GATE_PHASE = "gate:account"

#: AR39's ordered startup sequence. Agents must not reorder, merge, skip or
#: invent phases — :func:`phase` refuses a name that is not in here, so an
#: invented one fails at the call rather than producing a plausible log.
#:
#: ``PHASE_SEQUENCE[0]`` **is** ``live_check.GATE_PHASE`` (the same object, by
#: import) so the static gate cannot be renamed in one place only.
PHASE_SEQUENCE: tuple[str, ...] = (
    GATE_PHASE,
    "node:build",
    "node:connect",
    ACCOUNT_GATE_PHASE,
    "reconcile",
    "warmup",
    "subscribe",
    "trading",
)

#: Event name every phase record carries. One name, so an operator greps once;
#: the ``phase`` and ``status`` fields carry the detail.
PHASE_EVENT = "session.phase"


@contextmanager
def phase(log: Any, name: str) -> Iterator[None]:
    """Emit ``started`` before a block of work and ``ok``/``failed`` after it.

    Args:
        log: A bound structlog logger. The runner binds ``session_id`` onto it
            once, so every record this emits carries the correlation id (FR48,
            NFR22, AR41) without this function knowing the session exists.
        name: One of :data:`PHASE_SEQUENCE`.

    Yields:
        ``None`` — the caller's block runs inside the pair.

    Raises:
        ValueError: ``name`` is not an AR39 phase. Checked before the
            ``started`` record, so an invented phase never appears in a log at
            all rather than appearing and then failing.
        BaseException: Whatever the body raised, re-raised unchanged after the
            ``failed`` record. Re-raising is the whole point: AC #2 requires a
            failure to stop the sequence, and a context manager that swallowed
            it would let the next phase run.

    The ``failed`` record carries ``error_type`` and **never** ``str(exc)``:
    adapter and broker error text routinely embeds the account identifier and
    NFR26 admits no exception for a string that arrived from a third party. The
    caller renders the message itself, through
    ``live_check.failure_message``, which knows which exceptions are this
    codebase's own.

    ``BaseException`` rather than ``Exception`` is deliberate. A
    ``KeyboardInterrupt`` landing inside ``node:connect`` must still close the
    phase, or the last thing an operator sees is a phase that started and never
    ended.
    """
    if name not in PHASE_SEQUENCE:
        raise ValueError(
            f"{name!r} is not an AR39 phase. The sequence is fixed at "
            f"{', '.join(PHASE_SEQUENCE)} — see AR39; do not reorder, merge, skip or invent one."
        )

    log.info(PHASE_EVENT, phase=name, status="started")
    try:
        yield
    except BaseException as exc:
        log.error(PHASE_EVENT, phase=name, status="failed", error_type=type(exc).__name__)
        raise
    log.info(PHASE_EVENT, phase=name, status="ok")
