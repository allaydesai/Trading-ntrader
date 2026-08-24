"""The connectivity check's vocabulary, exit codes and Layer 1 pre-flight.

Owns: the outcome vocabulary a broker connectivity check can produce, AR28's
exit-code table, the pre-flight Layer 1 gate evaluation *and its log line*, the
report that carries what happened, and the operator-facing rendering of it.

Does not own: driving a node — that is ``src/core/live_check_driver.py``, which
may import Nautilus; the gate's *rules* — that is ``src/core/live_gate.py``,
which this module calls and never modifies; and a session's lifecycle — that is
Epic 2's ``live_session_runner.py`` (AR38).

**Purity is the point.** This module imports only the standard library,
``structlog`` and ``src.core.live_gate``. It is the same split
``live_gate`` (pure decisions) has to ``live_account_gate`` (enforcement seam),
one level up — and it is what lets the exit codes scripts depend on (FR11, AR28)
be tested with no Nautilus, no broker and no event loop.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import structlog

from src.core.live_gate import (
    GateFlags,
    GateMode,
    GateRefusalReason,
    evaluate_gate,
)

if TYPE_CHECKING:
    from src.config import IBKRSettings

logger = structlog.get_logger(__name__)

#: AR28's exit codes. Named here rather than inlined so the whole table is
#: readable in one place and assertable in one test — scripts branch on these,
#: and `3` in particular is the entire content of FR11.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2  # Click's own default; named so the table below is complete
EXIT_GATE_REFUSED = 3
EXIT_BROKER_UNREACHABLE = 4

#: This check's name for the Layer 1 step, mirroring
#: ``live_account_gate.STARTUP_PHASE == "gate:account"`` for Layer 2. Deliberately
#: *not* a claim to AR39's full startup sequence, which is Epic 2's contract.
GATE_PHASE = "gate:static"


class LiveCheckOutcome(str, Enum):
    """What a connectivity check concluded."""

    OK = "ok"
    GATE_REFUSED = "gate_refused"
    BROKER_UNREACHABLE = "broker_unreachable"
    CONFIG_ERROR = "config_error"
    INTERRUPTED = "interrupted"
    ERROR = "error"


class BrokerUnreachableError(Exception):
    """The gate permitted the connection but the broker never answered."""


class LiveCheckError(RuntimeError):
    """The check reached the gateway but the session did not do its job."""


class InvalidCheckWindowError(Exception):
    """A timing argument cannot bound anything.

    ``click.FloatRange`` does not reject ``nan`` — its bound check is a plain
    ``<``/``<=`` comparison and every comparison against NaN is False — and it
    accepts ``inf`` outright. Both defeat the deadlines the driver is built from:
    ``time.monotonic() < nan`` is False on entry, so a wait ends before it
    begins, and ``inf`` makes it never end while the live client id is held.
    """


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
}

#: Exception class name → outcome. Keyed on the **name**, not the class, because
#: importing ``GateRefusedError`` (``live_node_builder``) or
#: ``LiveMarketDataError`` (``live_market_data``) would drag ``nautilus_trader``
#: and ``ibapi` into this module and destroy the purity the split exists for.
#: ``tests/component/core/test_live_check_driver.py`` asserts the real classes
#: still carry these names, so the coupling fails loudly instead of silently
#: reclassifying a gate refusal as a generic error — which would turn exit 3 into
#: exit 1 on the one path FR11 exists for.
_OUTCOME_BY_EXCEPTION_NAME: Mapping[str, LiveCheckOutcome] = {
    # This module's own exceptions are keyed by `__name__` rather than by a
    # literal, so a rename cannot desynchronise them from the map. Only the ones
    # that live behind a Nautilus import are unavoidably strings.
    BrokerUnreachableError.__name__: LiveCheckOutcome.BROKER_UNREACHABLE,
    InvalidCheckWindowError.__name__: LiveCheckOutcome.CONFIG_ERROR,
    "GateRefusedError": LiveCheckOutcome.GATE_REFUSED,
    "LiveNodeConfigError": LiveCheckOutcome.CONFIG_ERROR,
    "LiveMarketDataError": LiveCheckOutcome.CONFIG_ERROR,
    # Story 2.5's three, for `ntrader live start`. All CONFIG_ERROR (exit 1),
    # each for its own reason: AR28 defines 4 as *broker* connectivity and
    # Redis is not the broker — 4 must stay scriptably specific to IBKR; a
    # session-state conflict is neither a gate refusal nor a connectivity
    # failure; and an unknown session name is an operator error, where 2 is
    # Click's own code for misuse of the command line itself.
    #
    # `InvalidSessionTransition` inherits `BacktestStorageError`, and the MRO
    # walk in `classify_failure` matches the specific name first — so a future
    # entry for the base would not silently reclassify this one.
    "RedisUnreachableError": LiveCheckOutcome.CONFIG_ERROR,
    "InvalidSessionTransition": LiveCheckOutcome.CONFIG_ERROR,
    "RecordNotFoundError": LiveCheckOutcome.CONFIG_ERROR,
    "KeyboardInterrupt": LiveCheckOutcome.INTERRUPTED,
    # The socket-level failures, named individually rather than through their
    # shared `OSError` base. Keying on `OSError` was tried and is wrong: it also
    # catches `FileNotFoundError`, `PermissionError` and `IsADirectoryError`, so
    # a log-directory permission failure during node construction would exit 4
    # and tell the operator to go restart a perfectly healthy gateway.
    # - `ConnectionError` covers refused/reset/aborted (gateway not listening)
    # - `gaierror` is `socket.gaierror` — a mistyped `IBKR_HOST`
    # - `TimeoutError` is an `OSError` subclass since 3.10 and is what a socket
    #   that accepts but never answers produces
    # Listed last in intent, not precedence: `classify_failure` walks the MRO,
    # so a more specific name above always wins.
    "ConnectionError": LiveCheckOutcome.BROKER_UNREACHABLE,
    "gaierror": LiveCheckOutcome.BROKER_UNREACHABLE,
    "TimeoutError": LiveCheckOutcome.BROKER_UNREACHABLE,
}

#: Exception names whose ``str()`` is safe to show an operator, because this
#: codebase wrote it. Everything else is third-party text, and
#: ``live_account_gate._stop_node`` documents why that must never be rendered:
#: "adapter and broker error text routinely embeds the account identifier, and
#: NFR26 admits no exception for a string that arrived from a third party."
#: A raw `str(exc)` from the IB adapter really does carry account ids — verified
#: during review, where `Error 321: account DU4076626 is not managed...` printed
#: the identifier straight to the console.
_SAFE_MESSAGE_EXCEPTION_NAMES: frozenset[str] = frozenset(
    {
        BrokerUnreachableError.__name__,
        LiveCheckError.__name__,
        InvalidCheckWindowError.__name__,
        "GateRefusedError",
        "LiveNodeConfigError",
        "LiveMarketDataError",
        # Story 2.5's, for `ntrader live start`. Each was written to be
        # actionable — the Redis host/port and the two remedies; the session's
        # name and its heartbeat's age; the identifier that matched nothing;
        # the two start instants that prove another process took the session —
        # and every one of those strings is this codebase's own, carrying no
        # adapter or broker text. Withholding them would leave the operator a
        # bare type name and make writing them pointless.
        #
        # `SessionReclaimedError` is a **fourth** name here where only three go
        # into the outcome map above: the map is about which exit code
        # describes the failure (a reclaim is a generic error, exit 1, and AR28
        # has no better code), while this set is only about whether the text is
        # ours to show. It is. Flagged for the Epic 2 retro.
        "RedisUnreachableError",
        "InvalidSessionTransition",
        "RecordNotFoundError",
        "SessionReclaimedError",
        # Story 2.7's, for `ntrader live start`. Its message names the specs
        # that failed and points at the `strategy.start_failed` records; every
        # word is this codebase's own, and the *third-party* text — whatever the
        # strategy actually raised — is deliberately not in it. Withholding it
        # would leave the operator a bare type name for a failure whose remedy
        # is entirely in their own spec.
        #
        # This is the **fifth** name here where only three go into the outcome
        # map above, so Story 2.5's Judgment call #8 (*"a fourth typed failure
        # is the moment to revisit the marker protocol"*) is now overdue rather
        # than approaching. Recorded in `deferred-work.md` for the Epic 2 retro;
        # not changed here, because inventing a protocol mid-story is exactly
        # the move that story warned against.
        "NoStrategyStartedError",
    }
)


@dataclass(frozen=True)
class LiveCheckReport:
    """What the check did, in a shape the CLI can render and exit on.

    Every collection field is a ``tuple`` rather than a ``list``/``dict``: the
    dataclass is frozen, and a mutable member would make that a lie.

    Attributes:
        outcome: The conclusion. ``exit_code`` derives from it.
        message: One operator-facing sentence. Already masked (NFR26).
        refusal_reason: The gate's specific failing condition, on a refusal.
        mode: The connection mode the gate established, or None on a refusal —
            a refused attempt has no established mode (see ``GateDecision``).
        accounts: The gateway's accounts, already masked and comma-joined.
        bar_types: The subscriptions the check requested.
        bars_received: Bars the observer actually counted (republishes excluded).
        counts_by_bar_type: Per-subscription counts, as ordered pairs.
        instruments_requested: Instrument ids behind ``bar_types``.
        instruments_loaded: Instrument ids the node's cache actually holds.
        delayed_data_suspected: The observer judged the feed no longer real-time
            and stopped the session. Carried on the report because it is the
            operator's diagnosis — without it a delayed feed and a node that
            died for any other reason look identical.
        shutdown_problems: Anything that went wrong tearing the node down.
            Reported, never raised — a shutdown error must not replace the
            primary outcome with a less useful one.
        elapsed_seconds: Wall-clock duration of the whole check.
    """

    outcome: LiveCheckOutcome
    message: str
    refusal_reason: GateRefusalReason | None = None
    mode: GateMode | None = None
    accounts: str = ""
    bar_types: tuple[str, ...] = ()
    bars_received: int = 0
    counts_by_bar_type: tuple[tuple[str, int], ...] = ()
    instruments_requested: tuple[str, ...] = ()
    instruments_loaded: tuple[str, ...] = ()
    delayed_data_suspected: bool = False
    shutdown_problems: tuple[str, ...] = ()
    elapsed_seconds: float = 0.0

    @property
    def exit_code(self) -> int:
        """The process exit code, derived from the outcome and never stored.

        A stored code can drift out of step with the outcome on a path someone
        forgets to update; a derived one cannot. Same reasoning as
        ``ConnectionMonitor.trading_permitted``.
        """
        return EXIT_CODES[self.outcome]

    @property
    def ok(self) -> bool:
        """True only for a clean, complete check."""
        return self.outcome is LiveCheckOutcome.OK

    @property
    def instruments_missing(self) -> tuple[str, ...]:
        """Requested instruments the node's cache does not hold.

        A contract IBKR would not qualify is *skipped* rather than reported
        (``providers.py:243-265``), so this difference is the only visible trace
        of the single most common cause of "connected, zero bars, no error".
        """
        loaded = set(self.instruments_loaded)
        return tuple(name for name in self.instruments_requested if name not in loaded)


def preflight_gate(
    settings: "IBKRSettings",
    cli_flags: GateFlags | None,
) -> LiveCheckReport | None:
    """Run Layer 1 before anything is constructed, and log what it decided.

    Returns ``None`` when the connection is permitted, so the caller proceeds.
    Returns a refusing report otherwise — before any node, loop, socket or
    client config exists, which is what makes AC #2's "no connection attempt"
    an *assertable* property rather than merely an emergent one.

    **This is additive defence, never a replacement.**
    ``build_trading_node_config`` still runs the gate itself and that remains the
    load-bearing control (AR13, NFR27). Do not remove the builder's gate, and do
    not let this become the only one.

    It also closes a gap carried from Stories 1.3 and 1.5: ``live_gate`` contains
    zero logging calls, so until now a Layer 1 refusal — the single most
    operationally interesting event the safety gate produces — left no trace at
    all. ``gate.refused`` is AR41's event name and is emitted at **error** by
    ``live_account_gate`` for Layer 2; the level is matched deliberately so an
    operator grepping one event never has to grep two levels.

    Args:
        settings: Loaded IBKR settings. Injected, never fetched.
        cli_flags: Operator declarations from the command line. ``None`` means no
            declaration was made, identical to ``GateFlags()``.
    """
    decision = evaluate_gate(settings, GateFlags() if cli_flags is None else cli_flags)

    if decision.permitted:
        logger.info(
            "gate.static",
            phase=GATE_PHASE,
            status="ok",
            mode=decision.mode.value if decision.mode else None,
        )
        return None

    refusal = decision.refusal
    if refusal is None:  # pragma: no cover - evaluate_gate always populates it
        # Checked rather than asserted: `python -O` strips asserts, and losing
        # this one would turn a gate refusal into an AttributeError on the
        # safety-critical path.
        #
        # Reported as GATE_REFUSED, not ERROR: the gate said no, and that is the
        # fact a script branching on exit 3 needs. Downgrading it to a generic
        # failure because *we* could not explain it would hide a refusal from the
        # one control built to make refusals visible (FR11).
        logger.error(
            "gate.refused",
            phase=GATE_PHASE,
            status="failed",
            reason=None,
            message="the gate refused but supplied no reason",
        )
        return LiveCheckReport(
            outcome=LiveCheckOutcome.GATE_REFUSED,
            message=(
                "The safety gate refused the connection but supplied no reason; refusing to "
                "continue against an unexplained refusal."
            ),
        )

    logger.error(
        "gate.refused",
        phase=GATE_PHASE,
        status="failed",
        reason=refusal.reason.value,
        message=refusal.message,
    )
    return LiveCheckReport(
        outcome=LiveCheckOutcome.GATE_REFUSED,
        message=refusal.message,
        refusal_reason=refusal.reason,
    )


def classify_failure(exc: BaseException) -> LiveCheckOutcome:
    """Map a raised exception to the outcome whose exit code describes it.

    Walks the MRO so a subclass classifies as its base, and defaults to
    ``ERROR``. Matching is by class **name** — see
    ``_OUTCOME_BY_EXCEPTION_NAME`` for why, and for what pins it.
    """
    for klass in type(exc).__mro__:
        outcome = _OUTCOME_BY_EXCEPTION_NAME.get(klass.__name__)
        if outcome is not None:
            return outcome
    return LiveCheckOutcome.ERROR


@dataclass
class CheckEvidence:
    """What a check observed, accumulated as it goes.

    Mutable, unlike the frozen :class:`LiveCheckReport` it becomes: the driver
    fills it in from several places, including a ``finally`` that must record
    what was seen even when the step after it raised. Pure and Nautilus-free —
    it lives here rather than in the driver so that turning evidence into a
    report is unit-testable, which is where the exit code is decided.
    """

    bar_types: tuple[str, ...] = ()
    instruments_requested: tuple[str, ...] = ()
    instruments_loaded: tuple[str, ...] = ()
    accounts: str = ""
    mode: GateMode | None = None
    bars_received: int = 0
    counts_by_bar_type: tuple[tuple[str, int], ...] = ()
    delayed_data_suspected: bool = False
    shutdown_problems: tuple[str, ...] = ()


def failure_message(exc: BaseException) -> str:
    """The operator-facing text for a raised failure, never empty, never leaky.

    Only this codebase's own exception messages are shown verbatim. Anything
    else — an adapter error, a broker error, an unexpected builtin — is reported
    by **type name only**, because third-party error text routinely embeds the
    account identifier and NFR26 admits no exception for a string that arrived
    from a third party. ``live_account_gate._stop_node`` takes the same posture
    for the same reason.
    """
    for klass in type(exc).__mro__:
        if klass.__name__ in _SAFE_MESSAGE_EXCEPTION_NAMES:
            return str(exc) or klass.__name__
    return (
        f"{type(exc).__name__} was raised while running the command. Its message is not shown "
        "because third-party error text can carry the account identifier; see the logs for "
        "the full trace."
    )


def success_message(evidence: CheckEvidence) -> str:
    """One sentence describing a check that completed."""
    if evidence.bars_received:
        return (
            f"gate passed, account verified, {evidence.bars_received} bar(s) received on "
            f"{len(evidence.bar_types)} subscription(s), disconnected cleanly"
        )
    return (
        "gate passed, account verified, subscriptions accepted — but no bars closed during "
        "the observation window. Outside regular trading hours this is expected "
        "(use_rth=True means no bar closes); inside them, check the market-data entitlement "
        "and the instrument shortfall above"
    )


def build_report(
    evidence: CheckEvidence,
    outcome: LiveCheckOutcome,
    message: str,
    elapsed_seconds: float,
    exc: BaseException | None = None,
) -> LiveCheckReport:
    """Freeze the accumulated evidence into the report the CLI exits on."""
    refusal = getattr(exc, "refusal", None)
    return LiveCheckReport(
        outcome=outcome,
        message=message,
        refusal_reason=getattr(refusal, "reason", None),
        mode=evidence.mode,
        accounts=evidence.accounts,
        bar_types=evidence.bar_types,
        bars_received=evidence.bars_received,
        counts_by_bar_type=evidence.counts_by_bar_type,
        instruments_requested=evidence.instruments_requested,
        instruments_loaded=evidence.instruments_loaded,
        delayed_data_suspected=evidence.delayed_data_suspected,
        shutdown_problems=evidence.shutdown_problems,
        elapsed_seconds=elapsed_seconds,
    )


def render_report(report: LiveCheckReport) -> str:
    """Render the report as the operator-facing summary block.

    Contains no unmasked account: ``report.accounts`` is masked at construction,
    and ``report.message`` comes from ``GateRefusal`` (masked by ``live_gate``),
    from this story's own strings, or from :func:`failure_message`, which shows
    only type names for third-party exceptions (NFR26).
    """
    lines = [
        f"live check: {report.outcome.value} (exit code {report.exit_code})",
        f"  {report.message}",
    ]
    if report.refusal_reason is not None:
        lines.append(f"  refusal reason: {report.refusal_reason.value}")
    if report.mode is not None:
        lines.append(f"  mode: {report.mode.value}")
    if report.accounts:
        lines.append(f"  accounts: {report.accounts}")
    if report.bar_types:
        lines.append(f"  subscriptions: {', '.join(report.bar_types)}")
        counts = ", ".join(f"{name}={count}" for name, count in report.counts_by_bar_type)
        lines.append(
            f"  bars received: {report.bars_received}" + (f" ({counts})" if counts else "")
        )
    missing = report.instruments_missing
    if missing:
        lines.append(
            f"  instruments NOT loaded: {', '.join(missing)} — IBKR did not qualify these "
            "contracts, so their subscriptions can never deliver a bar"
        )
    if report.delayed_data_suspected:
        lines.append(
            "  DELAYED DATA SUSPECTED — the observer measured bars arriving too late for a "
            "real-time feed and stopped the session"
        )
    if report.shutdown_problems:
        lines.append(f"  shutdown problems: {'; '.join(report.shutdown_problems)}")
    lines.append(f"  elapsed: {report.elapsed_seconds:.2f}s")
    return "\n".join(lines)
