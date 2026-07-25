"""Unit tests for IBKR venue qualification.

The value of this module is entirely in telling four outcomes apart — resolved,
not-found, ambiguous, error — because Nautilus's own ``request_instruments`` path
collapses all four into an empty list. These tests pin that discrimination, since
getting it wrong either sends resolvable tickers to manual research or lets an
ambiguous one be silently resolved by whichever contract came back first.
"""

from dataclasses import dataclass

import pytest

from src.services.metadata.providers.ibkr_venue_provider import (
    REVIEWED_VENUES,
    IBKRVenueProvider,
    VenueOutcome,
    classify,
    derive_venue,
)


@dataclass
class FakeContract:
    """Minimal stand-in for the ibapi Contract carried by IBContractDetails."""

    symbol: str = "AAA"
    exchange: str = "SMART"
    primaryExchange: str = "ARCA"  # noqa: N815 — mirrors the ibapi attribute name
    conId: int = 12345  # noqa: N815 — mirrors the ibapi attribute name


@dataclass
class FakeDetails:
    """Stand-in for IBContractDetails, which wraps a contract."""

    contract: FakeContract


def _details(*contracts: FakeContract) -> list[FakeDetails]:
    return [FakeDetails(contract=c) for c in contracts]


@pytest.mark.unit
class TestDeriveVenue:
    """The venue field mirrors the Nautilus adapter's own derivation."""

    def test_smart_routed_uses_primary_exchange(self):
        """The whole reason for the lookup: SMART returns the listing exchange."""
        assert derive_venue(FakeContract(exchange="SMART", primaryExchange="ARCA")) == "ARCA"

    def test_direct_routed_uses_exchange(self):
        assert derive_venue(FakeContract(exchange="NASDAQ", primaryExchange="")) == "NASDAQ"

    def test_missing_fields_yield_empty_string(self):
        assert derive_venue(FakeContract(exchange="SMART", primaryExchange="")) == ""


@pytest.mark.unit
class TestClassify:
    """The four-way discrimination, which the Nautilus provider path destroys."""

    def test_none_is_a_retryable_error_not_a_negative_answer(self):
        result = classify("AAA", None)
        assert result.outcome is VenueOutcome.ERROR
        assert result.is_terminal is False
        assert "200" in result.detail

    def test_empty_list_is_not_found(self):
        result = classify("AAA", [])
        assert result.outcome is VenueOutcome.NOT_FOUND
        assert result.is_terminal is True

    def test_single_contract_resolves(self):
        result = classify("AAA", _details(FakeContract(primaryExchange="ARCA", conId=99)))
        assert result.outcome is VenueOutcome.RESOLVED
        assert result.venue == "ARCA"
        assert result.con_id == 99
        assert result.primary_exchange == "ARCA"

    def test_several_contracts_agreeing_on_venue_resolve(self):
        """Contracts can differ in trading class yet still pin the listing exchange."""
        result = classify(
            "AAA",
            _details(
                FakeContract(primaryExchange="ARCA", conId=1),
                FakeContract(primaryExchange="ARCA", conId=2),
                FakeContract(primaryExchange="ARCA", conId=3),
            ),
        )
        assert result.outcome is VenueOutcome.RESOLVED
        assert result.venue == "ARCA"
        assert "3 contract" in result.detail

    def test_several_contracts_disagreeing_are_ambiguous(self):
        """Undecidable from here — the operator gets the candidates, not a coin flip."""
        result = classify(
            "AAA",
            _details(
                FakeContract(primaryExchange="ARCA"),
                FakeContract(primaryExchange="NASDAQ"),
                FakeContract(primaryExchange="BATS"),
            ),
        )
        assert result.outcome is VenueOutcome.AMBIGUOUS
        assert result.candidates == ("ARCA", "BATS", "NASDAQ")
        assert result.venue is None

    def test_contracts_with_no_exchange_field_are_an_error(self):
        result = classify("AAA", _details(FakeContract(exchange="SMART", primaryExchange="")))
        assert result.outcome is VenueOutcome.ERROR
        assert "no exchange field" in result.detail

    def test_oversize_venue_is_rejected_not_truncated(self):
        """venue is String(20); a longer value would fail at write time instead."""
        result = classify("AAA", _details(FakeContract(primaryExchange="X" * 21)))
        assert result.outcome is VenueOutcome.ERROR
        assert "unusable venue" in result.detail

    def test_venue_at_the_column_limit_is_accepted(self):
        result = classify("AAA", _details(FakeContract(primaryExchange="X" * 20)))
        assert result.outcome is VenueOutcome.RESOLVED

    def test_unreviewed_venue_still_resolves_but_is_flagged(self):
        """IBKR is the authority — flag for review, never reject or remap."""
        result = classify("AAA", _details(FakeContract(primaryExchange="WEIRDVENUE")))
        assert result.outcome is VenueOutcome.RESOLVED
        assert result.venue == "WEIRDVENUE"
        assert result.unexpected_venue is True

    def test_reviewed_venue_is_not_flagged(self):
        result = classify("AAA", _details(FakeContract(primaryExchange="ARCA")))
        assert result.unexpected_venue is False
        assert "ARCA" in REVIEWED_VENUES

    def test_multi_dot_ticker_produces_a_parseable_identity(self):
        result = classify("BRK.B", _details(FakeContract(symbol="BRK B", primaryExchange="NYSE")))
        assert result.outcome is VenueOutcome.RESOLVED
        assert result.venue == "NYSE"

    def test_metrics_are_carried_through(self):
        result = classify("AAA", [], attempts=2, elapsed_s=10.5)
        assert (result.attempts, result.elapsed_s) == (2, 10.5)


@pytest.mark.unit
class TestProviderRetryBehaviour:
    """Only a non-response is retried; a negative answer is accepted immediately."""

    @staticmethod
    def _provider(responses: list, **kwargs) -> tuple[IBKRVenueProvider, list]:
        calls: list = []

        async def fetch(contract):
            calls.append(contract)
            value = responses[min(len(calls) - 1, len(responses) - 1)]
            if isinstance(value, Exception):
                raise value
            return value

        return IBKRVenueProvider(fetch_details=fetch, **kwargs), calls

    async def test_resolves_on_first_attempt(self):
        provider, calls = self._provider([_details(FakeContract())])
        result = await provider.qualify("AAA")
        assert result.outcome is VenueOutcome.RESOLVED
        assert len(calls) == 1

    async def test_not_found_is_not_retried(self):
        """IBKR answered — asking again just burns 10 seconds."""
        provider, calls = self._provider([[]])
        result = await provider.qualify("AAA")
        assert result.outcome is VenueOutcome.NOT_FOUND
        assert len(calls) == 1

    async def test_ambiguous_is_not_retried(self):
        provider, calls = self._provider(
            [_details(FakeContract(primaryExchange="ARCA"), FakeContract(primaryExchange="NYSE"))]
        )
        result = await provider.qualify("AAA")
        assert result.outcome is VenueOutcome.AMBIGUOUS
        assert len(calls) == 1

    async def test_none_then_success_retries_and_resolves(self):
        provider, calls = self._provider([None, _details(FakeContract())])
        result = await provider.qualify("AAA")
        assert result.outcome is VenueOutcome.RESOLVED
        assert result.attempts == 2
        assert len(calls) == 2

    async def test_none_twice_downgrades_to_not_found(self):
        provider, calls = self._provider([None])
        result = await provider.qualify("AAA")
        assert result.outcome is VenueOutcome.NOT_FOUND
        assert "likely IBKR error 200" in result.detail
        assert len(calls) == 2

    async def test_client_exception_is_retried_then_reported(self):
        provider, calls = self._provider([ConnectionError("gateway went away")])
        result = await provider.qualify("AAA")
        assert result.outcome is VenueOutcome.NOT_FOUND
        assert len(calls) == 2

    async def test_exception_then_success_recovers(self):
        provider, calls = self._provider([ConnectionError("blip"), _details(FakeContract())])
        result = await provider.qualify("AAA")
        assert result.outcome is VenueOutcome.RESOLVED
        assert len(calls) == 2

    async def test_max_attempts_is_honoured(self):
        provider, calls = self._provider([None], max_attempts=4)
        await provider.qualify("AAA")
        assert len(calls) == 4

    async def test_elapsed_time_is_recorded(self):
        """Elapsed time is the signal that reveals 10s error-200 stalls in the data."""
        ticks = iter([100.0, 110.5])  # start, then the stamp inside classify
        provider, _ = self._provider([_details(FakeContract())])
        provider._clock = lambda: next(ticks)
        result = await provider.qualify("AAA")
        assert result.elapsed_s == pytest.approx(10.5)


@pytest.mark.unit
class TestContractConstruction:
    """The lookup contract must not pre-suppose the answer."""

    def test_contract_carries_no_primary_exchange_hint(self):
        """Supplying one would bias IBKR toward confirming the guess we're replacing."""

        async def fetch(contract):
            return []

        contract = IBKRVenueProvider(fetch_details=fetch).build_contract("AAA")
        assert contract.symbol == "AAA"
        assert contract.exchange == "SMART"
        assert contract.currency == "USD"
        assert contract.secType == "STK"
        assert not contract.primaryExchange

    def test_currency_is_configurable(self):
        async def fetch(contract):
            return []

        contract = IBKRVenueProvider(fetch_details=fetch, currency="CAD").build_contract("AAA")
        assert contract.currency == "CAD"
