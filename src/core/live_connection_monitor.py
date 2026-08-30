"""Broker connection state and the trading-permission flag it derives.

Owns: the connection state machine, the ``trading_permitted`` flag, and the
four events that make a connection's life legible in the log —
``connection.lost``, ``connection.restored``, ``connection.halted``,
``connection.recovery_refused``.

Does not own: *taking* the reading (``read_ibkr_connection_status`` in
``src/core/live_connection_probe.py`` does; its lookup key is the exact
``(host, port, client_id)`` triple ``live_node_builder`` configures, so the two
must be kept in step by hand), the poll schedule (Epic 2's runner —
AR38), re-establishing state after a reconnect (Epic 4's reconciliation), or
consuming the flag on an order path (Epic 3 — Epic 1 has no order path at all,
which is the point: the control ships before the capability it constrains).

Deliberately framework-free — standard library plus ``structlog``, nothing
else. That purity is what keeps the state machine in the unit tier, and it
mirrors ``src/core/live_gate.py``'s contract.

Three design rules earn their keep, and all three were settled by code review
after the first implementation shipped without them:

**Recovery is two steps, and the second step carries its own reading.** A
reconnected socket alone never restores trading permission (NFR10): the
adapter's own reconnect path re-establishes the socket and finishes its API
handshake *before* it resubscribes (``client/connection.py:111-115`` —
``_reset()`` then ``_resume()``), so there is a window in which the connection
is up and nothing has been checked against the broker's authoritative view.
``confirm_state_reestablished(status)`` therefore takes a ``ConnectionStatus``
and folds it in as an observation first: the grant is atomic with a live
reading, and a socket that died between the caller's last poll and its
confirmation cannot be confirmed. This is the seam Epic 4 will call once
reconciliation exists.

**Unavailability is anchored, not restarted.** ``_unavailable_since`` is set
once when trading permission is withdrawn and cleared only by a *successful*
confirmation. A gateway that flaps — up for one poll, down for the next —
therefore accumulates one continuous outage instead of resetting the halt clock
on every blip, and the halt deadline is evaluated on **every** observation
(including while ``RECOVERING``), so an outage whose socket came back but whose
state was never re-established still halts rather than stalling silently.

**Permission expires.** ``trading_permitted`` is false once the last observation
is older than ``max_observation_age_seconds``. Without that, a poll loop that
dies leaves permission granted forever through a dead socket — the one shape
that no sequence of *observations* can produce, and therefore the one an
observation-driven state machine cannot otherwise defend against.
"""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

import structlog

# NFR4: a transient disconnect should reconnect inside a minute. A constructor
# default rather than a setting: adding an env var would ripple into
# .env.example, README.md and docs/setup/IBKR_SETUP.md for a value the NFR
# already fixes. Epic 2's runner can pass a different window if it needs one.
DEFAULT_RECONNECT_WINDOW_SECONDS = 60.0

# Deliberately the same number, and derived from it rather than invented: if we
# have heard nothing for as long as we would tolerate a complete outage, we are
# in an outage we simply cannot see. Tying the two together also means a runner
# that polls anywhere near the reconnect window stays comfortably inside it.
DEFAULT_MAX_OBSERVATION_AGE_SECONDS = DEFAULT_RECONNECT_WINDOW_SECONDS


class ConnectionState(str, Enum):
    """Where the broker connection stands, from this process's point of view."""

    AWAITING_CONNECTION = "awaiting_connection"
    CONNECTED = "connected"
    RECOVERING = "recovering"
    LOST = "lost"
    HALTED = "halted"


@dataclass(frozen=True)
class ConnectionStatus:
    """One reading of the broker connection.

    Attributes:
        connected: Whether the broker connection was live at the moment of the
            reading. Fail-closed by construction — a reader that cannot tell
            reports ``False``, because withholding permission is the safe
            direction to be wrong in.
        detail: Why, in a few words. Carried into the log line so an operator
            can tell "no client registered" from "socket down" without a
            debugger. Never branched on — it is prose, not a code.
    """

    connected: bool
    detail: str


def _require_positive(name: str, value: float) -> float:
    """Reject a non-finite or non-positive interval at construction time.

    ``nan`` is the dangerous one: every ``>`` comparison against it is ``False``,
    so a ``nan`` window silently disables the halt forever rather than failing.
    Mirrors ``_validate_timeouts`` in ``live_node_builder.py``, which rejects
    the same class of input for the same reason.
    """
    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            f"{name} must be a finite positive number of seconds, got {value!r}. "
            "A non-positive or non-finite value disables the safety deadline it governs."
        )
    return value


class ConnectionMonitor:
    """Tracks broker connectivity and decides whether trading is permitted.

    Polled, not subscribed: at nautilus-trader 1.220.0 an IBKR connection drop
    publishes no message-bus event and changes no public connection property.
    Call :meth:`observe` on a fixed interval — both the halt deadline and the
    permission's own expiry are measured from observations.

    Neither :meth:`observe` nor :meth:`confirm_state_reestablished` raises. A
    monitor that threw into a runner's poll loop would turn a survivable
    disconnect into a dead session, which is the opposite of NFR19 and NFR20.
    The constructor *does* raise on unusable arguments — that is construction
    time, not the poll loop.

    Args:
        session_id: Bound to every event this monitor emits (FR6, AR41).
            Required and non-empty: Epic 2 owns *deriving* session identity
            (AR10), this only validates one it is handed.
        reconnect_window_seconds: How long trading may be withheld before the
            session halts rather than merely pausing (NFR4, NFR20).
        max_observation_age_seconds: How stale the last reading may be before
            permission lapses regardless of state (NFR10).
        time_source: Monotonic seconds. Injected so tests advance it exactly
            rather than sleep. Backward steps are clamped, never trusted.
    """

    def __init__(
        self,
        *,
        session_id: str,
        reconnect_window_seconds: float = DEFAULT_RECONNECT_WINDOW_SECONDS,
        max_observation_age_seconds: float = DEFAULT_MAX_OBSERVATION_AGE_SECONDS,
        time_source: Callable[[], float] = time.monotonic,
    ) -> None:
        if not session_id.strip():
            raise ValueError(
                "ConnectionMonitor requires a non-empty session_id: it is bound to every "
                "connection event, and an empty one silently defeats the session-scoped "
                "correlation FR6 and AR41 exist to provide."
            )
        self._session_id = session_id
        self._reconnect_window_seconds = _require_positive(
            "reconnect_window_seconds", reconnect_window_seconds
        )
        self._max_observation_age_seconds = _require_positive(
            "max_observation_age_seconds", max_observation_age_seconds
        )
        self._now = time_source
        self._state = ConnectionState.AWAITING_CONNECTION
        self._unavailable_since: float | None = None
        self._reconnected_at: float | None = None
        self._last_observed_at: float | None = None
        self._has_ever_connected = False
        self._halt_reported = False
        self._log = structlog.get_logger(__name__).bind(session_id=session_id)

    @property
    def state(self) -> ConnectionState:
        """The current connection state."""
        return self._state

    @property
    def trading_permitted(self) -> bool:
        """Whether trading is permitted right now.

        Derived, never stored — a stored boolean drifts out of step with the
        state machine on the one path someone forgets to update. Two conditions,
        both necessary: the state is ``CONNECTED``, **and** the reading that put
        it there is not stale. The staleness half is what makes a dead poll loop
        fail closed instead of leaving permission granted forever.
        """
        return self._state is ConnectionState.CONNECTED and not self.observation_is_stale

    @property
    def submission_withheld(self) -> bool:
        """Whether Story 3.2's order path should suppress submission right now.

        **Not** :attr:`trading_permitted`, and the difference is the whole
        point (Story 3.2, AC #4). That flag is ``False`` for the entire life
        of every session today, by design: its only grant path,
        ``confirm_state_reestablished()``, is deliberately never called in
        production until Epic 4 has a real reconciliation to follow
        (``live_session_steady_state.py:345-348``). Gating order submission
        on it would suppress every order this phase ever proves, including
        this story's own target fill.

        This predicate answers a narrower question instead: is the
        connection *known lost or unobserved*? It is derived, never stored,
        from the same two facts as ``trading_permitted`` — state and
        staleness — but draws the line one state earlier: ``RECOVERING`` with
        a fresh observation does **not** withhold, because that is this
        phase's permanent healthy steady state (Epic 1 retro Action Item #7
        keeps the grant Epic 4's). NFR10's other half — no orders while
        reconciliation is incomplete — is therefore not enforced by this
        property; it is Epic 4's by phase design.

        Closed form: ``(state not in {CONNECTED, RECOVERING}) or
        observation_is_stale``.
        """
        return (
            self._state not in (ConnectionState.CONNECTED, ConnectionState.RECOVERING)
        ) or self.observation_is_stale

    @property
    def observation_is_stale(self) -> bool:
        """Whether the last reading is too old to act on (or absent entirely)."""
        age = self.observation_age_seconds
        return age is None or age > self._max_observation_age_seconds

    @property
    def observation_age_seconds(self) -> float | None:
        """Seconds since the last reading, or None if nothing has been observed."""
        if self._last_observed_at is None:
            return None
        return self._elapsed_since(self._last_observed_at)

    @property
    def downtime_seconds(self) -> float | None:
        """Seconds since trading permission was withdrawn, or None if it is not.

        Measures *unavailability*, not disconnection: it keeps running through
        ``RECOVERING``, because a socket that is back but whose state has not
        been re-established is still not something to trade on. Use
        :attr:`reconnect_seconds` for the socket's own recovery time.
        """
        if self._unavailable_since is None:
            return None
        return self._elapsed_since(self._unavailable_since)

    @property
    def reconnect_seconds(self) -> float | None:
        """Seconds from the loss to the socket coming back, if it has.

        This is NFR4's quantity — reconnection time — as distinct from
        :attr:`downtime_seconds`, which also includes however long
        re-establishing state takes (NFR5 allows reconciliation 30s).
        """
        if self._unavailable_since is None or self._reconnected_at is None:
            return None
        return max(0.0, self._reconnected_at - self._unavailable_since)

    def observe(self, status: ConnectionStatus) -> ConnectionState:
        """Fold one reading into the state machine and return the new state."""
        self._last_observed_at = self._now()

        if status.connected:
            self._observe_connected()
        else:
            self._observe_disconnected(status)

        self._check_halt_deadline()
        return self._state

    def confirm_state_reestablished(self, status: ConnectionStatus) -> ConnectionState:
        """Grant trading permission — the second half of recovery.

        Called by whoever re-established state against the broker (Epic 4's
        reconciliation; in Epic 2's runner, the ``trading`` phase of AR39's
        sequence). The ``status`` argument is mandatory and is folded in as a
        fresh observation first, so the grant is atomic with a live reading: a
        socket that died since the caller's last poll refuses here rather than
        buying a whole poll interval of blind trading.
        """
        state = self.observe(status)
        if state is ConnectionState.CONNECTED:
            return state

        if state is not ConnectionState.RECOVERING:
            self._log.warning("connection.recovery_refused", state=state.value)
            return state

        self._grant_permission()
        return self._state

    def _grant_permission(self) -> None:
        """Move to CONNECTED, reporting the outage this closes (if any)."""
        downtime = self.downtime_seconds
        reconnect = self.reconnect_seconds

        self._state = ConnectionState.CONNECTED
        self._has_ever_connected = True

        # Only a prior loss can be "restored"; the first connection of a session
        # restores nothing, and its narration is the runner's startup phase
        # sequence (AR39), not this module's business.
        if downtime is not None:
            measured = reconnect if reconnect is not None else downtime
            self._log.info(
                "connection.restored",
                downtime_seconds=downtime,
                reconnect_seconds=reconnect,
                within_reconnect_window=measured <= self._reconnect_window_seconds,
                reconnect_window_seconds=self._reconnect_window_seconds,
            )

        self._unavailable_since = None
        self._reconnected_at = None
        self._halt_reported = False

    def _observe_connected(self) -> None:
        """A live reading moves to RECOVERING — never straight to permitted."""
        if self._state in (ConnectionState.CONNECTED, ConnectionState.RECOVERING):
            return

        self._state = ConnectionState.RECOVERING
        if self._unavailable_since is not None and self._reconnected_at is None:
            self._reconnected_at = self._now()

    def _observe_disconnected(self, status: ConnectionStatus) -> None:
        """A dead reading withdraws permission; the deadline check does the rest."""
        if self._state is ConnectionState.AWAITING_CONNECTION:
            # Nothing was lost yet. "Gate passed but the broker was never
            # reachable" is a startup failure (Story 1.7's exit code 4), and
            # calling it a lost connection starts a clock measuring nothing.
            return

        if self._state is ConnectionState.RECOVERING and not self._has_ever_connected:
            # Still trying to establish the *first* connection: a half-up socket
            # that drops again has lost nothing either. Same reasoning as above,
            # applied to the case the AWAITING_CONNECTION guard alone misses.
            self._state = ConnectionState.AWAITING_CONNECTION
            self._reconnected_at = None
            return

        if self._state in (ConnectionState.CONNECTED, ConnectionState.RECOVERING):
            self._state = ConnectionState.LOST
            self._reconnected_at = None
            if self._unavailable_since is None:
                # Anchored once per outage, not per entry to LOST — a flapping
                # gateway must accumulate one continuous outage rather than
                # restart the halt clock on every blip. Cleared only by a
                # successful confirmation, which is also what makes
                # `connection.lost` fire exactly once per outage.
                self._unavailable_since = self._now()
                self._log.warning(
                    "connection.lost",
                    detail=status.detail,
                    state=self._state.value,
                )

    def _check_halt_deadline(self) -> None:
        """Halt once the outage outlives the window, wherever the state sits.

        Evaluated on every observation, including while ``RECOVERING``: an
        outage whose socket returned but whose state was never re-established is
        still an outage, and leaving it un-deadlined was a silent permanent
        stall. ``_halt_reported`` latches so the halt is reported once and does
        not re-fire on every subsequent poll — which is also what keeps a halt
        escapable through the ordinary two-step path rather than a dead end.
        """
        if self._halt_reported or self._unavailable_since is None:
            return

        downtime = self._elapsed_since(self._unavailable_since)
        if downtime <= self._reconnect_window_seconds:
            return

        self._state = ConnectionState.HALTED
        self._halt_reported = True
        self._log.error(
            "connection.halted",
            downtime_seconds=downtime,
            reconnect_seconds=self.reconnect_seconds,
            reconnect_window_seconds=self._reconnect_window_seconds,
        )

    def _elapsed_since(self, marker: float) -> float:
        """Seconds since ``marker``, clamped at zero.

        ``time_source`` is injected and cannot be assumed monotonic — an NTP
        step or a host suspend can hand back a smaller number. A backward step
        may shorten a *report*; it must never produce a negative age that makes
        a stale reading look fresh or an overrun outage look inside its window.
        """
        return max(0.0, self._now() - marker)
