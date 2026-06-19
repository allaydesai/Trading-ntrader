"""Single source of truth for FMP→Nautilus venue translation (ADR-6).

Maps FMP ``exchange`` short labels to bare Nautilus venue codes (e.g.
``"NASDAQ"``, ``"NYSE"``, ``"ARCA"``). Seeded conservatively with only the two
confident equity venues: routing more tickers to ``VENUE_UNRESOLVED`` is the
SAFE direction — unresolved tickers are surfaced by the Story 3.2 report and
fixed via ``venue_overrides.csv`` (Story 3.3), whereas a wrongly-guessed venue
would silently corrupt a backtest.

``AMEX`` is the load-bearing ambiguous case: FMP labels SPY's exchange ``AMEX``,
but SPY's real Nautilus venue is ``ARCA``; the label covers the ARCA/BATS/
NYSE-American split and must NEVER be guessed. Adding a label requires empirical
FMP verification — do NOT pad the map to reduce the unresolved count.
"""

#: Confidently-mapped FMP exchange labels → bare Nautilus venue codes.
FMP_EXCHANGE_TO_VENUE: dict[str, str] = {"NASDAQ": "NASDAQ", "NYSE": "NYSE"}

#: Labels that are known-ambiguous and must resolve to ``None`` (never guessed).
AMBIGUOUS_LABELS: frozenset[str] = frozenset({"AMEX"})


def normalize_venue(label: str | None) -> str | None:
    """Translate an FMP exchange label to a Nautilus venue code, or ``None``.

    Blank/``None``, ambiguous, and unmapped labels all return ``None`` — there is
    no fallback or default venue (ADR-6, "no guessed default").

    Args:
        label: The FMP ``exchange`` short label (e.g. ``"NASDAQ"``), or ``None``.

    Returns:
        The Nautilus venue code for a confidently-mapped label, else ``None``.
    """
    if not label:
        return None
    key = label.strip().upper()
    if key in AMBIGUOUS_LABELS:
        return None
    return FMP_EXCHANGE_TO_VENUE.get(key)
