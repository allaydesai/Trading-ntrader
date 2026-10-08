"""Story 5.2 — a sealed session's results, reconstructed from its trade rows.

Component tier: :func:`reconstruct_session_results` builds a real ``Portfolio``
over a fresh ``Cache`` (story F11) and drives Story 5.1's ``LiveHost.settle``
and the unmodified ``ResultsExtractor``. Every expectation is computed by hand
from the rows below, never read back from the code under test.

Never constructs a ``TradingNode`` or ``BacktestEngine``; the autouse guard is
copied verbatim from ``test_results_extractor_live_host.py``.
"""

import math
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from nautilus_trader.analysis.analyzer import PortfolioAnalyzer
from nautilus_trader.common.component import is_logging_initialized
from nautilus_trader.model.currencies import USD

from src.core.results_extractor import _Ledger
from src.core.session_equity import (
    EQUITY_SERIES_KEY,
    _legs_from,
    reconstruct_session_results,
)
from src.db.models.trade import Trade as TradeRow
from src.models.trade import Trade
from src.services.backtest_persistence import BacktestPersistenceService

pytestmark = pytest.mark.component

SOURCE = "_bmad-output/planning-artifacts/prd-epic5-scope.md"
STARTING = Decimal("100000")
#: One year, so CAGR and Calmar are computed rather than ``None``.
START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2027, 1, 1, tzinfo=UTC)

#: (side, entry, exit, profit_loss, close day). One close a day: Nautilus's
#: returns statistics bin by day (Story 5.1 Debug Log). P&L is deliberately not
#: proportional to the price return — the row's own ``profit_loss`` is the P&L.
SESSION = (
    ("BUY", "100", "110", "1000.00", 2),  # +10%
    ("BUY", "100", "95", "-500.00", 3),  # -5%
    ("SELL", "100", "90", "1000.00", 4),  # short, +10%
    ("BUY", "100", "80", "-2000.00", 5),  # -20%: the drawdown, after the first peak
    ("BUY", "100", "104", "400.00", 6),  # +4%
)
#: Starting capital × the compounded per-trade price returns (R1b), by hand.
CURVE_VALUES = [110000.0, 104500.0, 114950.0, 91960.0, 95638.4]
#: 0.9196 / 1.1495 - 1, by hand.
MAX_DRAWDOWN = -0.2
FINAL = 100000.0 + 1000 - 500 + 1000 - 2000 + 400


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Copied verbatim from ``test_live_order_path.py:91-101``."""
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — this file must never construct a "
        "TradingNode."
    )


def _closed_at(day: int) -> datetime:
    return datetime(2026, 1, day, 15, 0, tzinfo=UTC)


def _row(trade_id, side, entry, exit_, pnl, day, *, cls=Trade, **overrides):
    """A session trade row — the domain ``Trade`` by default, the ORM row if asked."""
    fields = {
        "id": trade_id,
        "session_id": 1,
        "instrument_id": "AAPL.NASDAQ",
        "trade_id": f"AAPL.NASDAQ-SMACrossover-000:{trade_id}",
        "venue_order_id": f"O-{trade_id}-open",
        "client_order_id": f"O-{trade_id}-close",
        "order_side": side,
        "quantity": Decimal("100"),
        "entry_price": Decimal(entry),
        "exit_price": Decimal(exit_) if exit_ is not None else None,
        "commission_amount": Decimal("1.00"),
        "commission_currency": "USD",
        "profit_loss": Decimal(pnl) if pnl is not None else None,
        "entry_timestamp": _closed_at(day).replace(hour=14),
        "exit_timestamp": _closed_at(day) if exit_ is not None else None,
        "created_at": _closed_at(day),
    }
    fields.update(overrides)
    return cls(**fields)


def _session_rows(cls=Trade):
    return [_row(i, *spec, cls=cls) for i, spec in enumerate(SESSION, start=1)]


def _reconstruct(rows):
    return reconstruct_session_results(rows, starting_capital=STARTING, start=START, end=END)


class TestAHandComputedSession:
    """AC #1, #2, #5: the record a backtest's own extractor would produce."""

    def test_trade_counts_and_balances(self):
        result = _reconstruct(_session_rows()).result
        assert (result.total_trades, result.winning_trades, result.losing_trades) == (5, 3, 2)
        assert (result.largest_win, result.largest_loss) == (1000.0, -2000.0)
        assert result.final_balance == pytest.approx(FINAL, abs=1e-9)
        assert result.total_return == pytest.approx((FINAL - 100000.0) / 100000.0, abs=1e-12)
        assert result.total_pnl == pytest.approx(-100.0, abs=1e-9)
        assert result.expectancy == pytest.approx(-100.0 / 5, abs=1e-9)

    @pytest.mark.acceptance_criterion(
        "5.2b", "max drawdown, CAGR and Calmar come from the extractor's code path", SOURCE
    )
    def test_drawdown_cagr_and_calmar_are_computed_from_the_series(self):
        result = _reconstruct(_session_rows()).result
        years = (END - START).days / 365.25
        cagr = (FINAL / 100000.0) ** (1 / years) - 1
        assert result.max_drawdown == pytest.approx(MAX_DRAWDOWN, rel=1e-12)
        assert result.cagr == pytest.approx(cagr, rel=1e-12)
        assert result.calmar_ratio == pytest.approx(cagr / abs(MAX_DRAWDOWN), rel=1e-12)

    def test_the_returns_statistics_are_computed_not_missing(self):
        result = _reconstruct(_session_rows()).result
        assert result.profit_factor == pytest.approx(0.24 / 0.25, rel=1e-12)
        assert result.sharpe_ratio is not None
        assert result.avg_win_return == pytest.approx(0.08, rel=1e-12)

    @pytest.mark.acceptance_criterion(
        "5.2a", "the equity series is starting capital plus the closed-trade sequence", SOURCE
    )
    def test_the_series_is_starting_capital_compounded_over_the_closed_trades(self):
        series = _reconstruct(_session_rows()).session_conditions[EQUITY_SERIES_KEY]
        times = [int(_closed_at(day).timestamp()) for *_, day in SESSION]
        assert series["curve"] == [
            {"time": time, "value": value} for time, value in zip(times, CURVE_VALUES, strict=True)
        ]
        assert series["closed_trades"] == 5
        assert series["granularity"] == "closed_trade"
        assert series["basis"] == "per_trade_price_return"
        assert series["excludes"] == ["open_positions", "reconciliation_owned_activity"]

    def test_an_orm_row_reconstructs_exactly_as_the_domain_row(self):
        assert _reconstruct(_session_rows(TradeRow)).result.to_dict() | {
            "result_id": None
        } == _reconstruct(_session_rows()).result.to_dict() | {"result_id": None}

    def test_an_open_row_beside_the_closed_ones_changes_nothing(self):
        open_row = _row(99, "BUY", "100", None, None, 7)
        with_open = _reconstruct([*_session_rows(), open_row])
        without = _reconstruct(_session_rows())
        assert with_open.result.to_dict() | {"result_id": None} == (
            without.result.to_dict() | {"result_id": None}
        )
        assert with_open.session_conditions == without.session_conditions


class TestZeroClosedTrades:
    """AC #4, PO ruling R3a: an explicit zero-trade record, never NaN."""

    @pytest.mark.acceptance_criterion(
        "5.2d", "zero closed trades: an explicit zero-trade record, no NaN", SOURCE
    )
    @pytest.mark.parametrize(
        "rows",
        [[], [_row(1, "BUY", "100", None, None, 2)]],
        ids=["no-rows", "open-rows-only"],
    )
    def test_the_record_is_explicit_and_every_float_is_finite_or_absent(self, rows):
        reconstructed = _reconstruct(rows)
        result = reconstructed.result
        series = reconstructed.session_conditions[EQUITY_SERIES_KEY]
        assert series["closed_trades"] == 0
        assert series["curve"] == []
        assert result.total_trades == 0
        assert result.max_drawdown is None
        assert result.calmar_ratio is None
        assert result.final_balance == 100000.0
        for name, value in result.to_dict().items():
            assert not isinstance(value, float) or math.isfinite(value), name

    def test_the_persistence_services_validation_accepts_it_verbatim(self):
        """AR12: NaN/Infinity validation reused as is — and it finds nothing to refuse."""
        result = _reconstruct([]).result
        metrics = BacktestPersistenceService(MagicMock())._extract_and_validate_metrics(result)
        assert metrics["total_trades"] == metrics["winning_trades"] == 0
        assert metrics["win_rate"] is None
        assert metrics["max_drawdown"] is None


class TestTheAnalyzerReadsOnlyFourMembersOfALeg:
    """D-B canary: a ``_TradeLeg`` offers the analyzer ``id``, ``realized_pnl``,
    ``ts_closed`` and ``realized_return`` (plus what ``settle`` reads) — no more."""

    def test_the_analyzer_reads_only_four_members_of_a_leg(self):
        legs = _legs_from(_session_rows())
        analyzer = PortfolioAnalyzer()
        try:
            analyzer.calculate_statistics(_Ledger.over(legs, STARTING), legs)
        except AttributeError as error:  # pragma: no cover - the canary's red path
            pytest.fail(
                f"PortfolioAnalyzer now reads a leg member _TradeLeg does not offer ({error}). "
                "A Nautilus upgrade changed calculate_statistics: add the member to _TradeLeg "
                "from the trade row, or refuse — never default it."
            )
        assert len(analyzer.returns()) == len(SESSION)
        assert analyzer.realized_pnls(USD).sum() == pytest.approx(-100.0, abs=1e-9)


class TestTradesClosingAtTheSameInstant:
    """F2: ``add_return`` *sums* the returns of legs closing at the identical
    timestamp, so they share one curve point; every trade still counts once.
    (Distinct from the daily binning of the returns *statistics* above.)"""

    def test_both_trades_count_and_both_reach_the_ledger(self):
        rows = [
            _row(1, "BUY", "100", "110", "1000.00", 2),
            _row(2, "BUY", "100", "90", "-1000.00", 2),
            _row(3, "BUY", "100", "105", "500.00", 3),
        ]
        reconstructed = _reconstruct(rows)
        result = reconstructed.result
        assert result.total_trades == 3
        assert result.final_balance == pytest.approx(100500.0, abs=1e-9)
        assert reconstructed.session_conditions[EQUITY_SERIES_KEY]["closed_trades"] == 3
        # +10% and -10% at one instant sum to 0.0 (not 1.1 × 0.9 = 0.99): one
        # point at the starting capital, then +5% — exactly as a backtest's curve.
        assert reconstructed.session_conditions[EQUITY_SERIES_KEY]["curve"] == [
            {"time": int(_closed_at(2).timestamp()), "value": 100000.0},
            {"time": int(_closed_at(3).timestamp()), "value": 105000.0},
        ]


class TestStartingCapitalIsRefusedThroughTheEntryPoint:
    """AC #6 (review patch 7): Story 5.1's capital rule reaches the caller of
    :func:`reconstruct_session_results` unchanged — never coerced or quantized."""

    @pytest.mark.parametrize(
        ("capital", "error", "match"),
        [
            (100000.0, TypeError, "Decimal"),
            (Decimal("0"), ValueError, "positive"),
            (Decimal("-1"), ValueError, "positive"),
            (Decimal("100000.001"), ValueError, "whole cents"),
        ],
    )
    def test_capital_a_record_cannot_carry_as_is_refuses(self, capital, error, match):
        with pytest.raises(error, match=match):
            reconstruct_session_results(
                _session_rows(), starting_capital=capital, start=START, end=END
            )
