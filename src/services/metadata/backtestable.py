"""Pure backtestable-universe classification (Story 3.5).

Domain-facing, side-effect-free helpers for the exclude/flag/keep-bars contract:
a ticker is backtestable iff its venue is ``RESOLVED``. The universe is a live
derivation over ``instrument_metadata.resolution_status`` — never a stored flag.

``catalog_instruments.nautilus_id`` is the *derived* form of that verdict, and
``qualification_sync`` now enforces the equivalence in both directions:

    nautilus_id is non-NULL  ⟺  resolution_status is RESOLVED

so a consumer holding only a catalog row can gate on ``nautilus_id`` without
consulting the status — the two cannot disagree, and ``metadata coverage --gate``
fails if they ever do. What the status still uniquely provides is *why* a ticker is
excluded, which is why ``non_backtestable_reason`` exists: the three ways to be
non-backtestable need three different things from the operator, and telling a user
their delisted instrument has "an unresolved venue" sends them to fix something
that is not broken.

The CLI (``metadata backtestable``) renders these; this module owns only the math.
"""

from dataclasses import dataclass
from typing import Optional

from src.db.models.catalog_instrument import CatalogInstrument
from src.models.instrument_metadata import ResolutionStatus

#: The five per-timeframe bar-count columns summed by ``total_bars``.
_BAR_COUNT_FIELDS = (
    "bar_count_daily",
    "bar_count_hourly",
    "bar_count_minute",
    "bar_count_5min",
    "bar_count_30min",
)


def is_backtestable(status: ResolutionStatus) -> bool:
    """True iff the venue verdict admits the ticker to the backtestable universe.

    Backtestable ⟺ ``RESOLVED`` (venue known). ``VENUE_UNRESOLVED`` (the gate
    blocker) and ``UNRESOLVED`` (never attempted) are both non-backtestable, so
    no venue is ever guessed to admit a ticker.
    """
    return status == ResolutionStatus.RESOLVED


def non_backtestable_reason(status: Optional[ResolutionStatus], ticker: str, catalog: str) -> str:
    """Explain why a ticker is not backtestable, in terms the operator can act on.

    Called only on the failure path — a consumer gates on ``nautilus_id`` (cheap,
    already loaded) and reaches for the status only once it needs to say why. The
    three non-backtestable states are not interchangeable:

    - ``EXCLUDED`` — adjudicated. Nothing to fix; saying "unresolved venue" would
      send the operator hunting for a venue that was already established not to
      exist.
    - ``VENUE_UNRESOLVED`` — the gate blocker. Actionable via the override file.
    - ``UNRESOLVED`` / missing — resolution never ran. A different action again.

    Args:
        status: The ticker's resolution status, or ``None`` if it has no metadata row.
        ticker: Ticker symbol, for the message.
        catalog: Catalog name, for the message.

    Returns:
        A human-readable explanation naming the next action.
    """
    if status == ResolutionStatus.EXCLUDED:
        return (
            f"'{ticker}' in catalog '{catalog}' is excluded from the backtestable "
            "universe by an audited entry in venue_exclusions.csv (no authoritative "
            "source can qualify it). Its bars are retained but it can never be "
            "backtested. Remove its exclusion row and supply a venue to admit it."
        )
    if status == ResolutionStatus.VENUE_UNRESOLVED:
        return (
            f"'{ticker}' in catalog '{catalog}' has an unresolved venue, so it has no "
            "qualified instrument id and is excluded from backtests. Resolve its venue "
            "(venue_overrides.csv, then `ntrader metadata apply-overrides`) to admit it."
        )
    if status == ResolutionStatus.RESOLVED:
        # Only reachable if the invariant broke — qualification sync never ran after
        # the venue was resolved. Say so precisely; it is a one-command fix.
        return (
            f"'{ticker}' in catalog '{catalog}' has a resolved venue but no qualified "
            "instrument id — catalog qualification is out of sync. Run "
            "`ntrader metadata apply-overrides` to converge it."
        )
    return (
        f"'{ticker}' in catalog '{catalog}' has no venue verdict yet (metadata "
        "resolution has not run for it), so it has no qualified instrument id and is "
        "excluded from backtests."
    )


def total_bars(instrument: CatalogInstrument) -> int:
    """Sum the five per-timeframe bar counts (unset/``None`` counts count as 0).

    Evidence that an excluded ticker's imported bars are retained (excluded ≠
    dropped). A freshly-constructed ORM row has ``None`` counts (defaults apply
    at INSERT), so each is coerced to 0.
    """
    return sum(int(getattr(instrument, field) or 0) for field in _BAR_COUNT_FIELDS)


@dataclass(frozen=True)
class NonBacktestableTicker:
    """A flagged non-backtestable ticker + the bars still retained for it.

    Attributes:
        ticker: The excluded ticker (venue unresolved).
        bars_retained: Total imported bars still on disk for it (0 if it has no
            ``catalog_instruments`` row — fresh unresolved, nothing to lose).
    """

    ticker: str
    bars_retained: int

    @property
    def bars_kept(self) -> bool:
        """True when imported bars are retained for this excluded ticker."""
        return self.bars_retained > 0
