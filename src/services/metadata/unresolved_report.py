"""Derivation for the Story 3.2 unresolved-venue report.

Pure, side-effect-free classification of *why* a ``VENUE_UNRESOLVED`` row lacks
a venue, derived from the persisted ``instrument_metadata`` row alone. The raw
FMP ``exchange`` label is never stored (``normalize_venue`` collapses blank,
ambiguous, and unmapped labels all to ``venue=None``), so the reason is a
deliberately best-effort two-way classification — see the module docstring in
``providers/fmp_provider.py`` for the two paths this discriminates.
"""

from src.db.models.instrument_metadata import InstrumentMetadata


def unresolved_reason(md: InstrumentMetadata) -> str:
    """Return a human-readable reason a ticker's venue is unresolved.

    Discriminates the FMP provider's two ``VENUE_UNRESOLVED`` paths using
    ``asset_type``: ``_degraded`` (unknown ticker / no profile) leaves it
    ``None``, while ``_to_domain`` (profile found, exchange label blank or
    ambiguous) always assigns a concrete ``AssetType``.

    Args:
        md: A persisted ``VENUE_UNRESOLVED`` metadata row.

    Returns:
        A best-effort reason string, provider-agnostic via
        ``md.metadata_provider``.
    """
    provider = md.metadata_provider
    if md.asset_type is None:
        return f"unknown to {provider} — no profile returned"
    return f"{provider} venue label blank, ambiguous (e.g. AMEX), or unmapped"
