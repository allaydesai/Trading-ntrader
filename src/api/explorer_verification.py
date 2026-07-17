"""Explorer accuracy-verification primitives (Story 4.6).

Pure, I/O-free helpers that pin the two accuracy contracts the Data Explorer
must honor before the ETF catalog is trusted for backtesting — no DB, no
Nautilus engine, no route imports (mirrors the primitive-helper shape of
``src/services/firstrate/import_verification.py``).

- :func:`verify_candle_fidelity` proves the chart transform is an **exact**
  identity: the rendered ``Candle`` reproduces the source Nautilus ``Bar``
  OHLCV with no rounding drift and ``time == int(ts_event / 1e9)``. This is the
  catalog↔render link of the verification chain (Story 2.5 already proved
  source↔catalog parity), so "what the operator sees == the external
  reference" holds end-to-end (Story 4.6 AC1).
- :func:`verify_metadata_na_contract` proves the ADR-2 three-state N/A rule
  holds for full / sparse / unresolved-venue rows: no ``display_*`` is ever
  blank, and venue is never the descriptive ``N/A`` sentinel (Story 4.6 AC2).

Both return human-readable issue strings; an empty list means the contract
holds. Nothing here mutates state or reaches the network.
"""

from typing import Any, Mapping, Sequence

from src.api.models.metadata_panel import VENUE_UNRESOLVED_LABEL
from src.models.instrument_metadata import NA_SENTINEL

#: Cap on returned fidelity issues so a fully-drifted series can't produce a
#: giant list; once exceeded a single truncation note is appended.
MAX_FIDELITY_ISSUES = 20

#: The descriptive ``display_*`` props checked for the blank-vs-N/A contract.
#: ``display_venue`` is intentionally excluded — venue has its own distinct
#: state (never the descriptive sentinel) and is checked separately.
_DESCRIPTIVE_DISPLAY_FIELDS = (
    "display_company_name",
    "display_currency",
    "display_asset_type",
    "display_sector",
    "display_industry",
    "display_country",
    "display_ipo_date",
)


def _as_double(value: Any) -> float:
    """Coerce a Nautilus ``Price``/``Quantity`` (or a plain number) to float.

    Mirrors ``import_verification`` coercion so this unit-tests with stub bars.
    """
    as_double: Any = getattr(value, "as_double", None)
    if as_double is not None:
        return float(as_double())
    return float(value)


def _candle_field(candle: Any, name: str) -> Any:
    """Read a candle field from either a ``Candle`` model or a plain dict.

    The component test parses ``bars_json`` into dicts; unit tests pass models.
    """
    if isinstance(candle, Mapping):
        return candle[name]
    return getattr(candle, name)


def verify_candle_fidelity(
    source_bars: Sequence[Any],
    candles: Sequence[Any],
    *,
    max_issues: int = MAX_FIDELITY_ISSUES,
) -> list[str]:
    """Return mismatch strings between source bars and rendered candles.

    Args:
        source_bars: The catalog's Nautilus ``Bar`` objects (source of truth).
        candles: The explorer's rendered candles (``Candle`` models or the
            dicts parsed out of ``bars_json``), same length and order.
        max_issues: Hard cap on returned strings; once exceeded the first
            ``max_issues`` are kept and a truncation note is appended.

    Returns:
        Human-readable mismatch strings; empty when the render is faithful.
        A length mismatch (dropped/extra bar) is reported first and alone.
    """
    if len(candles) != len(source_bars):
        return [f"candle count mismatch: {len(candles)} rendered vs {len(source_bars)} source bars"]

    issues: list[str] = []
    for i, (bar, candle) in enumerate(zip(source_bars, candles)):
        expected = {
            "open": _as_double(bar.open),
            "high": _as_double(bar.high),
            "low": _as_double(bar.low),
            "close": _as_double(bar.close),
        }
        for field, want in expected.items():
            got = float(_candle_field(candle, field))
            if got != want:
                issues.append(f"bar[{i}] {field} drift: rendered {got} != source {want}")

        want_vol = int(_as_double(bar.volume))
        got_vol = int(_candle_field(candle, "volume"))
        if got_vol != want_vol:
            issues.append(f"bar[{i}] volume drift: rendered {got_vol} != source {want_vol}")

        want_time = int(bar.ts_event / 1e9)
        got_time = int(_candle_field(candle, "time"))
        if got_time != want_time:
            issues.append(f"bar[{i}] time drift: rendered {got_time} != source {want_time}")

        if len(issues) >= max_issues:
            issues = issues[:max_issues]
            issues.append(f"... (truncated at {max_issues} fidelity issues)")
            break

    return issues


def verify_metadata_na_contract(panel: Any) -> list[str]:
    """Return N/A-contract violations for an ``EtfMetadataPanel`` (AC2).

    Flags a blank-vs-N/A ambiguity (any descriptive ``display_*`` that is empty
    or ``None`` instead of a real value or the ``N/A`` sentinel), a venue
    mislabeled as the descriptive sentinel, or a ``venue_resolved`` flag that
    disagrees with ``display_venue``. Empty list == the contract holds.
    """
    issues: list[str] = []

    for field in _DESCRIPTIVE_DISPLAY_FIELDS:
        value = getattr(panel, field)
        if value is None or value == "":
            issues.append(f"{field} is blank (blank-vs-N/A ambiguity) — expected a value or N/A")

    display_venue = panel.display_venue
    if display_venue is None or display_venue == "":
        issues.append("display_venue is blank — expected a code or the Unresolved label")
    elif display_venue == NA_SENTINEL:
        issues.append("venue mislabeled as the descriptive N/A sentinel — venue is never N/A")

    resolved = bool(panel.venue_resolved)
    if resolved and display_venue == VENUE_UNRESOLVED_LABEL:
        issues.append("venue_resolved is True but display_venue is the Unresolved label")
    if not resolved and display_venue != VENUE_UNRESOLVED_LABEL:
        issues.append(
            f"venue_resolved is False but display_venue is {display_venue!r} "
            f"(expected {VENUE_UNRESOLVED_LABEL!r})"
        )

    return issues
