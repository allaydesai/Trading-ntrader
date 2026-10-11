"""Unit tests for a sealed session's execution conditions (Story 5.4, FR43, FR45, AR7).

``src/services/session_conditions.py`` builds AR7's flat ``session_conditions``
keys. These tests pin the document's shape against the PRD's own list (a name
dropped goes red) and against the builder's output (a name never added goes
red), every value's type and JSON round trip, and each condition against the
code that makes it true: the node builder's RTH and market-data refusals, the
run row's own capital column.
"""

import copy
import inspect
import json
from decimal import Decimal

import pytest
from ibapi.common import MarketDataTypeEnum  # type: ignore[import-untyped]
from sqlalchemy import Numeric

from src.config import IBKRSettings
from src.core.live_market_data import (
    REQUIRED_LIVE_MARKET_DATA_TYPE_NAME,
    LiveMarketDataError,
    resolve_live_market_data_type,
    resolve_live_use_rth,
)
from src.db.models.backtest import BacktestRun
from src.models.reconciliation import ViewPosition
from src.services.session_conditions import (
    AR7_KEYS,
    BACKTEST_PRICE_BASIS,
    CAPITAL_QUANTUM,
    LAST_RUN_FLAGS_KEY,
    MARKET_DATA_TIER,
    PAPER_PRICE_BASIS,
    PRICE_BASES,
    RTH_ONLY,
    SCHEMA_VERSION,
    START_DATE_BASIS,
    START_DATE_BASIS_KEY,
    build_session_conditions,
)

pytestmark = pytest.mark.unit

SOURCE = "_bmad-output/planning-artifacts/prd-epic5-scope.md"
#: ACs 7–9 are the story's own (from the routed debt), not the PRD's — their
#: markers cite the story (code review P12).
STORY = (
    "_bmad-output/implementation-artifacts/"
    "5-4-record-the-conditions-the-session-actually-ran-under.md"
)
AC_7 = "rth_only and market_data_tier are tied to the policy the node builder enforces"
AC_8 = (
    "a synthetic owner's split shares held after the strategy exits are an open position at "
    "seal: the seal refuses naming them, and open_positions_at_seal is built from the same view"
)
AC_9 = "last_run_flags is the sealed row's runtime_flags as stored, or null"

#: AR7's schema, copied by hand from ``prd-epic5-scope.md`` — a name dropped
#: from ``AR7_KEYS`` goes red here, never silently.
PRD_AR7_KEYS = {
    "starting_capital",
    "rth_only",
    "price_basis",
    "market_data_tier",
    "closed_trade_count",
    "open_positions_at_seal",
    "schema_version",
}
#: PO rulings R1a and R2a (2026-10-11): the two keys beside AR7's own.
RULED_EXTRAS = {"start_date_basis", "last_run_flags"}

#: The last process run's ``runtime_flags`` exactly as ``SessionService`` writes
#: them: a contained strategy failure and a refusal summary (Stories 2.7, 3.7).
RUNTIME_FLAGS = {
    "v": 1,
    "all_failed": True,
    "failed_strategies": [
        {
            "strategy_id": "SMACrossover-000",
            "spec_strategy_id": "sma_crossover",
            "error_type": "ValueError",
            "handler": "on_bar",
            "at": "2026-10-02T15:30:00+00:00",
            "detail": "indicator not ready",
        }
    ],
    "order_rejections": {
        "rejected": 2,
        "denied": 0,
        "consecutive": 2,
        "first_at": "2026-10-02T15:00:00+00:00",
        "last": {"at": "2026-10-02T15:10:00+00:00", "kind": "rejected", "reason": "[redacted]"},
    },
}


def _conditions(**overrides):
    values = dict(
        starting_capital=Decimal("100000.00"),
        closed_trade_count=4,
        open_positions=(),
        last_run_flags=None,
    )
    values.update(overrides)
    return build_session_conditions(**values)


class TestTheDocumentHoldsAR7sKeys:
    @pytest.mark.acceptance_criterion(
        "5.4a",
        "a sealed session's config_snapshot holds session_conditions with starting_capital, "
        "rth_only, price_basis, market_data_tier, closed_trade_count, open_positions_at_seal "
        "and schema_version: 1",
        SOURCE,
    )
    def test_the_document_holds_exactly_ar7s_keys_and_the_ruled_extras(self):
        doc = _conditions()

        assert set(doc) == PRD_AR7_KEYS | RULED_EXTRAS
        assert doc["schema_version"] == SCHEMA_VERSION == 1

    def test_ar7_keys_are_the_prds_seven_names(self):
        assert set(AR7_KEYS) == PRD_AR7_KEYS
        assert len(AR7_KEYS) == len(PRD_AR7_KEYS)

    def test_the_extra_keys_are_named_constants(self):
        assert {START_DATE_BASIS_KEY, LAST_RUN_FLAGS_KEY} == RULED_EXTRAS

    def test_every_value_is_json_native_and_typed(self):
        doc = _conditions(
            open_positions=(ViewPosition("AAPL.NASDAQ", Decimal("10")),),
            last_run_flags=RUNTIME_FLAGS,
        )

        assert json.loads(json.dumps(doc)) == doc
        assert isinstance(doc["starting_capital"], str)
        assert doc["rth_only"] is True
        assert isinstance(doc["price_basis"], str)
        assert isinstance(doc["market_data_tier"], str)
        assert type(doc["closed_trade_count"]) is int
        assert type(doc["schema_version"]) is int
        assert isinstance(doc["open_positions_at_seal"], list)
        assert isinstance(doc["start_date_basis"], str)
        assert isinstance(doc["last_run_flags"], dict)


class TestThePriceBasis:
    @pytest.mark.acceptance_criterion(
        "5.4b",
        "price_basis records ibkr_unadjusted_smart for a paper session, distinct from a "
        "backtest's firstrate_adjusted",
        SOURCE,
    )
    def test_a_paper_session_records_ibkr_unadjusted_smart(self):
        assert _conditions()["price_basis"] == PAPER_PRICE_BASIS == "ibkr_unadjusted_smart"
        assert BACKTEST_PRICE_BASIS == "firstrate_adjusted"
        assert PAPER_PRICE_BASIS != BACKTEST_PRICE_BASIS

    def test_the_vocabulary_is_closed_at_ar7s_two_values(self):
        assert set(PRICE_BASES) == {"firstrate_adjusted", "ibkr_unadjusted_smart"}
        assert len(PRICE_BASES) == 2


def _ibkr(**overrides) -> IBKRSettings:
    """Settings with no ``.env`` and only the fields given set — the developer's
    shell is cleared by ``_isolate_market_data_env`` (the market-data tests' own
    rule: an exported ``IBKR_MARKET_DATA_TYPE`` marks the field *set*)."""
    return IBKRSettings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture
def _isolate_market_data_env(monkeypatch):
    for name in ("IBKR_MARKET_DATA_TYPE", "IBKR_USE_RTH"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)


@pytest.mark.usefixtures("_isolate_market_data_env")
class TestTheConditionsArePolicyTheBuilderEnforces:
    """AC #7: RTH and the market-data tier are what every start refuses to do
    without. The tier is the builder's own constant; RTH has none upstream, so
    its literal is pinned here to the resolver's behaviour, both ways."""

    @pytest.mark.acceptance_criterion("5.4g", AC_7, STORY)
    def test_the_tier_is_the_only_one_a_session_can_run_on(self):
        assert _conditions()["market_data_tier"] == MARKET_DATA_TIER
        assert MARKET_DATA_TIER == REQUIRED_LIVE_MARKET_DATA_TYPE_NAME == "REALTIME"
        resolved = resolve_live_market_data_type(_ibkr())
        assert MarketDataTypeEnum.toStr(resolved) == MARKET_DATA_TIER
        with pytest.raises(LiveMarketDataError):
            resolve_live_market_data_type(_ibkr(ibkr_market_data_type="DELAYED_FROZEN"))

    def test_rth_is_the_only_basis_a_session_can_run_on(self):
        assert _conditions()["rth_only"] is RTH_ONLY is True
        assert resolve_live_use_rth(_ibkr(ibkr_use_rth=True)) is RTH_ONLY
        with pytest.raises(LiveMarketDataError):
            resolve_live_use_rth(_ibkr(ibkr_use_rth=False))


class TestTheClosedTradeCount:
    @pytest.mark.acceptance_criterion(
        "5.4c",
        "the closed-trade count travels with the record so a difference can be read against "
        "sampling noise",
        SOURCE,
    )
    @pytest.mark.parametrize("count", [0, 1, 103])
    def test_the_count_given_is_the_count_recorded(self, count):
        assert _conditions(closed_trade_count=count)["closed_trade_count"] == count

    @pytest.mark.parametrize("bad", [-1, True, 3.0, "4"], ids=["negative", "bool", "float", "str"])
    def test_a_count_that_is_not_a_whole_non_negative_number_is_refused(self, bad):
        with pytest.raises((TypeError, ValueError)):
            _conditions(closed_trade_count=bad)


class TestTheStartingCapitalIsStoredAsIs:
    @pytest.mark.acceptance_criterion(
        "5.4e",
        "the paper session's own starting balance is stored as-is, with no normalization",
        SOURCE,
    )
    @pytest.mark.parametrize(
        "typed",
        ["100000.00", "100000", "1E+5", "100000.0000"],
        ids=["cents", "whole", "exp", "4dp"],
    )
    def test_the_capital_is_recorded_at_the_columns_own_scale(self, typed):
        """The value never changes; only its form matches the stored column
        (``Numeric(20,2)``) — typed ``1E+5`` reads back as ``100000.00``."""
        assert _conditions(starting_capital=Decimal(typed))["starting_capital"] == "100000.00"

    def test_an_odd_amount_is_kept_exactly(self):
        assert _conditions(starting_capital=Decimal("25000.37"))["starting_capital"] == "25000.37"

    def test_the_scale_is_read_from_the_run_rows_column(self):
        column_type = BacktestRun.__table__.c.initial_capital.type
        assert isinstance(column_type, Numeric) and column_type.scale is not None
        assert CAPITAL_QUANTUM == Decimal(1).scaleb(-column_type.scale) == Decimal("0.01")

    def test_a_capital_the_column_would_round_is_refused_never_changed(self):
        with pytest.raises(ValueError, match="cent"):
            _conditions(starting_capital=Decimal("100000.005"))

    def test_a_capital_that_is_not_a_decimal_is_refused(self):
        with pytest.raises(TypeError):
            _conditions(starting_capital=100000.0)

    @pytest.mark.parametrize("typed", ["NaN", "Infinity", "-Infinity"])
    def test_a_non_finite_capital_is_refused_in_its_own_words(self, typed):
        """Code review P10: never "fractions of a cent" for a NaN."""
        with pytest.raises(ValueError, match="not a finite amount"):
            _conditions(starting_capital=Decimal(typed))


class TestOpenPositionsAtSeal:
    def test_a_flat_view_records_an_empty_list(self):
        assert _conditions(open_positions=())["open_positions_at_seal"] == []

    @pytest.mark.acceptance_criterion("5.4h", AC_8, STORY)
    def test_each_view_position_is_recorded_verbatim(self):
        """Every owner's net — the strategy's and a synthetic one's alike —
        exactly as the session's view reports it (Story 5.5's ``--force``)."""
        positions = (
            ViewPosition("AAPL.NASDAQ", Decimal("10")),
            ViewPosition("NVDA.NASDAQ", Decimal("-2.5")),
        )

        recorded = _conditions(open_positions=positions)["open_positions_at_seal"]

        assert recorded == [
            {"instrument_id": "AAPL.NASDAQ", "quantity": "10"},
            {"instrument_id": "NVDA.NASDAQ", "quantity": "-2.5"},
        ]

    def test_anything_but_a_view_position_is_refused(self):
        with pytest.raises(TypeError):
            _conditions(open_positions=({"instrument_id": "AAPL.NASDAQ", "quantity": "1"},))


class TestTheStartDateBasis:
    def test_the_basis_is_always_the_sessions_creation(self):
        """PO ruling R1a (2026-10-11): ``start_date`` is ``created_at`` — a
        disclosed deviation from "first start", stated in the record."""
        assert _conditions()["start_date_basis"] == START_DATE_BASIS == "session_created_at"

    def test_no_caller_can_record_another_basis(self):
        """Code review P9: the run's ``start_date`` is ``period_start``'s, so a
        caller-chosen basis could only make the record contradict its own
        column. The builder takes none."""
        assert "start_date_basis" not in inspect.signature(build_session_conditions).parameters


class TestTheLastRunsFlags:
    """PO ruling R2a (2026-10-11): the sealed row's ``runtime_flags``, as stored."""

    @pytest.mark.acceptance_criterion("5.4i", AC_9, STORY)
    def test_the_flags_are_recorded_as_stored(self):
        assert _conditions(last_run_flags=RUNTIME_FLAGS)["last_run_flags"] == RUNTIME_FLAGS

    def test_no_flags_record_null(self):
        assert _conditions(last_run_flags=None)["last_run_flags"] is None

    def test_the_flags_are_a_deep_copy(self):
        given = copy.deepcopy(RUNTIME_FLAGS)
        recorded = _conditions(last_run_flags=given)["last_run_flags"]

        given["failed_strategies"][0]["detail"] = "changed afterwards"
        given["order_rejections"]["rejected"] = 99

        assert recorded == RUNTIME_FLAGS

    @pytest.mark.parametrize(
        "stored", [["odd", {"v": 1}], "a string", 3, {}], ids=["list", "str", "int", "empty"]
    )
    def test_flags_of_any_stored_shape_are_kept_never_refused(self, stored):
        """Code review P4: JSONB has no schema, and a stopped session must
        always be sealable — a hand-edited or restored ``runtime_flags`` is
        recorded as stored (R2a), never a reason to refuse forever."""
        assert _conditions(last_run_flags=stored)["last_run_flags"] == stored
