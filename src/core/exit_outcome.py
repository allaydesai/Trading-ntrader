"""The connectivity check's outcome vocabulary and AR28's exit-code table.

A leaf module, stdlib only, so an exception class defined **anywhere** in the
codebase — including ``src/db/exceptions.py``, which has no imports of its
own today — can carry the ``exit_outcome``/``operator_safe_message`` markers
this module defines without importing ``live_check`` itself and dragging in
everything *it* imports (``live_gate``, ``structlog``). ``live_check.py``
re-exports everything here, so existing callers keep working unchanged.

Markers are plain class attributes, not a base class: several exception
classes already inherit from other families (``InvalidSessionTransition``
from ``BacktestStorageError``), and a marker base would force multiple
inheritance across four modules. ``classify_failure``/``failure_message``
in ``live_check.py`` find them with ``getattr``, which needs no import of the
class that carries them — only ``type(exc).__mro__``.
"""

from collections.abc import Mapping
from enum import Enum
from typing import ClassVar, Protocol


class LiveCheckOutcome(str, Enum):
    """What a connectivity check concluded."""

    OK = "ok"
    GATE_REFUSED = "gate_refused"
    BROKER_UNREACHABLE = "broker_unreachable"
    CONFIG_ERROR = "config_error"
    INTERRUPTED = "interrupted"
    ERROR = "error"
    #: ``live reconcile`` (Story 4.6) completed and the session's view disagrees
    #: with IBKR's. A finding of a check that ran, not a failure to run one.
    DISCREPANCY = "discrepancy"


#: AR28's exit codes. Named here rather than inlined so the whole table is
#: readable in one place and assertable in one test — scripts branch on these,
#: and `3` in particular is the entire content of FR11.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2  # Click's own default; named so the table below is complete
EXIT_GATE_REFUSED = 3
EXIT_BROKER_UNREACHABLE = 4
#: Story 4.6: distinct from every failure code, so a script can tell "the check
#: ran and found a discrepancy" from "the check could not run".
EXIT_DISCREPANCY = 5

#: Outcome → process exit code. Total over ``LiveCheckOutcome`` by construction,
#: and a test loops the enum to keep it that way: an outcome added without a code
#: must fail a test rather than raise ``KeyError`` in front of an operator.
EXIT_CODES: Mapping[LiveCheckOutcome, int] = {
    LiveCheckOutcome.OK: EXIT_OK,
    LiveCheckOutcome.GATE_REFUSED: EXIT_GATE_REFUSED,
    LiveCheckOutcome.BROKER_UNREACHABLE: EXIT_BROKER_UNREACHABLE,
    LiveCheckOutcome.CONFIG_ERROR: EXIT_ERROR,
    # AR28's table has no 130. A CLI that invents an exit code outside its own
    # documented table is worse than one that reports a generic failure, and an
    # interrupted check proved nothing — which is exactly what `1` says.
    LiveCheckOutcome.INTERRUPTED: EXIT_ERROR,
    LiveCheckOutcome.ERROR: EXIT_ERROR,
    LiveCheckOutcome.DISCREPANCY: EXIT_DISCREPANCY,
}


class ExitOutcomeMarker(Protocol):
    """The shape an exception class carries to classify itself.

    Not used for ``isinstance`` checks — ``classify_failure``/``failure_message``
    read these off each class in an exception's MRO with ``getattr``, which
    needs no import of the class at all. This ``Protocol`` exists purely to
    give the two attributes a documented, checkable shape.
    """

    exit_outcome: ClassVar[LiveCheckOutcome]
    operator_safe_message: ClassVar[bool]
