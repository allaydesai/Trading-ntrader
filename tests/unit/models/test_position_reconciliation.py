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
    POSITION,
    STRATEGY_POSITION,
    SYNTHETIC_STRATEGY_IDS,
    CachedPosition,
    PositionDiscrepancy,
    StartupReconciliation,
    compare_positions,
    count_synthetic,
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


class TestCountSynthetic:
    def test_it_counts_open_positions_owned_by_reconciliation(self):
        cached = [
            _cached(NVDA, "10"),
            _cached(NVDA, "10", "EXTERNAL"),
            _cached(NVDA, "-10", "INTERNAL-DIFF"),
        ]
        assert count_synthetic(cached) == 2


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
