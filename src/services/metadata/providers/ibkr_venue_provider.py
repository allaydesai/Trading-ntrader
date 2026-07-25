"""Resolve a ticker's venue from IBKR's contract database.

FMP's conservative exchange map (ADR-6) leaves most of the ETF universe
``VENUE_UNRESOLVED``: it only maps NASDAQ and NYSE, and FMP's ``AMEX`` label is
ambiguous enough that guessing from it is forbidden. IBKR's contract database
answers the question FMP cannot — it is the same source the Nautilus IB adapter
itself qualifies against, so its ``primaryExchange`` *is* the venue a backtest
would trade under.

This is a narrow, honest sibling of the ``MetadataProvider`` Protocol rather than
an implementation of it. That Protocol is ``resolve(ticker) -> InstrumentMetadata``:
synchronous, and a full descriptive record. IBKR yields a venue, asynchronously,
and nothing else. Satisfying the Protocol would mean fabricating ``N/A`` for every
descriptive field and stamping ``metadata_provider="IBKR"`` over FMP's good data —
destroying provenance to satisfy a type.

Pure classification lives in ``classify``; the I/O is injected. That split is what
makes the interesting part — telling not-found from ambiguous from unparseable —
testable without a Gateway.
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional, Protocol

import structlog
from nautilus_trader.adapters.interactive_brokers.common import IBContract
from nautilus_trader.model.identifiers import InstrumentId

logger = structlog.get_logger(__name__)

#: Max venue length — matches the ``instrument_metadata.venue`` String(20) column.
_MAX_VENUE_LEN = 20

#: Venues we have seen and sanity-checked for US equities/ETFs. Used ONLY to raise
#: an ``unexpected_venue`` flag for human review — never to reject or remap a
#: value. IBKR is the authority here; silently rewriting its answer to something
#: from a familiar list would be exactly the guessed venue ADR-6 forbids.
REVIEWED_VENUES: frozenset[str] = frozenset(
    {
        "ARCA",
        "NASDAQ",
        "NYSE",
        "AMEX",
        "BATS",
        "CBOE",
        "IEX",
        "PINK",
        "MEMX",
        "PSX",
        "LTSE",
        "NYSENAT",
        "DRCTEDGE",
        "EDGEA",
        "PEARL",
    }
)


class VenueOutcome(str, Enum):
    """What IBKR was able to tell us about a ticker."""

    RESOLVED = "resolved"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    ERROR = "error"


@dataclass(frozen=True)
class VenueQualification:
    """One ticker's venue verdict, with everything an operator needs to adjudicate it.

    Attributes:
        ticker: The symbol looked up.
        outcome: Which of the four buckets this landed in.
        venue: The resolved venue code, or ``None`` for any non-RESOLVED outcome.
        primary_exchange: Raw ``primaryExchange`` from the contract, for audit.
        con_id: IBKR's contract id — the stable handle for manual verification.
        candidates: Distinct venues seen when several contracts matched (AMBIGUOUS).
        detail: Human-readable reason, shown in the rejects report.
        attempts: How many requests this took.
        elapsed_s: Wall time spent, so 10s error-200 stalls are visible in the data.
        unexpected_venue: Venue resolved but sits outside ``REVIEWED_VENUES``.
    """

    ticker: str
    outcome: VenueOutcome
    venue: Optional[str] = None
    primary_exchange: Optional[str] = None
    con_id: Optional[int] = None
    candidates: tuple[str, ...] = ()
    detail: str = ""
    attempts: int = 1
    elapsed_s: float = 0.0
    unexpected_venue: bool = False

    @property
    def is_terminal(self) -> bool:
        """True when re-asking IBKR cannot change the answer.

        ERROR is the only retryable outcome — it means the request never resolved,
        not that IBKR gave us a negative answer.
        """
        return self.outcome is not VenueOutcome.ERROR


class VenueQualifier(Protocol):
    """The one capability the backfill runner needs."""

    async def qualify(self, ticker: str) -> VenueQualification: ...


def derive_venue(contract: Any) -> str:
    """Extract the venue from a contract the way the Nautilus IB adapter does.

    Mirrors ``InteractiveBrokersInstrumentProvider.determine_venue_from_contract``
    under its default ``convert_exchange_to_mic_venue=False``. Kept as a one-line
    mirror rather than a call into the provider so this module does not have to
    construct a provider (which needs a live client) just to read a field.

    A SMART-routed request comes back with the *listing* exchange in
    ``primaryExchange`` — that is the whole reason the lookup is worth doing.
    """
    exchange = getattr(contract, "exchange", None)
    primary = getattr(contract, "primaryExchange", None)
    return (primary if exchange == "SMART" else exchange) or ""


def _usable_venue(venue: str, ticker: str) -> Optional[str]:
    """Return the venue if it can survive the round trip to a backtest, else ``None``.

    Rejects values that would fail later and more confusingly: too long for the
    ``venue`` column, or not parseable as a Nautilus ``InstrumentId``.
    """
    if not venue or len(venue) > _MAX_VENUE_LEN:
        return None
    try:
        InstrumentId.from_str(f"{ticker}.{venue}")
    except (ValueError, RuntimeError):
        return None
    return venue


def classify(
    ticker: str,
    details: Optional[list],
    *,
    attempts: int = 1,
    elapsed_s: float = 0.0,
    allowlist: frozenset[str] = REVIEWED_VENUES,
) -> VenueQualification:
    """Turn a raw contract-details response into a verdict (pure).

    The mapping, and why each case is what it is:

    - ``None`` → ERROR. The request resolved neither way (IBKR error 200 or a real
      timeout). Retryable; the caller downgrades to NOT_FOUND once retries run out.
    - ``[]`` → NOT_FOUND. IBKR answered, and the answer was "no such contract".
    - one match → RESOLVED.
    - several matches, one distinct venue → RESOLVED. Multiple contracts differing
      in currency or trading class but agreeing on the listing exchange still pin
      the venue, which is all we are asking.
    - several matches, several venues → AMBIGUOUS. Genuinely undecidable from here;
      the candidates go to the operator rather than being resolved by coin flip.
    """

    def verdict(
        outcome: VenueOutcome,
        detail: str,
        *,
        venue: Optional[str] = None,
        primary_exchange: Optional[str] = None,
        con_id: Optional[int] = None,
        candidates: tuple[str, ...] = (),
        unexpected_venue: bool = False,
    ) -> VenueQualification:
        """Bind the per-call metrics once so each branch states only what differs."""
        return VenueQualification(
            ticker=ticker,
            outcome=outcome,
            venue=venue,
            primary_exchange=primary_exchange,
            con_id=con_id,
            candidates=candidates,
            detail=detail,
            attempts=attempts,
            elapsed_s=elapsed_s,
            unexpected_venue=unexpected_venue,
        )

    if details is None:
        return verdict(VenueOutcome.ERROR, "no response (IBKR error 200 or request timeout)")
    if not details:
        return verdict(VenueOutcome.NOT_FOUND, "IBKR has no matching contract")

    contracts = [getattr(d, "contract", d) for d in details]
    venues = {v for v in (derive_venue(c) for c in contracts) if v}

    if not venues:
        return verdict(
            VenueOutcome.ERROR, f"{len(contracts)} contract(s) but no exchange field set"
        )
    if len(venues) > 1:
        return verdict(
            VenueOutcome.AMBIGUOUS,
            f"{len(contracts)} contracts across {len(venues)} venues",
            candidates=tuple(sorted(venues)),
        )

    raw_venue = venues.pop()
    venue = _usable_venue(raw_venue, ticker)
    if venue is None:
        return verdict(
            VenueOutcome.ERROR,
            f"unusable venue {raw_venue!r} (too long or not a valid InstrumentId)",
        )

    first = contracts[0]
    return verdict(
        VenueOutcome.RESOLVED,
        f"{len(contracts)} contract(s)",
        venue=venue,
        primary_exchange=getattr(first, "primaryExchange", None) or None,
        con_id=getattr(first, "conId", None) or None,
        unexpected_venue=venue not in allowlist,
    )


@dataclass
class IBKRVenueProvider:
    """Qualifies one ticker at a time against IBKR's contract database.

    Args:
        fetch_details: Injected I/O — normally
            ``IBKRHistoricalClient.fetch_contract_details``. Injected rather than
            held as a client so the runner can swap in a reconnected client, and so
            tests need no Gateway.
        currency: Contract currency filter. USD narrows a symbol that also lists
            abroad, which is a common source of spurious ambiguity.
        sec_type: IBKR security type. ETFs are ``STK`` at IBKR.
        allowlist: Venues considered already-reviewed (flagging only).
        max_attempts: Total tries per ticker before an ERROR becomes NOT_FOUND.
    """

    fetch_details: Callable[[IBContract], Awaitable[Optional[list]]]
    currency: str = "USD"
    # Literal, not str: IBContract.secType is a Literal union, so a plain str would
    # let a typo through to a runtime IBKR rejection.
    sec_type: Literal["STK", "CFD", "CMDTY", "IND"] = "STK"
    allowlist: frozenset[str] = REVIEWED_VENUES
    max_attempts: int = 2
    _clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    def build_contract(self, ticker: str) -> IBContract:
        """Build the lookup contract.

        ``exchange="SMART"`` with **no** ``primaryExchange`` hint: supplying one
        (as the bar-fetch paths do) would bias IBKR toward confirming the guess we
        are trying to replace.
        """
        return IBContract(
            secType=self.sec_type,
            symbol=ticker,
            exchange="SMART",
            currency=self.currency,
        )

    async def qualify(self, ticker: str) -> VenueQualification:
        """Resolve one ticker's venue, retrying only the genuinely retryable outcome."""
        started = self._clock()
        result = classify(ticker, None, allowlist=self.allowlist)
        for attempt in range(1, self.max_attempts + 1):
            try:
                details = await self.fetch_details(self.build_contract(ticker))
            except Exception as exc:  # noqa: BLE001 — any client failure is retryable
                logger.warning(
                    "venue_lookup_failed", ticker=ticker, attempt=attempt, error=str(exc)
                )
                result = VenueQualification(
                    ticker=ticker,
                    outcome=VenueOutcome.ERROR,
                    detail=f"{type(exc).__name__}: {exc}",
                    attempts=attempt,
                    elapsed_s=self._clock() - started,
                )
                continue

            result = classify(
                ticker,
                details,
                attempts=attempt,
                elapsed_s=self._clock() - started,
                allowlist=self.allowlist,
            )
            if result.is_terminal:
                return result

        # Retries exhausted on a non-response. IBKR error 200 and a real timeout are
        # indistinguishable here, and both need the same operator adjudication, so
        # record it as NOT_FOUND while keeping the ambiguity visible in `detail`.
        return VenueQualification(
            ticker=ticker,
            outcome=VenueOutcome.NOT_FOUND,
            detail=f"no response after {result.attempts} attempt(s) — likely IBKR error 200",
            attempts=result.attempts,
            elapsed_s=result.elapsed_s,
        )
