"""The startup comparison between the cache and the broker (Story 4.2, AC #3, #5).

What "internal position state matches the broker exactly" means (D-C): for
every instrument either side holds, the cache's **net** signed quantity over
every open position — every strategy, synthetic or not — equals the broker's,
compared exactly as ``Decimal``. Separately, a strategy's *own* net (the
non-synthetic part) must not contradict the broker (D-D): the framework's
correction never rewrites a strategy's position (measured, Task 1.1), so a
matching net can hide a strategy that believes it holds shares the broker does
not. Stdlib only, so Story 4.6's service can reuse it (AR38).
"""

import ast
import dataclasses
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from src.core.live_trade_recorder import RECONCILIATION_STRATEGY_IDS
from src.models import position_reconciliation as module
from src.models.broker_state import BrokerPosition, BrokerState, CashBalance
from src.models.position_reconciliation import (
    CAUSE_FLAT,
    CAUSE_MIXED,
    CAUSE_NET_AGREES,
    CAUSE_OPPOSITE,
    CAUSE_SHRANK,
    POSITION,
    STRATEGY_POSITION,
    SYNTHETIC_STRATEGY_IDS,
    CachedPosition,
    CashChange,
    LocalSnapshot,
    PositionDiscrepancy,
    StartupReconciliation,
    cash_changes,
    compare_positions,
    count_synthetic,
    split_by_owner,
    strategies_on_both_sides,
    symbol_key,
)

pytestmark = pytest.mark.unit

AT = datetime(2026, 9, 27, 13, 25, tzinfo=UTC)
NVDA = "NVDA.NASDAQ"
AAPL = "AAPL.NASDAQ"
STRATEGY = "SMACrossover-000"


def _broker(*positions: BrokerPosition) -> BrokerState:
    return BrokerState(
        account="***626",
        positions=tuple(positions),
        cash=(CashBalance(currency="USD", total_cash=Decimal("100000")),),
        retrieved_at=AT,
    )


def _held(instrument_id: str, quantity: str, *, resolved: bool = True) -> BrokerPosition:
    return BrokerPosition(
        instrument_id=instrument_id,
        quantity=Decimal(quantity),
        average_price=Decimal("100"),
        con_id=4815747,
        symbol=instrument_id.split(".")[0],
        instrument_resolved=resolved,
    )


def _cached(instrument_id: str, quantity: str, strategy_id: str = STRATEGY) -> CachedPosition:
    return CachedPosition(
        instrument_id=instrument_id, strategy_id=strategy_id, quantity=Decimal(quantity)
    )


class TestCachedPosition:
    def test_a_synthetic_owner_is_one_of_reconciliations_two_stamps(self):
        assert _cached(NVDA, "4", "EXTERNAL").is_synthetic
        assert _cached(NVDA, "-4", "INTERNAL-DIFF").is_synthetic
        assert not _cached(NVDA, "4").is_synthetic

    def test_a_zero_quantity_is_refused_because_a_flat_position_is_not_open(self):
        with pytest.raises(ValueError, match="non-zero"):
            _cached(NVDA, "0")

    @pytest.mark.parametrize("bad", [22.0, 22, "22"])
    def test_a_quantity_must_be_a_decimal(self, bad):
        with pytest.raises(TypeError, match="Decimal"):
            CachedPosition(instrument_id=NVDA, strategy_id=STRATEGY, quantity=bad)  # type: ignore[arg-type]

    def test_a_quantity_must_be_finite(self):
        with pytest.raises(ValueError, match="finite"):
            _cached(NVDA, "NaN")

    @pytest.mark.parametrize("field", ["instrument_id", "strategy_id"])
    def test_both_identifiers_are_required(self, field):
        values = {"instrument_id": NVDA, "strategy_id": STRATEGY, "quantity": Decimal("1")}
        values[field] = ""
        with pytest.raises(ValueError, match=field):
            CachedPosition(**values)

    def test_it_is_immutable(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            _cached(NVDA, "1").quantity = Decimal("2")  # type: ignore[misc]


class TestSyntheticIdsArePinnedToTheRecordersOwn:
    def test_the_duplicated_literal_equals_the_trade_recorders_constant(self):
        """A duplicated literal needs its own equality pin (CLAUDE.md): the
        recorder skips persisting exactly these owners (Story 3.6 D-D), and
        this module treats exactly these owners as not-a-strategy's-belief.
        Drift in either would let one side disagree with the other silently.
        """
        assert SYNTHETIC_STRATEGY_IDS == RECONCILIATION_STRATEGY_IDS


class TestComparePositions:
    """D-C and D-D. Each scenario is one Task 1 measured shape."""

    def test_matching_state_has_no_discrepancy(self):
        assert compare_positions([_cached(NVDA, "10")], _broker(_held(NVDA, "10"))) == ()

    def test_a_flat_cache_against_a_flat_broker_has_no_discrepancy(self):
        assert compare_positions([], _broker()) == ()

    def test_scenario_a_the_framework_triple_agrees_with_the_broker(self):
        """Measured 1.4A: a normal mid-position restart leaves S +10,
        EXTERNAL +10, INTERNAL-DIFF −10. Net and strategy both equal the broker."""
        cached = [
            _cached(NVDA, "10"),
            _cached(NVDA, "10", "EXTERNAL"),
            _cached(NVDA, "-10", "INTERNAL-DIFF"),
        ]
        assert compare_positions(cached, _broker(_held(NVDA, "10"))) == ()

    def test_scenario_c_a_phantom_strategy_position_is_a_strategy_discrepancy(self):
        """Measured 1.4C, the p7-fill-0901 shape: the broker is flat, the
        strategy's own cached +22 was never touched by the framework."""
        (row,) = compare_positions([_cached(NVDA, "22")], _broker())

        assert row == PositionDiscrepancy(
            instrument_id=NVDA,
            local_quantity=Decimal("22"),
            strategy_quantity=Decimal("22"),
            broker_quantity=Decimal("0"),
        )
        assert row.kind == STRATEGY_POSITION

    def test_scenario_d_a_matching_net_still_hides_a_contradicted_strategy(self):
        """Measured 1.4D: after the framework pass, net equals the broker's 10
        but the strategy still believes 22. Only the strategy check sees it."""
        cached = [
            _cached(NVDA, "22"),
            _cached(NVDA, "10", "EXTERNAL"),
            _cached(NVDA, "-22", "INTERNAL-DIFF"),
        ]

        (row,) = compare_positions(cached, _broker(_held(NVDA, "10")))

        assert row.local_quantity == row.broker_quantity == Decimal("10")
        assert row.strategy_quantity == Decimal("22")
        assert row.kind == STRATEGY_POSITION

    def test_an_offset_strategy_position_is_still_contradicted_when_net_is_zero(self):
        """Measured 1.1: S +22 with INTERNAL-DIFF −22 nets to the broker's 0,
        and the strategy still believes it is long."""
        cached = [_cached(NVDA, "22"), _cached(NVDA, "-22", "INTERNAL-DIFF")]

        (row,) = compare_positions(cached, _broker())

        assert row.local_quantity == Decimal("0") and row.kind == STRATEGY_POSITION

    def test_a_stale_synthetic_position_is_a_position_discrepancy(self):
        """Measured 1.4E: a stale EXTERNAL +4 on a flat broker is not a
        strategy's belief, so it is the resolvable kind."""
        (row,) = compare_positions([_cached(AAPL, "4", "EXTERNAL")], _broker())

        assert row.kind == POSITION
        assert (row.local_quantity, row.strategy_quantity, row.broker_quantity) == (
            Decimal("4"),
            Decimal("0"),
            Decimal("0"),
        )

    def test_a_broker_only_position_is_caught(self):
        (row,) = compare_positions([], _broker(_held(AAPL, "5")))

        assert row.kind == POSITION
        assert row.local_quantity == Decimal("0") and row.broker_quantity == Decimal("5")

    def test_a_strategy_matching_the_broker_with_a_synthetic_mismatch_is_resolvable(self):
        """Strategy +10 is right; an unmatched EXTERNAL +10 makes net 20."""
        cached = [_cached(NVDA, "10"), _cached(NVDA, "10", "EXTERNAL")]

        (row,) = compare_positions(cached, _broker(_held(NVDA, "10")))

        assert row.kind == POSITION and row.local_quantity == Decimal("20")

    def test_the_comparison_is_exact_never_close_enough(self):
        (row,) = compare_positions([_cached(NVDA, "22")], _broker(_held(NVDA, "22.0001")))
        assert row.difference == Decimal("0.0001")

    def test_the_net_comparison_is_exact_when_no_strategy_owns_the_position(self):
        """The mutation sweep's M2 survivor: the test above goes red through the
        *strategy* check even with a tolerance on net, because its position is
        strategy-owned. A synthetic position has no strategy share, so only an
        exact net comparison can see a 0.0001-share difference."""
        (row,) = compare_positions(
            [_cached(NVDA, "22", "EXTERNAL")], _broker(_held(NVDA, "22.0001"))
        )

        assert row.kind == POSITION and row.difference == Decimal("0.0001")

    def test_a_trailing_zero_is_not_a_discrepancy(self):
        assert compare_positions([_cached(NVDA, "22.0")], _broker(_held(NVDA, "22"))) == ()

    def test_net_sums_across_strategies(self):
        cached = [_cached(NVDA, "15", "A-000"), _cached(NVDA, "-5", "B-000")]
        assert compare_positions(cached, _broker(_held(NVDA, "10"))) == ()

    def test_a_short_is_compared_with_its_sign(self):
        (row,) = compare_positions([_cached(NVDA, "-5")], _broker(_held(NVDA, "5")))
        assert row.strategy_quantity == Decimal("-5") and row.kind == STRATEGY_POSITION

    def test_an_unresolved_broker_row_is_carried_as_such(self):
        (row,) = compare_positions([], _broker(_held("IB-CONID-265598", "3", resolved=False)))
        assert row.broker_resolved is False

    def test_rows_are_sorted_by_instrument_and_span_both_sides(self):
        rows = compare_positions(
            [_cached(NVDA, "22"), _cached("MSFT.NASDAQ", "1", "EXTERNAL")],
            _broker(_held(AAPL, "5")),
        )
        assert [row.instrument_id for row in rows] == [AAPL, "MSFT.NASDAQ", NVDA]

    def test_it_accepts_any_iterable_once(self):
        cached = iter([_cached(NVDA, "10")])
        assert compare_positions(cached, _broker(_held(NVDA, "10"))) == ()


class TestPositionDiscrepancy:
    def test_difference_is_broker_minus_local(self):
        row = PositionDiscrepancy(
            instrument_id=NVDA,
            local_quantity=Decimal("22"),
            strategy_quantity=Decimal("22"),
            broker_quantity=Decimal("10"),
        )
        assert row.difference == Decimal("-12")

    def test_a_row_with_no_strategy_share_is_never_a_strategy_discrepancy(self):
        row = PositionDiscrepancy(
            instrument_id=NVDA,
            local_quantity=Decimal("4"),
            strategy_quantity=Decimal("0"),
            broker_quantity=Decimal("0"),
        )
        assert row.kind == POSITION and not row.strategy_contradicted


def _row(strategy: str, broker: str, local: str | None = None) -> PositionDiscrepancy:
    return PositionDiscrepancy(
        instrument_id=NVDA,
        local_quantity=Decimal(strategy if local is None else local),
        strategy_quantity=Decimal(strategy),
        broker_quantity=Decimal(broker),
    )


class TestTheCoverageRule:
    """Story 4.7, D-A (PO ruling A, 2026-09-28): a strategy's own position is
    contradicted only when the broker does not cover it — the broker holds
    less in the strategy's direction, holds nothing, or holds the opposite
    side. Growth in the strategy's direction (a forward split, a stock
    dividend) is absorbed broker-ward instead of stopping the session: the
    strategy closing its lot then leaves the broker at ``B − S``, same side as
    ``B``, so the close can never carry the account through zero."""

    @pytest.mark.parametrize(
        ("strategy", "broker", "contradicted"),
        [
            ("10", "20", False),  # a 2:1 forward split, long
            ("-10", "-20", False),  # the same split on a short
            ("10", "10", False),  # agreement
            ("10", "5", True),  # a reverse split: the broker holds fewer
            ("10", "0", True),  # a cash merger, a symbol change: the broker holds none
            ("10", "-5", True),  # the opposite side
            ("10", "-20", True),  # the opposite side, and larger: size alone must not decide
            ("-10", "-5", True),  # a short the broker holds fewer of
            ("-10", "5", True),  # a short against a long
            ("-10", "20", True),  # a short against a larger long
            ("-10", "0", True),  # a short against nothing
        ],
    )
    def test_the_truth_table(self, strategy, broker, contradicted):
        row = _row(strategy, broker)

        assert row.strategy_contradicted is contradicted
        assert row.kind == (STRATEGY_POSITION if contradicted else POSITION)

    def test_a_forward_split_on_a_strategy_position_is_a_resolvable_row(self):
        """The named test Stories 4.2 and 4.3 handed to this story: strategy +10,
        the broker +20 after a 2:1 split — a net row the framework can
        correct, not a refusal."""
        (row,) = compare_positions([_cached(NVDA, "10")], _broker(_held(NVDA, "20")))

        assert row.kind == POSITION and not row.strategy_contradicted
        assert (row.local_quantity, row.strategy_quantity, row.broker_quantity) == (
            Decimal("10"),
            Decimal("10"),
            Decimal("20"),
        )

    def test_once_absorbed_a_split_leaves_no_row(self):
        """1.1a, measured: after the framework's pass the strategy's lot sits beside
        the synthetic shares and net equals the broker — clean."""
        cached = [
            _cached(NVDA, "10"),
            _cached(NVDA, "20", "EXTERNAL"),
            _cached(NVDA, "-10", "INTERNAL-DIFF"),
        ]
        assert compare_positions(cached, _broker(_held(NVDA, "20"))) == ()

    def test_a_strategy_beside_an_external_holding_is_not_a_discrepancy(self):
        """Strategy +10 beside an ``EXTERNAL`` +4 the account already held: the
        broker's 14 covers the strategy, net agrees — nothing to stop for."""
        cached = [_cached(NVDA, "10"), _cached(NVDA, "4", "EXTERNAL")]
        assert compare_positions(cached, _broker(_held(NVDA, "14"))) == ()

    def test_same_side_strategies_are_covered_by_their_sum(self):
        """Two long strategies, +10 each: the broker must hold at least 20 — closing
        both leaves it at ``broker − 20``, on its own side."""
        cached = [_cached(NVDA, "10", "A-000"), _cached(NVDA, "10", "B-000")]

        assert compare_positions(cached, _broker(_held(NVDA, "30")))[0].kind == POSITION
        (row,) = compare_positions(cached, _broker(_held(NVDA, "15")))
        assert row.kind == STRATEGY_POSITION


class TestStrategiesOnBothSidesKeepTheEqualityRule:
    """Code review (2026-09-28): coverage is judged on the strategies' *net*, which
    means nothing when their own lots are on both sides — A +10 and B −5 net +5,
    "covered" by a broker at 7, yet A closing its 10 crosses the broker through
    zero. Such an instrument keeps the pre-4.7 rule (exact equality), so the
    coverage rule stays strictly a relaxation and single-side sessions are as
    the PO ruled."""

    def test_growth_over_a_mixed_net_is_refused(self):
        cached = [_cached(NVDA, "10", "A-000"), _cached(NVDA, "-5", "B-000")]

        (row,) = compare_positions(cached, _broker(_held(NVDA, "7")))

        assert row.strategy_mixed_sides is True
        assert row.kind == STRATEGY_POSITION and row.strategy_contradicted
        assert row.likely_cause == CAUSE_MIXED

    def test_a_mixed_net_that_equals_the_broker_is_clean(self):
        cached = [_cached(NVDA, "10", "A-000"), _cached(NVDA, "-5", "B-000")]
        assert compare_positions(cached, _broker(_held(NVDA, "5"))) == ()

    def test_a_mixed_net_of_zero_against_a_broker_holding_is_refused(self):
        """Code review of PR #35: A +10 and B −10 net to 0. Lots exist, so the
        ``strategy != 0`` guard the single-side rule uses cannot mean "no
        position" here — a broker at +5 must be refused, or the correction
        lands in ``INTERNAL-DIFF +5`` and A closing its 10 carries the account
        short 5 (NFR14)."""
        cached = [_cached(NVDA, "10", "A-000"), _cached(NVDA, "-10", "B-000")]

        (row,) = compare_positions(cached, _broker(_held(NVDA, "5")))

        assert row.strategy_mixed_sides is True
        assert row.strategy_contradicted and row.kind == STRATEGY_POSITION
        assert row.likely_cause == CAUSE_MIXED

    def test_a_mixed_net_of_zero_against_a_flat_broker_is_clean(self):
        cached = [_cached(NVDA, "10", "A-000"), _cached(NVDA, "-10", "B-000")]
        assert compare_positions(cached, _broker()) == ()

    def test_synthetic_owners_do_not_make_a_strategy_mixed(self):
        """Only strategies' own lots count: a synthetic −10 beside S +10 is the
        normal restart triple, judged by coverage as before."""
        cached = [_cached(NVDA, "10"), _cached(NVDA, "-10", "INTERNAL-DIFF")]

        (row,) = compare_positions(cached, _broker(_held(NVDA, "20")))

        assert row.strategy_mixed_sides is False and row.kind == POSITION


class TestWhatStaysRefused:
    """PO ruling (Story 4.7): a strictly-shrinking, a zero and an opposite-side
    broker quantity are still refused — the NFR14 floor the coverage rule keeps."""

    @pytest.mark.parametrize(
        ("broker", "cause"),
        [("5", CAUSE_SHRANK), ("0", CAUSE_FLAT), ("-5", CAUSE_OPPOSITE)],
        ids=["shrinking", "zero", "opposite-side"],
    )
    def test_each_uncovered_shape_is_refused_through_compare(self, broker, cause):
        held = () if broker == "0" else (_held(NVDA, broker),)

        (row,) = compare_positions([_cached(NVDA, "10")], _broker(*held))

        assert row.kind == STRATEGY_POSITION and row.strategy_contradicted
        assert row.likely_cause == cause

    def test_a_shrink_that_net_already_matches_is_still_refused(self):
        """Measured 1.4D's shape after a reverse split: the framework's pass
        makes net agree, and only the strategy check still sees S +22 against 10."""
        cached = [
            _cached(NVDA, "22"),
            _cached(NVDA, "10", "EXTERNAL"),
            _cached(NVDA, "-22", "INTERNAL-DIFF"),
        ]

        (row,) = compare_positions(cached, _broker(_held(NVDA, "10")))

        assert row.local_quantity == row.broker_quantity
        assert row.likely_cause == f"{CAUSE_SHRANK}{CAUSE_NET_AGREES}"


class TestLikelyCause:
    """PO ruling (Story 4.7): the refusal names the likely cause — our own
    words, chosen from the broker's shape alone, never broker text."""

    @pytest.mark.parametrize(
        ("strategy", "broker", "cause"),
        [
            ("10", "5", CAUSE_SHRANK),
            ("-10", "-5", CAUSE_SHRANK),
            ("10", "0", CAUSE_FLAT),
            ("-10", "0", CAUSE_FLAT),
            ("10", "-5", CAUSE_OPPOSITE),
            ("-10", "5", CAUSE_OPPOSITE),
        ],
    )
    def test_each_refused_shape_names_its_cause(self, strategy, broker, cause):
        assert _row(strategy, broker).likely_cause == cause

    @pytest.mark.parametrize(("strategy", "broker"), [("10", "20"), ("0", "4"), ("10", "10")])
    def test_a_row_that_is_not_refused_names_none(self, strategy, broker):
        assert _row(strategy, broker, local="4").likely_cause is None

    def test_the_causes_name_the_corporate_actions_the_ruling_lists(self):
        assert "reverse split" in CAUSE_SHRANK and "partial sale" in CAUSE_SHRANK
        assert "cash merger" in CAUSE_FLAT and "symbol change" in CAUSE_FLAT
        assert "opposite side" in CAUSE_OPPOSITE

    def test_when_the_net_already_agrees_the_sessions_own_orders_are_named_too(self):
        """Code review (2026-09-28): after a split is absorbed, a strategy that
        sells the reconciliation-owned shares (``sma_momentum`` reads the net)
        ends S −10 beside a synthetic +10 on a flat broker. Net agrees; blaming a
        cash merger alone would send the operator after an event that never
        happened."""
        row = _row("-10", "0", local="0")

        assert row.likely_cause == f"{CAUSE_FLAT}{CAUSE_NET_AGREES}"
        assert "reconciliation-owned" in CAUSE_NET_AGREES

    def test_when_the_net_disagrees_the_cause_is_the_brokers_shape_alone(self):
        assert _row("10", "0").likely_cause == CAUSE_FLAT


def _usd(amount: str, currency: str = "USD") -> CashBalance:
    return CashBalance(currency=currency, total_cash=Decimal(amount))


class TestLocalSnapshot:
    """Story 4.7, D-B: what the cache held before Nautilus's own pass — its
    positions (Story 4.2 D-F) and the previous run's last-recorded cash."""

    def test_it_holds_positions_cash_and_when_the_cash_was_recorded(self):
        snapshot = LocalSnapshot(
            positions=(_cached(NVDA, "10"),), cash=(_usd("100000"),), cash_recorded_at=AT
        )

        assert snapshot.positions[0].quantity == Decimal("10")
        assert snapshot.cash == (_usd("100000"),) and snapshot.cash_recorded_at == AT

    def test_cash_and_its_time_default_to_unknown(self):
        snapshot = LocalSnapshot(positions=())

        assert snapshot.cash == () and snapshot.cash_recorded_at is None

    def test_a_naive_recorded_at_is_refused(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            LocalSnapshot(positions=(), cash_recorded_at=datetime(2026, 9, 27))

    def test_one_row_per_currency(self):
        with pytest.raises(ValueError, match="currency"):
            LocalSnapshot(positions=(), cash=(_usd("1"), _usd("2")))

    @pytest.mark.parametrize(
        ("field", "value"),
        [("positions", [_cached(NVDA, "1")]), ("cash", [_usd("1")]), ("positions", ("x",))],
    )
    def test_members_are_typed_tuples(self, field, value):
        values = {"positions": (), field: value}
        with pytest.raises(TypeError):
            LocalSnapshot(**values)


class TestCashChanges:
    """Story 4.7, D-B (PO ruling A): cash that moved while the session was not
    running — informational, compared exactly, per currency."""

    def test_a_dividend_is_one_change_with_before_after_and_difference(self):
        before = LocalSnapshot(positions=(), cash=(_usd("100000.00"),), cash_recorded_at=AT)
        broker = BrokerState(
            account="***626", positions=(), cash=(_usd("100123.45"),), retrieved_at=AT
        )

        (change,) = cash_changes(before, broker)

        assert change == CashChange(
            currency="USD", before=Decimal("100000.00"), after=Decimal("100123.45")
        )
        assert change.difference == Decimal("123.45")

    def test_unchanged_cash_is_no_change_and_the_comparison_is_exact(self):
        before = LocalSnapshot(positions=(), cash=(_usd("100000.00"),))

        assert cash_changes(before, _broker()) == (), "100000.00 == 100000 as a Decimal"
        moved = LocalSnapshot(positions=(), cash=(_usd("100000.01"),))
        assert [c.difference for c in cash_changes(moved, _broker())] == [Decimal("-0.01")]

    def test_no_recorded_cash_is_nothing_to_compare(self):
        """A fresh session, or a flushed cache: there is no before."""
        assert cash_changes(LocalSnapshot(positions=()), _broker()) == ()

    def test_a_currency_on_one_side_only_is_a_change_with_that_side_unknown(self):
        before = LocalSnapshot(positions=(), cash=(_usd("5", "EUR"), _usd("100000")))

        (change,) = cash_changes(before, _broker())

        assert (change.currency, change.before, change.after) == ("EUR", Decimal("5"), None)
        assert change.difference is None

    def test_changes_are_sorted_by_currency(self):
        before = LocalSnapshot(positions=(), cash=(_usd("5", "EUR"), _usd("1")))
        broker = BrokerState(
            account="***626", positions=(), cash=(_usd("2"), _usd("6", "EUR")), retrieved_at=AT
        )

        assert [c.currency for c in cash_changes(before, broker)] == ["EUR", "USD"]

    def test_a_change_needs_at_least_one_side(self):
        with pytest.raises(ValueError, match="side"):
            CashChange(currency="USD", before=None, after=None)


class TestCountSynthetic:
    def test_it_counts_open_positions_owned_by_reconciliation(self):
        cached = [
            _cached(NVDA, "10"),
            _cached(NVDA, "10", "EXTERNAL"),
            _cached(NVDA, "-10", "INTERNAL-DIFF"),
        ]
        assert count_synthetic(cached) == 2


class TestBrokerCoversStrategy:
    """Story 4.5, PO ruling 2026-09-28: the broker holds all the strategies own,
    on the same side, and more — the one contradiction startup leaves to D-C."""

    @pytest.mark.parametrize(
        ("strategy", "broker", "covered"),
        [
            ("10", "15", True),
            ("-10", "-15", True),
            ("10", "10", False),  # equal: not a disagreement at all
            ("10", "5", False),  # the broker holds less than believed
            ("10", "0", False),  # the broker is flat
            ("10", "-5", False),  # the opposite side
            ("-10", "15", False),
            ("0", "15", False),  # no strategy position: nothing to cover
        ],
    )
    def test_it_is_true_only_for_a_same_side_larger_holding(self, strategy, broker, covered):
        row = PositionDiscrepancy(
            instrument_id=NVDA,
            local_quantity=Decimal(broker),
            strategy_quantity=Decimal(strategy),
            broker_quantity=Decimal(broker),
        )

        assert row.broker_covers_strategy is covered


class TestSplitByOwner:
    """Story 4.5 (D-C): what a strategy may start beside — the strategies'
    signed net on one instrument, and the part no strategy owns."""

    def test_a_strategy_alone_owns_everything(self):
        assert split_by_owner([_cached(NVDA, "10")], NVDA) == (Decimal("10"), Decimal("0"))

    def test_an_unattributable_holding_is_unowned(self):
        cached = [_cached(NVDA, "10", "INTERNAL-DIFF")]

        assert split_by_owner(cached, NVDA) == (Decimal("0"), Decimal("10"))

    def test_a_pre_4_5_triple_nets_its_synthetics_to_nothing(self):
        """``EXTERNAL +10 / INTERNAL-DIFF −10`` beside the strategy's own +10:
        nothing is unowned, so the strategy is not refused for it."""
        cached = [
            _cached(NVDA, "10"),
            _cached(NVDA, "10", "EXTERNAL"),
            _cached(NVDA, "-10", "INTERNAL-DIFF"),
        ]

        assert split_by_owner(cached, NVDA) == (Decimal("10"), Decimal("0"))

    def test_other_instruments_are_ignored(self):
        cached = [_cached(AAPL, "4", "EXTERNAL"), _cached(AAPL, "3")]

        assert split_by_owner(cached, NVDA) == (Decimal("0"), Decimal("0"))

    def test_every_strategy_of_the_session_counts_as_owned(self):
        cached = [_cached(NVDA, "10"), _cached(NVDA, "-4", "SMAMomentum-002")]

        assert split_by_owner(cached, NVDA) == (Decimal("6"), Decimal("0"))

    def test_with_the_sessions_ids_a_stale_own_id_is_unowned(self):
        """PR #35 code review, D4 (PO ruling 2026-09-30): a position under an id
        no entry of this session resolves to belongs to no strategy that will
        start, so it is judged with the synthetics."""
        cached = [_cached(NVDA, "10", "SMACrossover-001"), _cached(NVDA, "4", "SMACrossover-000")]

        assert split_by_owner(cached, NVDA, session_strategy_ids={"SMACrossover-001"}) == (
            Decimal("10"),
            Decimal("4"),
        )

    def test_without_the_sessions_ids_every_non_synthetic_id_is_owned(self):
        cached = [_cached(NVDA, "10", "SMACrossover-001"), _cached(NVDA, "4", "SMACrossover-000")]

        assert split_by_owner(cached, NVDA, session_strategy_ids=None) == (
            Decimal("14"),
            Decimal("0"),
        )

    def test_an_empty_session_id_set_owns_nothing(self):
        """Explicitly empty is not "unknown": nothing of this session will start."""
        assert split_by_owner([_cached(NVDA, "10")], NVDA, session_strategy_ids=()) == (
            Decimal("0"),
            Decimal("10"),
        )


class TestStrategiesOnBothSides:
    """The resume check's guard on D5a's exemption: a net cannot say whether
    each lot is covered when the session's own lots are long and short."""

    def test_long_and_short_lots_are_mixed(self):
        cached = [_cached(NVDA, "20"), _cached(NVDA, "-10", "SMAMomentum-001")]

        assert strategies_on_both_sides(cached, NVDA)

    def test_one_side_is_not(self):
        cached = [_cached(NVDA, "20"), _cached(NVDA, "10", "SMAMomentum-001")]

        assert not strategies_on_both_sides(cached, NVDA)

    def test_synthetics_and_other_instruments_do_not_count(self):
        cached = [_cached(NVDA, "20"), _cached(NVDA, "-5", "INTERNAL-DIFF"), _cached(AAPL, "-3")]

        assert not strategies_on_both_sides(cached, NVDA)

    def test_a_stale_own_id_does_not_count_when_the_sessions_ids_are_known(self):
        cached = [_cached(NVDA, "20", "SMACrossover-001"), _cached(NVDA, "-10", "SMACrossover-000")]

        assert not strategies_on_both_sides(cached, NVDA, session_strategy_ids={"SMACrossover-001"})
        assert strategies_on_both_sides(cached, NVDA)


class TestSymbolKey:
    """PR #35 code review, D1: the broker's and Nautilus's spellings of one
    symbol compare equal; nothing else does."""

    @pytest.mark.parametrize("spelling", ["BRK B", "BRK-B", "brk.b", " BRK B "])
    def test_class_share_spellings_agree(self, spelling):
        assert symbol_key(spelling) == "BRKB"

    def test_distinct_symbols_stay_distinct(self):
        assert symbol_key("NVDA") != symbol_key("NVDL")

    def test_none_and_empty_read_empty(self):
        assert symbol_key(None) == "" and symbol_key("") == ""

    def test_the_rows_key_is_its_instruments_symbol(self):
        (row,) = compare_positions([_cached("BRK-B.NYSE", "4")], _broker())

        assert row.symbol_key == "BRKB"
        assert row.broker_symbol is None

    def test_a_broker_row_carries_the_brokers_symbol(self):
        (row,) = compare_positions([], _broker(_held(NVDA, "10")))

        assert row.broker_symbol == "NVDA"


class TestStartupReconciliation:
    def test_it_holds_what_reconcile_ok_renders(self):
        result = StartupReconciliation(
            broker=_broker(_held(NVDA, "10")),
            framework_resolved=(),
            reconcile_resolved=(),
            open_orders=1,
            synthetic_positions=2,
            elapsed_ms=12.5,
        )

        assert result.discrepancy_count == 0
        assert result.broker.positions[0].instrument_id == NVDA

    def test_the_discrepancy_count_spans_both_resolutions(self):
        row = PositionDiscrepancy(
            instrument_id=AAPL,
            local_quantity=Decimal("4"),
            strategy_quantity=Decimal("0"),
            broker_quantity=Decimal("0"),
        )
        result = StartupReconciliation(
            broker=_broker(),
            framework_resolved=(row,),
            reconcile_resolved=(row,),
            open_orders=0,
            synthetic_positions=0,
            elapsed_ms=1.0,
        )
        assert result.discrepancy_count == 2


def _non_stdlib_imports(source: str) -> list[str]:
    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.append(node.module)
    return [
        name
        for name in imported
        if name.split(".")[0] not in sys.stdlib_module_names and name != "src.models.broker_state"
    ]


class TestImportPurity:
    """AR38: Story 4.6's service imports this, so it must stay stdlib-only
    (plus the equally stdlib-only ``broker_state`` it compares against)."""

    def test_imports_are_standard_library_only(self):
        source = Path(module.__file__).read_text(encoding="utf-8")

        assert "import" in source, "the scan is not looking at the module"
        assert _non_stdlib_imports(source) == []

    @pytest.mark.parametrize(
        "planted", ["import structlog", "from src.core import live_gate", "import nautilus_trader"]
    )
    def test_the_scan_can_fail(self, planted):
        assert _non_stdlib_imports(f"import decimal\n{planted}\n") != []

    def test_importing_the_module_loads_no_framework(self):
        code = (
            "import sys, src.models.position_reconciliation;"
            "print(','.join(sorted(m for m in "
            "('sqlalchemy', 'nautilus_trader', 'ibapi', 'src.core', 'src.db', 'src.services') "
            "if m in sys.modules)))"
        )

        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=Path(module.__file__).parents[2],
            capture_output=True,
            text=True,
            check=True,
        )

        assert result.stdout.strip() == ""
