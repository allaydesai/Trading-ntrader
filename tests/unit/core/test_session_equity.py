"""Story 5.2 — reconstruct a sealed session's results from its trade rows.

Unit tier: the row → leg conversion, the refusals, the ``session_conditions``
fragment, and two structural pins. No ``Portfolio``, no engine: anything that
settles a real analyzer is in ``tests/component/core/test_session_equity.py``.
"""

import ast
import calendar
import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.objects import Money

from src.core import session_equity
from src.core.live_trade_recorder import RECONCILIATION_STRATEGY_IDS
from src.core.results_extractor import _calculate_max_drawdown
from src.core.session_equity import (
    EQUITY_SERIES_KEY,
    SessionResults,
    _legs_from,
    _require_finite,
    _session_conditions,
    _TradeLeg,
    reconstruct_session_results,
)
from src.models.backtest_result import BacktestResult

pytestmark = pytest.mark.unit

SOURCE = "_bmad-output/planning-artifacts/prd-epic5-scope.md"
START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2027, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class _Row:
    """The seven members of a trade row the reconstruction reads (``SessionTradeRow``)."""

    id: int = 1
    order_side: str = "BUY"
    entry_price: Decimal = Decimal("100")
    exit_price: Decimal | None = Decimal("110")
    profit_loss: Decimal | None = Decimal("1000.00")
    exit_timestamp: datetime | None = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    commission_currency: str | None = "USD"


class TestTradeLeg:
    """D-B: a row becomes the leg Nautilus would have analysed (R1b)."""

    def test_a_long_rows_return_is_exit_minus_entry_over_entry(self):
        leg = _TradeLeg.from_row(
            _Row(order_side="BUY", entry_price=Decimal("100"), exit_price=Decimal("110"))
        )
        assert leg.realized_return == pytest.approx(0.10, abs=1e-15)

    @pytest.mark.parametrize(
        ("exit_price", "expected"), [(Decimal("90"), 0.10), (Decimal("110"), -0.10)]
    )
    def test_a_short_rows_return_is_entry_minus_exit_over_entry(self, exit_price, expected):
        leg = _TradeLeg.from_row(_Row(order_side="SELL", exit_price=exit_price))
        assert leg.realized_return == pytest.approx(expected, abs=1e-15)

    def test_the_leg_carries_the_rows_profit_loss_as_usd_money(self):
        leg = _TradeLeg.from_row(_Row(profit_loss=Decimal("-123.45000000")))
        assert leg.realized_pnl == Money(Decimal("-123.45"), USD)

    def test_ts_closed_is_exact_integer_nanoseconds(self):
        stamp = datetime(2026, 1, 5, 14, 30, 7, 123456, tzinfo=UTC)
        leg = _TradeLeg.from_row(_Row(exit_timestamp=stamp))
        expected = calendar.timegm(stamp.timetuple()) * 1_000_000_000 + 123_456_000
        assert leg.ts_closed == expected

    def test_a_naive_exit_timestamp_is_read_as_utc(self):
        aware = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
        naive = aware.replace(tzinfo=None)
        assert (
            _TradeLeg.from_row(_Row(exit_timestamp=naive)).ts_closed
            == _TradeLeg.from_row(_Row(exit_timestamp=aware)).ts_closed
        )

    def test_the_leg_id_is_the_rows_database_id(self):
        """F8: the DB id is the only key unique per session; the analyzer keys P&L by it."""
        assert _TradeLeg.from_row(_Row(id=42)).id.value == "trade-42"

    def test_the_leg_is_strategy_owned_and_closed(self):
        leg = _TradeLeg.from_row(_Row())
        assert str(leg.strategy_id) not in RECONCILIATION_STRATEGY_IDS
        assert leg.is_closed is True
        assert leg.is_open is False


class TestRefusals:
    """D-E: a row that cannot be read honestly refuses, naming the trade (AC #6)."""

    @pytest.mark.parametrize(
        ("change", "reason"),
        [
            ({"exit_timestamp": None}, "exit_timestamp"),
            ({"profit_loss": None}, "profit_loss"),
            ({"exit_price": None}, "exit_price"),
            ({"commission_currency": "EUR"}, "EUR"),
            ({"order_side": "HOLD"}, "HOLD"),
            ({"entry_price": Decimal("0")}, "entry_price"),
        ],
    )
    def test_an_unreadable_closed_row_refuses_naming_the_trade(self, change, reason):
        with pytest.raises(ValueError, match=rf"trade 77\b.*{reason}"):
            _TradeLeg.from_row(replace(_Row(id=77), **change))

    def test_an_unknown_commission_currency_is_not_a_refusal(self):
        """``None`` means the commission was unknown (Story 3.5), not foreign."""
        assert _TradeLeg.from_row(_Row(commission_currency=None)).realized_pnl.currency == USD

    @pytest.mark.parametrize("end", [START, datetime(2025, 12, 31, tzinfo=UTC)])
    def test_a_period_that_does_not_move_forward_refuses(self, end):
        with pytest.raises(ValueError, match="period"):
            reconstruct_session_results(
                [], starting_capital=Decimal("100000"), start=START, end=end
            )

    @pytest.mark.parametrize(
        ("start", "end", "named"),
        [
            (START.replace(tzinfo=None), END, "start"),
            (START, END.replace(tzinfo=None), "end"),
            (START.replace(tzinfo=None), END.replace(tzinfo=None), "start"),
        ],
    )
    def test_a_naive_period_bound_refuses_clearly_never_a_raw_type_error(self, start, end, named):
        """Review patch 6 (PO: refuse, never a raw ``TypeError``). The period comes
        from a ``TIMESTAMPTZ`` column and the seal clock; a naive bound is a caller
        bug, and comparing it with an aware one would raise ``TypeError``."""
        with pytest.raises(ValueError, match=rf"{named}.*timezone"):
            reconstruct_session_results(
                [], starting_capital=Decimal("100000"), start=start, end=end
            )

    def test_an_exit_price_without_an_exit_timestamp_refuses(self):
        """Review patch 3: never silently dropped as 'open'. A backtest open row has
        no exit price (``backtest_persistence.py:495-500``), so the exit price —
        not ``profit_loss``, which an open row carries — is what contradicts."""
        half_closed = _Row(id=31, exit_timestamp=None)
        with pytest.raises(ValueError, match=r"trade 31\b.*exit_price.*exit_timestamp"):
            _legs_from([_Row(id=1), half_closed])

    def test_an_open_row_with_a_profit_loss_is_still_open_not_refused(self):
        """The backtest convention: an open row carries ``profit_loss`` 0.00."""
        open_row = _Row(id=2, exit_timestamp=None, exit_price=None, profit_loss=Decimal("0.00"))
        assert [leg.id.value for leg in _legs_from([_Row(id=1), open_row])] == ["trade-1"]

    def test_a_repeated_row_id_refuses_naming_it(self):
        """Review patch 5: the analyzer keys P&L by ``trade-{id}`` — a repeat would be
        counted twice in the ledger and once in the P&L statistics."""
        with pytest.raises(ValueError, match=r"trade 7\b.*more than once"):
            _legs_from([_Row(id=7), _Row(id=8), _Row(id=7)])

    def test_a_repeated_open_row_id_refuses_too(self):
        open_row = _Row(id=7, exit_timestamp=None, exit_price=None)
        with pytest.raises(ValueError, match=r"trade 7\b.*more than once"):
            _legs_from([_Row(id=7), open_row])


class TestLegsFrom:
    """D-D: closed rows only, in close order, ties broken by row id."""

    def test_an_open_row_is_not_a_leg(self):
        rows = [_Row(id=1), _Row(id=2, exit_timestamp=None, exit_price=None, profit_loss=None)]
        assert [leg.id.value for leg in _legs_from(rows)] == ["trade-1"]

    def test_the_input_order_of_the_rows_does_not_change_the_legs(self):
        day = datetime(2026, 1, 5, tzinfo=UTC)
        rows = [
            _Row(id=3, exit_timestamp=day.replace(day=7)),
            _Row(id=2, exit_timestamp=day.replace(day=6)),
            _Row(id=9, exit_timestamp=day.replace(day=6)),
            _Row(id=1, exit_timestamp=day),
        ]
        expected = ["trade-1", "trade-2", "trade-9", "trade-3"]
        assert [leg.id.value for leg in _legs_from(rows)] == expected
        assert [leg.id.value for leg in _legs_from(reversed(rows))] == expected


class TestSessionConditions:
    """D-F: one namespaced key carries the series and its limitation (AC #3, #7)."""

    @pytest.mark.acceptance_criterion(
        "5.2c", "the trade-granular limitation is recorded in session_conditions", SOURCE
    )
    def test_the_fragment_records_the_series_and_its_limitation(self):
        curve = [{"time": 1, "value": 100.0}]
        assert _session_conditions(3, curve) == {
            EQUITY_SERIES_KEY: {
                "granularity": "closed_trade",
                "basis": "per_trade_price_return",
                "closed_trades": 3,
                "excludes": ["open_positions", "reconciliation_owned_activity"],
                "curve": [{"time": 1, "value": 100.0}],
            }
        }
        assert EQUITY_SERIES_KEY == "equity_series"


class TestMergedInto:
    """Review patch 1: the hand-over adds ``equity_series`` beside whatever
    ``session_conditions`` already holds (Story 5.4's flat AR7 keys) — never
    replaces the document, and never writes a second series."""

    RESULTS = SessionResults(BacktestResult(), _session_conditions(1, [{"time": 1}]))
    SNAPSHOT = {
        "strategy_path": "a",
        "config_path": "b",
        "session_conditions": {"schema_version": 1, "rth_only": True},
    }

    def test_the_series_joins_the_existing_conditions_beside_their_keys(self):
        merged = self.RESULTS.merged_into(self.SNAPSHOT)
        assert merged == {
            "strategy_path": "a",
            "config_path": "b",
            "session_conditions": {
                "schema_version": 1,
                "rth_only": True,
                EQUITY_SERIES_KEY: self.RESULTS.session_conditions[EQUITY_SERIES_KEY],
            },
        }

    def test_a_snapshot_without_conditions_gains_them(self):
        merged = self.RESULTS.merged_into({"strategy_path": "a", "config_path": "b"})
        assert merged["session_conditions"] == self.RESULTS.session_conditions

    def test_a_second_equity_series_refuses(self):
        already = {"session_conditions": {EQUITY_SERIES_KEY: {"curve": []}, "rth_only": True}}
        with pytest.raises(ValueError, match=EQUITY_SERIES_KEY):
            self.RESULTS.merged_into(already)

    def test_neither_its_input_nor_its_own_conditions_are_shared_or_changed(self):
        snapshot = {"session_conditions": {"rth_only": True}}
        merged = self.RESULTS.merged_into(snapshot)
        merged["session_conditions"]["rth_only"] = False
        merged["session_conditions"][EQUITY_SERIES_KEY]["curve"].append({"time": 2})
        assert snapshot == {"session_conditions": {"rth_only": True}}
        assert self.RESULTS.session_conditions[EQUITY_SERIES_KEY]["curve"] == [{"time": 1}]


class TestNonFiniteRefusal:
    """R3a: if the existing path ever yields NaN/Infinity, refuse instead of writing it."""

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_a_non_finite_metric_refuses_naming_the_field(self, value):
        with pytest.raises(ValueError, match="sharpe_ratio"):
            _require_finite(BacktestResult(sharpe_ratio=value))

    def test_finite_and_absent_metrics_pass(self):
        _require_finite(BacktestResult(sharpe_ratio=None, total_return=0.0))

    def test_the_guard_is_wired_into_the_reconstruction(self, monkeypatch):
        class _NaNExtractor:
            def __init__(self, engine):
                pass

            def extract_results(self, start, end):
                return BacktestResult(max_drawdown=math.nan)

            def extract_equity_curve(self, start, end):
                return []

        monkeypatch.setattr(session_equity, "_TradesSource", SimpleNamespace(over=lambda legs: 0))
        monkeypatch.setattr(
            session_equity, "LiveHost", SimpleNamespace(settle=lambda source, **_: "host")
        )
        monkeypatch.setattr(session_equity, "ResultsExtractor", _NaNExtractor)
        with pytest.raises(ValueError, match="max_drawdown"):
            reconstruct_session_results(
                [], starting_capital=Decimal("100000"), start=START, end=END
            )


class TestAnEmptyCurveBesideClosedTrades:
    """Review patch 8: ``extract_equity_curve`` swallows any exception into ``[]``;
    closed trades with no series would be a silent wrong value (D-E)."""

    @staticmethod
    def _extractor_with_curve(curve):
        class _Extractor:
            def __init__(self, engine):
                pass

            def extract_results(self, start, end):
                return BacktestResult(total_trades=1, winning_trades=1)

            def extract_equity_curve(self, start, end):
                return curve

        return _Extractor

    @pytest.fixture
    def _no_portfolio(self, monkeypatch):
        monkeypatch.setattr(session_equity, "_TradesSource", SimpleNamespace(over=lambda legs: 0))
        monkeypatch.setattr(
            session_equity, "LiveHost", SimpleNamespace(settle=lambda source, **_: "host")
        )

    @pytest.mark.usefixtures("_no_portfolio")
    def test_closed_trades_with_no_series_refuse(self, monkeypatch):
        monkeypatch.setattr(session_equity, "ResultsExtractor", self._extractor_with_curve([]))
        with pytest.raises(ValueError, match=r"1 closed trade.*no equity series"):
            reconstruct_session_results(
                [_Row()], starting_capital=Decimal("100000"), start=START, end=END
            )

    @pytest.mark.usefixtures("_no_portfolio")
    def test_the_same_trades_with_a_series_pass(self, monkeypatch):
        curve = [{"time": 1, "value": 110000.0}]
        monkeypatch.setattr(session_equity, "ResultsExtractor", self._extractor_with_curve(curve))
        sealed = reconstruct_session_results(
            [_Row()], starting_capital=Decimal("100000"), start=START, end=END
        )
        assert sealed.session_conditions[EQUITY_SERIES_KEY]["curve"] == curve


MODULE_PATH = Path(session_equity.__file__)
_NO_METRIC_MATH = {"cumprod", "expanding", "pct_change", "cummax", "resample"}


def _references(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name.split(".")[-1])
    return names


class TestNoMetricMathHere:
    """AC #2: the module computes no metric — the extractor's own code path does."""

    @pytest.mark.acceptance_criterion(
        "5.2b", "max drawdown, CAGR and Calmar come from the extractor's code path", SOURCE
    )
    def test_the_module_has_no_metric_arithmetic_of_its_own(self):
        tree = ast.parse(MODULE_PATH.read_text())
        references = _references(tree)
        assert not references & _NO_METRIC_MATH
        assert not {name for name in references if name.startswith("_calculate_")}
        assert not [node for node in ast.walk(tree) if isinstance(node, ast.Pow)]

    def test_the_pin_is_not_vacuous_the_module_drives_the_extractor(self):
        references = _references(ast.parse(MODULE_PATH.read_text()))
        assert {"LiveHost", "settle", "ResultsExtractor", "extract_results"} <= references


class TestPreExistingDrawdownBehaviour:
    """R4a: ``_calculate_max_drawdown`` is kept as is, and pinned.

    Pre-existing on both sides (backtest and paper), recorded in deferred-work
    ("Deferred from: story-5.2"): ``(1 + r).cumprod()`` starts after the first
    trade, so a loss before the first peak is never measured against the
    starting capital. A both-sides fix changes every future backtest whose first
    trade loses, so it is an epic-level decision, not this story's.
    """

    @pytest.mark.parametrize(
        ("returns", "pinned", "true_from_capital"),
        [([-0.10, -0.05], -0.05, -0.145), ([-0.10, 0.05], 0.0, -0.10)],
    )
    def test_a_loss_before_the_first_peak_is_not_measured(self, returns, pinned, true_from_capital):
        analyzer = SimpleNamespace(returns=lambda: pd.Series(returns))
        measured = _calculate_max_drawdown(analyzer)
        assert measured == pytest.approx(pinned, abs=1e-12), (
            "pre-existing, both sides: see deferred-work 'Deferred from: story-5.2'. "
            "If this changed on purpose, the backtest side changed too — update both."
        )
        assert measured != pytest.approx(true_from_capital)
