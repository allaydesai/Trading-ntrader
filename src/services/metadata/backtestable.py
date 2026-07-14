"""Pure backtestable-universe classification (Story 3.5).

Domain-facing, side-effect-free helpers for the exclude/flag/keep-bars contract:
a ticker is backtestable iff its venue is ``RESOLVED``. The universe is a live
derivation over ``instrument_metadata.resolution_status`` — never a stored flag
and never read off ``catalog_instruments.nautilus_id`` (which the orphan fix in
``instrument_mapper.sync_qualification`` may deliberately keep provisional). The
CLI (``metadata backtestable``) renders these; this module owns only the math.
"""

from dataclasses import dataclass

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
