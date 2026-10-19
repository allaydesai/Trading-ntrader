"""Tests for ResultsExtractor."""

import ast
import inspect
import textwrap
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from nautilus_trader.analysis.analyzer import PortfolioAnalyzer
from nautilus_trader.model.currencies import EUR, USD
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money

from src.core.live_trade_recorder import RECONCILIATION_STRATEGY_IDS
from src.core.results_extractor import (
    LiveHost,
    ResultsExtractor,
    _calculate_cagr,
    _calculate_calmar_ratio,
    _calculate_max_drawdown,
    _ClosedLegs,
    _final_balance,
    _Ledger,
    _safe_float,
    _SettledPortfolio,
    _side_stat,
    _starting_balance,
)
from src.models.backtest_result import BacktestResult

#: Story 5.1's acceptance criteria (PR #36 review P7: traced like 5.2–5.4's).
EPIC5_PRD = "_bmad-output/planning-artifacts/prd-epic5-scope.md"


class TestSafeFloat:
    """Tests for _safe_float helper function."""

    def test_safe_float_with_valid_number(self):
        """Test safe_float with valid number."""
        assert _safe_float(1.5) == 1.5

    def test_safe_float_with_integer(self):
        """Test safe_float with integer."""
        assert _safe_float(10) == 10.0

    def test_safe_float_with_string_number(self):
        """Test safe_float with string number."""
        assert _safe_float("3.14") == 3.14

    def test_safe_float_with_none(self):
        """Test safe_float with None."""
        assert _safe_float(None) is None

    def test_safe_float_with_empty_string(self):
        """Test safe_float with empty string."""
        assert _safe_float("") is None

    def test_safe_float_with_nan(self):
        """Test safe_float with NaN."""
        assert _safe_float(float("nan")) is None

    def test_safe_float_with_infinity(self):
        """Test safe_float with infinity."""
        assert _safe_float(float("inf")) is None
        assert _safe_float(float("-inf")) is None

    def test_safe_float_with_invalid_string(self):
        """Test safe_float with invalid string."""
        assert _safe_float("not_a_number") is None


class TestSideStat:
    """A winners-only or losers-only statistic means nothing when that side never traded."""

    def test_no_trades_on_that_side_is_none_not_zero(self):
        """Nautilus reports 0.0 for 'Avg Loser' with no losers: that reads as a real average."""
        assert _side_stat({"Avg Loser": 0.0}, "Avg Loser", traded=False) is None

    def test_a_side_that_traded_keeps_its_value(self):
        assert _side_stat({"Avg Loser": -850.25}, "Avg Loser", traded=True) == -850.25

    def test_a_missing_statistic_is_none(self):
        assert _side_stat({}, "Avg Loser", traded=True) is None


class TestCalculateCagr:
    """Tests for _calculate_cagr helper function."""

    def test_calculate_cagr_positive_return(self):
        """Test CAGR calculation with positive return."""
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2021, 1, 1, tzinfo=timezone.utc)
        cagr = _calculate_cagr(100000, 110000, start, end)
        assert cagr is not None
        assert abs(cagr - 0.1) < 0.01  # ~10% CAGR

    def test_calculate_cagr_negative_return(self):
        """Test CAGR calculation with negative return."""
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2021, 1, 1, tzinfo=timezone.utc)
        cagr = _calculate_cagr(100000, 90000, start, end)
        assert cagr is not None
        assert cagr < 0

    def test_calculate_cagr_multi_year(self):
        """Test CAGR calculation over multiple years."""
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2022, 1, 1, tzinfo=timezone.utc)
        # Double in 2 years = ~41% CAGR
        cagr = _calculate_cagr(100000, 200000, start, end)
        assert cagr is not None
        assert abs(cagr - 0.41) < 0.02

    def test_calculate_cagr_zero_starting_balance(self):
        """Test CAGR returns None for zero starting balance."""
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2021, 1, 1, tzinfo=timezone.utc)
        assert _calculate_cagr(0, 100000, start, end) is None

    def test_calculate_cagr_zero_final_balance(self):
        """Test CAGR returns None for zero final balance."""
        start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        end = datetime(2021, 1, 1, tzinfo=timezone.utc)
        assert _calculate_cagr(100000, 0, start, end) is None

    def test_calculate_cagr_same_date(self):
        """Test CAGR returns None for same start/end date."""
        date = datetime(2020, 1, 1, tzinfo=timezone.utc)
        assert _calculate_cagr(100000, 110000, date, date) is None


class TestCalculateCalmarRatio:
    """Tests for _calculate_calmar_ratio helper function."""

    def test_calmar_ratio_positive(self):
        """Test Calmar ratio with positive CAGR and negative drawdown."""
        calmar = _calculate_calmar_ratio(0.15, -0.10)  # 15% CAGR, -10% drawdown
        assert calmar is not None
        assert abs(calmar - 1.5) < 0.01

    def test_calmar_ratio_none_cagr(self):
        """Test Calmar ratio with None CAGR."""
        assert _calculate_calmar_ratio(None, -0.10) is None

    def test_calmar_ratio_none_drawdown(self):
        """Test Calmar ratio with None max drawdown."""
        assert _calculate_calmar_ratio(0.15, None) is None

    def test_calmar_ratio_zero_drawdown(self):
        """Test Calmar ratio with zero max drawdown."""
        assert _calculate_calmar_ratio(0.15, 0.0) is None


class TestCalculateMaxDrawdown:
    """Tests for _calculate_max_drawdown helper function."""

    def test_max_drawdown_with_returns(self):
        """Test max drawdown calculation with returns series."""
        import pandas as pd

        mock_analyzer = MagicMock()
        # Simulate returns that go up then down: 10%, -5%, -10%, 5%
        returns = pd.Series([0.10, -0.05, -0.10, 0.05])
        mock_analyzer.returns.return_value = returns

        drawdown = _calculate_max_drawdown(mock_analyzer)
        assert drawdown is not None
        assert drawdown < 0  # Drawdown should be negative

    def test_max_drawdown_empty_returns(self):
        """Test max drawdown with empty returns."""
        import pandas as pd

        mock_analyzer = MagicMock()
        mock_analyzer.returns.return_value = pd.Series([])

        assert _calculate_max_drawdown(mock_analyzer) is None

    def test_max_drawdown_none_returns(self):
        """Test max drawdown with None returns."""
        mock_analyzer = MagicMock()
        mock_analyzer.returns.return_value = None

        assert _calculate_max_drawdown(mock_analyzer) is None


class TestResultsExtractor:
    """Tests for ResultsExtractor class."""

    @pytest.fixture
    def mock_engine(self):
        """Create a mock backtest engine."""
        engine = MagicMock()
        return engine

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.default_balance = 1000000
        return settings

    def test_extract_results_no_engine(self, mock_settings):
        """Test extract_results returns empty result when no engine."""
        extractor = ResultsExtractor(engine=None, settings=mock_settings)
        result = extractor.extract_results()

        assert result.total_trades == 0
        assert result.total_return == 0.0

    def test_extract_results_no_account(self, mock_engine, mock_settings):
        """Test extract_results when no account found."""
        mock_engine.cache.account_for_venue.return_value = None
        extractor = ResultsExtractor(engine=mock_engine, settings=mock_settings)
        result = extractor.extract_results()

        assert result.total_trades == 0

    def test_extract_equity_curve_no_engine(self, mock_settings):
        """Test extract_equity_curve returns empty list when no engine."""
        extractor = ResultsExtractor(engine=None, settings=mock_settings)
        curve = extractor.extract_equity_curve()

        assert curve == []


# --- Story 5.1: the live host --------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[3]
STARTING = Decimal("100000")


class _Untouchable:
    """A host component any attribute read of which fails the test — the proof that a
    code path never reaches it (no account, no balance, nothing)."""

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_name", name)

    def __getattr__(self, attribute: str):
        raise AssertionError(f"{self._name}.{attribute} was read")


def _live_host(*, starting=STARTING, final="100000", cache=None, portfolio=None) -> LiveHost:
    return LiveHost(
        portfolio=portfolio or _SettledPortfolio(PortfolioAnalyzer()),
        cache=cache if cache is not None else _ClosedLegs(()),
        starting_balance=starting,
        final_balance=Money(Decimal(final), USD),
    )


def _leg(pnl, *, strategy="SMACrossover-000", closed=True, ts_closed=1):
    """A position-shaped leg. An open one has ``ts_closed == 0``, as Nautilus's does."""
    return SimpleNamespace(
        strategy_id=strategy,
        realized_pnl=pnl,
        is_closed=closed,
        is_open=not closed,
        ts_closed=ts_closed if closed else 0,
    )


def _usd(amount: str) -> Money:
    return Money(Decimal(amount), USD)


def _settings(default_balance=1_000_000):
    return SimpleNamespace(default_balance=default_balance)


@pytest.mark.unit
class TestStartingBalanceSource:
    """AC #10 / D-F — a live host's capital is its own; a backtest's rule is unchanged."""

    def test_a_backtest_host_keeps_an_explicit_starting_balance(self):
        assert _starting_balance(MagicMock(), 50_000.0, _settings()) == 50_000.0

    def test_a_backtest_host_defaults_to_the_settings_balance(self):
        assert _starting_balance(MagicMock(), None, _settings(1_000_000)) == 1_000_000.0

    def test_a_live_host_uses_its_own_capital_never_the_settings_default(self):
        host = _live_host(starting=Decimal("25000"))

        assert _starting_balance(host, None, _settings(1_000_000)) == 25_000.0

    def test_a_live_host_accepts_an_agreeing_explicit_value(self):
        assert _starting_balance(_live_host(), 100_000.0, _settings()) == 100_000.0

    def test_a_live_host_refuses_a_disagreeing_explicit_value(self):
        with pytest.raises(ValueError, match="starting"):
            _starting_balance(_live_host(), 99_999.0, _settings())

    def test_an_agreeing_decimal_is_not_refused_as_different(self):
        """Review 2026-10-07: ``Decimal("100000.10") != 100000.1`` exactly — compare as floats."""
        host = _live_host(starting=Decimal("100000.10"))

        assert _starting_balance(host, Decimal("100000.10"), _settings()) == 100_000.1

    def test_a_raw_trading_node_is_refused(self):
        """Review 2026-10-07: unsettled, a node would be read through the backtest path —
        ``account_for_venue(...).balance_total(USD)``, which for IB is NetLiquidation."""
        from nautilus_trader.live.node import TradingNode

        node = object.__new__(TradingNode)  # no __init__: nothing connects, nothing logs

        with pytest.raises(TypeError, match="LiveHost.settle"):
            ResultsExtractor(engine=node, settings=_settings())

    def test_a_trading_node_subclass_is_refused_too(self):
        from nautilus_trader.live.node import TradingNode

        node = object.__new__(type("SessionNode", (TradingNode,), {}))

        with pytest.raises(TypeError, match="NetLiquidation"):
            _starting_balance(node, None, _settings())

    def test_the_extractor_takes_a_live_hosts_capital(self):
        extractor = ResultsExtractor(
            engine=_live_host(starting=Decimal("25000")), settings=_settings(1_000_000)
        )

        assert extractor._starting_balance == 25_000.0


@pytest.mark.unit
class TestFinalBalanceSource:
    """AC #5 — a live host's final balance is its ledger; nothing account-shaped is read."""

    def test_a_live_host_reads_its_ledger_and_nothing_else(self):
        host = _live_host(
            final="159404.73",
            cache=_Untouchable("cache"),
            portfolio=_Untouchable("portfolio"),
        )

        assert _final_balance(host, Venue("SIM")) == 159_404.73

    def test_a_backtest_host_without_an_account_has_no_final_balance(self):
        engine = MagicMock()
        engine.cache.account_for_venue.return_value = None

        assert _final_balance(engine, Venue("SIM")) is None

    def test_a_backtest_host_reads_its_accounts_usd_total(self):
        engine = MagicMock()
        engine.cache.account_for_venue.return_value.balance_total.return_value = Money(
            Decimal("123.45"), USD
        )

        assert _final_balance(engine, Venue("SIM")) == 123.45
        engine.cache.account_for_venue.assert_called_once_with(Venue("SIM"))


@pytest.mark.unit
class TestLedger:
    """D-B / D-C / D-G — the ledger the analyzer reads in place of an account."""

    def test_final_is_starting_plus_every_realized_pnl(self):
        legs = [
            _leg(Money(Decimal("100.50"), USD)),
            _leg(Money(Decimal("-40.25"), USD)),
            _leg(Money(Decimal("0.00"), USD), closed=False),
        ]

        ledger = _Ledger.over(legs, STARTING)

        assert ledger.starting == Money(STARTING, USD)
        assert ledger.final == Money(Decimal("100060.25"), USD)

    def test_a_loss_beyond_the_capital_is_recorded_not_refused(self):
        ledger = _Ledger.over([_leg(Money(Decimal("-150000"), USD))], STARTING)

        assert ledger.final == Money(Decimal("-50000"), USD)

    def test_a_non_usd_leg_refuses_naming_the_currency(self):
        with pytest.raises(ValueError, match="EUR"):
            _Ledger.over([_leg(Money(Decimal("10"), EUR))], STARTING)

    def test_it_exposes_exactly_the_two_balance_reads_the_analyzer_makes(self):
        ledger = _Ledger.over([], STARTING)

        assert ledger.starting_balances() == {USD: Money(STARTING, USD)}
        assert ledger.balances_total() == {USD: Money(STARTING, USD)}
        public = {name for name in dir(ledger) if not name.startswith("_")}
        assert public == {"starting", "final", "over", "starting_balances", "balances_total"}


@pytest.mark.unit
class TestClosedLegs:
    """D-E — the one cache read the extractor makes, and nothing account-shaped."""

    def test_it_answers_closed_legs_only(self):
        closed, still_open = _leg(_usd("1")), _leg(_usd("-1"), closed=False)

        assert _ClosedLegs((closed, still_open)).positions_closed() == [closed]

    @pytest.mark.parametrize("member", ["account_for_venue", "account", "accounts"])
    def test_it_offers_no_account(self, member):
        assert not hasattr(_ClosedLegs(()), member)


class _SpyAnalyzer:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def calculate_statistics(self, account, positions) -> None:
        self.calls.append((account, list(positions)))


def _source(positions=(), snapshots=(), analyzer=None):
    return SimpleNamespace(
        portfolio=SimpleNamespace(analyzer=analyzer or _SpyAnalyzer()),
        cache=SimpleNamespace(
            positions=lambda: list(positions), position_snapshots=lambda: list(snapshots)
        ),
    )


@pytest.mark.unit
class TestSettle:
    """D-B / D-D — settle collects the legs, builds the ledger, primes a copy of the
    analyzer once, and writes nothing to its source (code review 2026-10-07)."""

    def test_it_primes_a_copy_of_the_analyzer_with_the_ledger_and_every_leg(self):
        current = _leg(_usd("10"), ts_closed=2)
        snapshot = _leg(_usd("-3"), ts_closed=1)
        source = _source(positions=[current], snapshots=[snapshot])

        host = LiveHost.settle(source, starting_balance=STARTING)

        [(ledger, legs)] = host.portfolio.analyzer.calls
        assert legs == [snapshot, current]
        assert ledger.balances_total() == {USD: _usd("100007")}
        assert host.final_balance == _usd("100007")
        assert host.cache.positions_closed() == [snapshot, current]

    def test_it_writes_nothing_to_its_source(self):
        source = _source(positions=[_leg(_usd("10"))])

        host = LiveHost.settle(source, starting_balance=STARTING)

        assert source.portfolio.analyzer.calls == []
        assert host.portfolio is not source.portfolio
        assert host.portfolio.analyzer is not source.portfolio.analyzer

    def test_the_settled_portfolio_offers_no_account(self):
        """The source portfolio's ``account(venue)`` would return the IB account."""
        host = LiveHost.settle(_source(), starting_balance=STARTING)

        assert {n for n in dir(host.portfolio) if not n.startswith("_")} == {"analyzer"}

    def test_the_legs_are_copies_so_a_later_change_to_the_source_is_not_seen(self):
        leg = _leg(_usd("10"))
        host = LiveHost.settle(_source(positions=[leg]), starting_balance=STARTING)

        leg.realized_pnl, leg.is_closed = _usd("-999"), False

        [settled] = host.cache.legs
        assert (settled.realized_pnl, settled.is_closed) == (_usd("10"), True)

    def test_legs_are_put_in_close_order_with_open_legs_last(self):
        """Review 2026-10-07: NETTING presents legs by position id with snapshots after,
        so the returns series (and max drawdown) would follow id order, not time."""
        late, early, middle = (
            _leg(_usd(p), ts_closed=t) for p, t in (("1", 30), ("2", 10), ("3", 20))
        )
        still_open = _leg(_usd("-1"), closed=False)
        source = _source(positions=[still_open, late], snapshots=[early, middle])

        host = LiveHost.settle(source, starting_balance=STARTING)

        assert [leg.realized_pnl for leg in host.cache.legs] == [
            _usd("2"),
            _usd("3"),
            _usd("1"),
            _usd("-1"),
        ]

    @pytest.mark.parametrize("owner", sorted(RECONCILIATION_STRATEGY_IDS))
    def test_a_reconciliation_owned_leg_is_left_out_of_everything(self, owner):
        own = _leg(_usd("10"))
        reconciled = _leg(_usd("999"), strategy=owner)
        source = _source(positions=[own, reconciled])

        host = LiveHost.settle(source, starting_balance=STARTING)

        [(_ledger, legs)] = host.portfolio.analyzer.calls
        assert legs == [own]
        assert host.final_balance == _usd("100010")
        assert host.cache.positions_closed() == [own]

    def test_starting_capital_is_required(self):
        with pytest.raises(TypeError):
            LiveHost.settle(_source())  # type: ignore[call-arg]

    def test_starting_capital_must_be_a_decimal(self):
        with pytest.raises(TypeError, match="Decimal, not float"):
            LiveHost.settle(_source(), starting_balance=100_000.0)  # type: ignore[arg-type]

    @pytest.mark.parametrize("capital", ["0", "-100000", "NaN", "Infinity"])
    def test_starting_capital_must_be_positive_and_finite(self, capital):
        """Zero would divide ``total_return`` by zero; a negative one flips every sign."""
        with pytest.raises(ValueError, match="positive"):
            LiveHost.settle(_source(), starting_balance=Decimal(capital))

    def test_starting_capital_must_be_whole_cents(self):
        """``Money`` rounds to cents for the analyzer; ``total_return`` would not."""
        with pytest.raises(ValueError, match="whole cents"):
            LiveHost.settle(_source(), starting_balance=Decimal("100000.005"))

    def test_whole_cents_are_accepted(self):
        host = LiveHost.settle(_source(), starting_balance=Decimal("100000.10"))

        assert host.final_balance == _usd("100000.10")

    def test_a_settled_host_is_immutable(self):
        host = LiveHost.settle(_source(), starting_balance=STARTING)

        with pytest.raises(AttributeError):
            host.starting_balance = Decimal("1")  # type: ignore[misc]


def _body_references(function) -> set[str]:
    """Every name and attribute a function's *body* references — ``LiveHost`` and
    ``module.LiveHost`` alike — with the signature (whose ``engine`` annotation
    legitimately names ``LiveHost``) and the docstring excluded."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    definition = tree.body[0]
    assert isinstance(definition, ast.FunctionDef)
    body = definition.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    references: set[str] = set()
    for statement in body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Name):
                references.add(node.id)
            elif isinstance(node, ast.Attribute):
                references.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                references.add(node.value)  # type(host).__name__ == "LiveHost"
    return references


#: Every method the extractor has — derived from the class, not kept by hand, so a
#: method added later is scanned too (code review 2026-10-07).
EXTRACTOR_METHODS = sorted(
    name for name, member in vars(ResultsExtractor).items() if inspect.isfunction(member)
)


@pytest.mark.unit
class TestTheDispatchIsConfinedToTwoHelpers:
    """AC #2 — the metric math is untouched; only where the balances come from changed."""

    def test_the_scanned_methods_are_every_method_the_extractor_has(self):
        """Sibling: the derived list is not empty or partial."""
        assert {
            "__init__",
            "extract_results",
            "extract_equity_curve",
            "_build_equity_curve_from_positions",
        } <= set(EXTRACTOR_METHODS)

    @pytest.mark.acceptance_criterion(
        "5.1b",
        "the metric math is untouched — the change is confined to how the host's portfolio and "
        "cache are obtained (FR41)",
        EPIC5_PRD,
    )
    @pytest.mark.parametrize("method", EXTRACTOR_METHODS)
    def test_no_extractor_method_names_the_live_host(self, method):
        references = _body_references(getattr(ResultsExtractor, method))

        assert not {"LiveHost", "_SettledPortfolio", "_ClosedLegs"} & references

    @pytest.mark.parametrize("method", ["__init__", "extract_results"])
    def test_the_two_wired_methods_do_no_type_dispatch_of_their_own(self, method):
        """``extract_equity_curve`` already checked a timestamp's type before this story,
        so ``isinstance`` is pinned only where it never belonged. (``hasattr`` is not:
        ``extract_results`` has always probed a *position* with it.)"""
        references = _body_references(getattr(ResultsExtractor, method))

        assert not {"isinstance", "type"} & references

    @pytest.mark.parametrize("helper", [_starting_balance, _final_balance])
    def test_the_two_helpers_are_where_the_host_is_told_apart(self, helper):
        """Sibling: the pins above are not vacuous — the dispatch exists, just not there."""
        assert {"isinstance", "LiveHost"} <= _body_references(helper)


@pytest.mark.unit
class TestNoParallelResultsVocabulary:
    """AC #4 / FR42 / AR43 — a paper result is a ``BacktestResult``, in the existing tables."""

    def test_a_live_host_yields_the_existing_result_class(self):
        result = ResultsExtractor(engine=_live_host(), settings=_settings()).extract_results()

        assert type(result) is BacktestResult

    @pytest.mark.acceptance_criterion(
        "5.1d",
        "no metrics table, model, or view specific to paper results is added (FR42, AR43)",
        EPIC5_PRD,
    )
    def test_the_orm_table_set_is_unchanged(self):
        import src.db.models  # noqa: F401  # registers every model on Base.metadata
        from src.db.base import Base

        # The research MCP's tables (merged in by PO ruling, 2026-10-11) hold backtest
        # research and per-run equity curves — none of them is specific to paper results.
        # research_analyses (MCP phase 4, 2026-10-13) holds a study's robustness analyses.
        assert set(Base.metadata.tables) == {
            "backtest_runs",
            "catalog_dividends",
            "catalog_instruments",
            "catalog_stock_splits",
            "instrument_metadata",
            "performance_metrics",
            "research_analyses",
            "research_candidates",
            "research_studies",
            "research_study_events",
            "research_trials",
            "run_equity_curves",
            "trades",
            "trading_sessions",
        }

    def test_there_is_one_migration_head_and_epic_5_added_none(self):
        """Epic 5 added no migration: the single head is the research MCP's, whose
        chain descends from Epic 4's ``85c949ac0374`` (PO ruling, 2026-10-11). MCP phase 4
        (2026-10-13) moved the head to ``87090e0d5660`` (research_analyses); MCP phase 5a
        (2026-10-19) to ``72a945ca06f5`` (the portfolio analysis kind); still one head."""
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))

        assert script.get_heads() == ["72a945ca06f5"]
        assert "85c949ac0374" in {rev.revision for rev in script.walk_revisions()}
