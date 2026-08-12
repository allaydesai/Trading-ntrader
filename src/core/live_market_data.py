"""Live market-data policy for a paper-trading session.

Owns: resolving the market-data type and RTH restriction a live session must run
on, parsing and validating the bar types it will subscribe to, and checking that
set against the account's market-data line budget. All of it is pure — no I/O, no
actor, no node — so ``live_node_builder`` can run every check before a socket
opens.

Does not own: the node's client configuration (``live_node_builder`` consumes
what this module resolves), the observing actor itself (``live_bar_observer``),
the node's lifecycle (Epic 2's runner, AR38), or any order path — Epic 1 has none.

Two silent fallbacks exist upstream of this module, and closing them is the point
of it (FR3, AR21):

1. ``IBKRSettings.ibkr_market_data_type`` defaults to ``DELAYED_FROZEN`` — right
   for the catalog fetcher it was written for, wrong for a live session — and
   ``get_market_data_type_enum()`` maps *any* unrecognised string to
   ``DELAYED_FROZEN`` with no error (``src/config.py:105-107``). This module
   validates the raw string and never calls that helper.
2. Nautilus treats IB error 10167 ("Requested market data is not subscribed.
   Displaying delayed market data.") as a warning
   (``adapters/interactive_brokers/client/error.py:36``), so a broker-side
   downgrade never reaches configuration at all. That half is the observer's —
   see ``live_bar_observer``'s "Delayed-feed detection".
"""

from collections.abc import Sequence

import structlog
from ibapi.common import MarketDataTypeEnum  # type: ignore[import-untyped]
from nautilus_trader.model.data import BarType

from src.config import IBKRSettings

logger = structlog.get_logger(__name__)

# The only market-data type a live session may run on (FR3, AR21).
REQUIRED_LIVE_MARKET_DATA_TYPE: MarketDataTypeEnum = MarketDataTypeEnum.REALTIME
REQUIRED_LIVE_MARKET_DATA_TYPE_NAME = "REALTIME"

# A worked example, quoted in every parse failure so the operator has the shape.
EXAMPLE_BAR_TYPE = "AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"

# How long after its period ends a bar may still arrive before the feed is
# suspected of being delayed. This is a tolerance for publication latency only —
# the bar interval is subtracted separately, because `ts_event` is the bar's OPEN
# (see live_bar_observer's "Delayed-feed detection"). On a healthy feed a
# completed bar is published as soon as the next bar's first update arrives
# (client/market_data.py:1162-1166), and when nothing arrives the fallback
# completion timeout publishes at bar_duration + 1s
# (client/market_data.py:1048-1073) — so seconds, not minutes. 120s is generous
# against that and far under IBKR's ~900s delayed feed, which is the gap this
# number has to sit inside.
DEFAULT_DELAYED_DATA_GRACE_SECONDS = 120.0

NANOS_PER_SECOND = 1_000_000_000


class LiveMarketDataError(Exception):
    """The live market-data configuration cannot be honoured.

    Deliberately a sibling of ``live_node_builder.LiveNodeConfigError`` rather
    than a subclass: making it one would require this module to import
    ``live_node_builder``, and the dependency runs the other way. Callers that
    map configuration failures to an outcome — the diagnostic probes today,
    Story 1.7's CLI next — must catch both.
    """


def resolve_live_market_data_type(settings: IBKRSettings) -> MarketDataTypeEnum:
    """Return the market-data type a live session must use, or refuse.

    ``ibkr_market_data_type`` exists for historical fetching and defaults to
    ``DELAYED_FROZEN``. A live session overrides that default explicitly (AC #1)
    rather than requiring every operator to add a key to ``.env``. But an
    operator who *asked* for delayed data has said something specific, and
    quietly upgrading them would be its own silent substitution — so an explicit
    non-REALTIME value is refused instead.

    The two cases are told apart by ``model_fields_set``, which pydantic-settings
    populates for an init kwarg and for an environment/``.env`` value alike, and
    leaves empty when the field falls back to its default.

    Raises:
        LiveMarketDataError: The operator explicitly configured a market-data
            type other than REALTIME, including an unrecognised string.
    """
    configured = settings.ibkr_market_data_type
    was_set = "ibkr_market_data_type" in settings.model_fields_set

    if not was_set:
        logger.info(
            "live_market_data.override",
            field="ibkr_market_data_type",
            fetch_default=configured,
            live_value=REQUIRED_LIVE_MARKET_DATA_TYPE_NAME,
            reason="the DELAYED_FROZEN default exists for catalog fetching, not for a session",
        )
        return REQUIRED_LIVE_MARKET_DATA_TYPE

    if configured.strip().upper() == REQUIRED_LIVE_MARKET_DATA_TYPE_NAME:
        return REQUIRED_LIVE_MARKET_DATA_TYPE

    raise LiveMarketDataError(
        f"IBKR_MARKET_DATA_TYPE is {configured!r}, but a live session may only run on "
        f"{REQUIRED_LIVE_MARKET_DATA_TYPE_NAME} market data (FR3). Delayed data would make the "
        "session act on prices roughly 15 minutes old while reporting them as live. "
        f"Set IBKR_MARKET_DATA_TYPE={REQUIRED_LIVE_MARKET_DATA_TYPE_NAME}, or unset it to take "
        "the live default."
    )


def resolve_live_use_rth(settings: IBKRSettings) -> bool:
    """Return the RTH restriction a live session must use, or refuse.

    Bars outside regular trading hours do not match the session basis of the
    bars a strategy was backtested on, which is the whole comparison this phase
    is built to make (FR4, AR21). The default is already ``True``, so only an
    explicit opt-out reaches the refusal.

    Raises:
        LiveMarketDataError: ``ibkr_use_rth`` is False.
    """
    if settings.ibkr_use_rth is not True:
        raise LiveMarketDataError(
            "IBKR_USE_RTH is False, but a live session's bars must be restricted to regular "
            "trading hours (FR4) so they share the session basis of the bars the strategy was "
            "backtested on. Set IBKR_USE_RTH=true, or unset it to take the default."
        )
    return True


def resolve_live_bar_types(raw: Sequence[str]) -> tuple[BarType, ...]:
    """Parse and validate the bar types a session will subscribe to.

    Args:
        raw: Bar-type strings, e.g. ``["AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"]``.

    Returns:
        The parsed bar types, in the order given.

    Raises:
        LiveMarketDataError: An entry does not parse, is internally aggregated,
            is composite, is not time-aggregated, or repeats an earlier entry.
    """
    if isinstance(raw, str):
        # `Sequence[str]` admits a bare str, which would iterate characters and
        # report the useless "cannot subscribe to bar type 'A'".
        raise LiveMarketDataError(
            f"Expected a sequence of bar-type strings, got a single string {raw!r}. "
            f"Wrap it in a list, e.g. [{EXAMPLE_BAR_TYPE!r}]."
        )

    resolved: list[BarType] = []
    seen: set[str] = set()

    for entry in raw:
        try:
            bar_type = BarType.from_str(entry)
        except (ValueError, TypeError) as exc:
            # from_str raises a bare ValueError with the parser's own wording;
            # wrapping it keeps one exception type on this path and lets the
            # message carry a shape the operator can copy.
            raise LiveMarketDataError(
                f"Cannot subscribe to bar type {entry!r}: {exc}. "
                f"Expected the form {EXAMPLE_BAR_TYPE!r}."
            ) from exc

        if not bar_type.is_externally_aggregated():
            raise LiveMarketDataError(
                f"Bar type {entry!r} is INTERNAL-aggregated. An INTERNAL bar type never reaches "
                "the IBKR adapter — Nautilus aggregates it locally from ticks instead, so the "
                "session would run on bars that are not the venue's bars, with no warning. "
                f"Use EXTERNAL, e.g. {EXAMPLE_BAR_TYPE!r}."
            )

        if bar_type.is_composite():
            # `Actor.subscribe_bars` subscribes to `bar_type.standard()`, while a
            # composite bar publishes on the full `...EXTERNAL@...` topic. The
            # topics never match: the session connects, the subscription is
            # accepted, and no bar is ever delivered.
            raise LiveMarketDataError(
                f"Bar type {entry!r} is composite. Nautilus subscribes to its standard form but "
                "publishes composite bars under the full name, so the two never meet and the "
                f"session would receive nothing. Use a plain bar type, e.g. {EXAMPLE_BAR_TYPE!r}."
            )

        if not _is_time_aggregated(bar_type):
            # `spec.timedelta` raises `ValueError: Aggregation not time based` for
            # TICK/VOLUME/VALUE — from inside `on_bar`, after the bar is counted,
            # and from inside the adapter's own subscribe path before that.
            raise LiveMarketDataError(
                f"Bar type {entry!r} is not time-aggregated. The IBKR adapter only streams "
                "time-based bars, and a tick/volume/value aggregation has no bar interval for "
                f"the delayed-feed check to reason about. Use e.g. {EXAMPLE_BAR_TYPE!r}."
            )

        # Keyed on the canonical upper-case form: `BarType.from_str` upper-cases
        # the aggregation but preserves instrument-id case, so `aapl.nasdaq-...`
        # and `AAPL.NASDAQ-...` are the same stream with different keys. Left
        # undeduplicated they burn two market-data lines and leave `load_ids`
        # carrying a lowercase id IBKR will not resolve — a silently dead
        # subscription.
        key = str(bar_type).upper()
        if key in seen:
            raise LiveMarketDataError(
                f"Bar type {entry!r} is subscribed more than once (case-insensitively). Each "
                "subscription consumes one IBKR market-data line, so a duplicate costs a line "
                "and delivers nothing new."
            )
        seen.add(key)
        resolved.append(bar_type)

    return tuple(resolved)


def _is_time_aggregated(bar_type: BarType) -> bool:
    """True when the bar type has a wall-clock interval."""
    try:
        bar_type.spec.timedelta
    except ValueError:
        return False
    return True


def validate_market_data_line_budget(bar_types: Sequence[BarType], *, budget: int) -> None:
    """Refuse a session that would exceed the account's market-data lines.

    One streaming subscription consumes one line. IBKR drops the excess silently
    rather than reporting it, so this has to fail at startup — before a socket
    opens — naming both counts (NFR16, NFR30).

    ``instrument count`` and ``subscription count`` diverge exactly when a
    session subscribes to several timeframes on one instrument, so the message
    reports both rather than letting the operator infer the wrong one.

    Raises:
        LiveMarketDataError: The subscription count exceeds ``budget``.
    """
    requested = len(bar_types)
    if requested <= budget:
        return

    instruments = len(instrument_ids_for(bar_types))
    raise LiveMarketDataError(
        f"This session requests {requested} streaming subscription(s) across {instruments} "
        f"instrument(s), but the account allows {budget} concurrent market-data line(s). "
        "Each subscription consumes one line and IBKR drops the excess without reporting it. "
        "Reduce the subscription set, or raise IBKR_MARKET_DATA_LINES to a figure the account "
        "actually holds."
    )


def instrument_ids_for(bar_types: Sequence[BarType]) -> tuple[str, ...]:
    """Return the distinct instrument ids behind a subscription set, first-seen order.

    These become the instrument provider's ``load_ids``. Without them the IBKR
    data client logs ``instrument not found`` and returns from ``_subscribe_bars``
    (``adapters/interactive_brokers/data.py:248-254``) — no exception, no retry,
    no bars, ever.

    ``load_ids`` narrows that failure mode; it does not remove it.
    ``InteractiveBrokersInstrumentProvider.load_with_return_async`` returns
    ``None`` when a contract will not resolve and ``load_ids_with_return_async``
    simply skips it (``providers.py:243-265``), so an id that IBKR cannot qualify
    still yields a connected session with zero bars for that subscription and no
    error anywhere. The diagnostic probe's zero-bar check is what actually
    catches that; this only removes the case where nobody asked at all.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for bar_type in bar_types:
        instrument_id = str(bar_type.instrument_id)
        if instrument_id not in seen:
            seen.add(instrument_id)
            ordered.append(instrument_id)
    return tuple(ordered)
