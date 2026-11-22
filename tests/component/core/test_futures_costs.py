"""Futures M3: per-contract commission and dollar-per-contract margin models.

Component tier: real Nautilus instruments and objects, no engine run (the engine-level
margin denial and P&L are in ``tests/integration/core/test_futures_margin_engine.py``).
"""

from decimal import Decimal

import pytest
from nautilus_trader.accounting.margin_models import LeveragedMarginModel
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from src.core.fee_models import IBKRCommissionModel
from src.core.futures_costs import (
    FuturesCommissionModel,
    FuturesMarginModel,
    futures_snapshot,
    futures_terms,
    terms_by_instrument,
    venue_cost_models,
)
from src.services.futures.contract_specs import get_contract_spec
from src.services.futures.instrument_builder import build_futures_contract

pytestmark = pytest.mark.component

ROOTS = ("ES", "NQ", "CL", "GC", "ZN")


def _contract(root: str):
    return build_futures_contract(get_contract_spec(root))


@pytest.fixture
def equity():
    return TestInstrumentProvider.equity(symbol="AAPL", venue="NASDAQ")


class TestFuturesTerms:
    @pytest.mark.parametrize("root", ROOTS)
    def test_terms_come_from_the_spec(self, root):
        spec = get_contract_spec(root)
        terms = futures_terms(_contract(root))
        assert terms is not None
        assert terms.multiplier == spec.multiplier
        assert terms.initial_margin_usd == spec.margin.initial_usd
        assert terms.maintenance_margin_usd == spec.margin.maintenance_usd
        assert terms.commission_per_contract_usd == spec.commission_per_contract_usd
        assert terms.margin_as_of == spec.margin.as_of.isoformat()

    def test_an_equity_has_no_futures_terms(self, equity):
        assert futures_terms(equity) is None

    def test_a_futures_contract_without_spec_terms_is_an_error(self):
        bare = TestInstrumentProvider.es_future(expiry_year=2025, expiry_month=3)
        with pytest.raises(ValueError, match="margin_initial_usd"):
            futures_terms(bare)


class TestFuturesCommissionModel:
    def test_charges_per_contract_per_fill(self):
        es = _contract("ES")
        model = FuturesCommissionModel({es.id: Decimal("2.25")})
        fee = model.get_commission(None, Quantity.from_int(3), Price.from_str("7000.00"), es)
        assert fee == Money(Decimal("6.75"), USD)

    def test_the_fee_does_not_scale_with_price_or_multiplier(self):
        cl = _contract("CL")
        model = FuturesCommissionModel({cl.id: Decimal("2.37")})
        low = model.get_commission(None, Quantity.from_int(1), Price.from_str("40.000"), cl)
        high = model.get_commission(None, Quantity.from_int(1), Price.from_str("140.000"), cl)
        assert low == high == Money(Decimal("2.37"), USD)

    def test_cost_stress_scales_the_rate(self):
        es = _contract("ES")
        model = FuturesCommissionModel({es.id: Decimal("2.25")}, scale=Decimal("2"))
        fee = model.get_commission(None, Quantity.from_int(1), Price.from_str("7000.00"), es)
        assert fee == Money(Decimal("4.50"), USD)

    def test_an_instrument_without_a_rate_uses_the_equity_fallback(self, equity):
        fallback = IBKRCommissionModel()
        model = FuturesCommissionModel({}, fallback=fallback)
        qty, px = Quantity.from_int(500), Price.from_str("100.00")
        assert model.get_commission(None, qty, px, equity) == fallback.get_commission(
            None, qty, px, equity
        )

    def test_no_rate_and_no_fallback_is_an_error(self, equity):
        model = FuturesCommissionModel({})
        with pytest.raises(ValueError, match="No per-contract commission"):
            model.get_commission(None, Quantity.from_int(1), Price.from_str("1.00"), equity)

    def test_negative_rates_are_rejected(self):
        with pytest.raises(ValueError):
            FuturesCommissionModel({_contract("ES").id: Decimal("-1")})


class TestSlippageDrag:
    """The per-share slippage drag (``SLIPPAGE_PER_SHARE``) is an equities-only cost.

    A futures venue charges the contract's per-contract commission and uses the equity
    model only as the fallback for non-futures instruments, so a futures fill never pays a
    per-share drag and M3/M4 futures results do not move when the setting is on.
    """

    def test_an_equity_fill_pays_commission_plus_the_drag(self, equity):
        model = IBKRCommissionModel(slippage_per_share=Decimal("0.01"))
        fee = model.get_commission(None, Quantity.from_int(100), Price.from_str("100.00"), equity)
        assert fee == Money(Decimal("2.00"), USD)  # $1 minimum + 100 x $0.01

    def test_a_futures_fill_never_pays_the_per_share_drag(self):
        es = _contract("ES")
        equity_fee = IBKRCommissionModel(slippage_per_share=Decimal("0.01"))
        fee_model, _ = venue_cost_models([es], equity_fee)
        fee = fee_model.get_commission(None, Quantity.from_int(3), Price.from_str("7000.00"), es)
        assert fee == Money(Decimal("6.75"), USD)  # 3 x 2.25, as with no drag

    def test_an_equity_on_a_futures_venue_still_pays_it(self, equity):
        equity_fee = IBKRCommissionModel(slippage_per_share=Decimal("0.01"))
        fee_model, _ = venue_cost_models([_contract("ES")], equity_fee)
        qty, px = Quantity.from_int(100), Price.from_str("100.00")
        assert fee_model.get_commission(None, qty, px, equity) == Money(Decimal("2.00"), USD)


class TestFuturesMarginModel:
    @pytest.mark.parametrize("price", ["2000.00", "7000.00", "12000.00"])
    def test_initial_margin_is_dollars_per_contract_at_any_price(self, price):
        es = _contract("ES")
        model = FuturesMarginModel(terms_by_instrument([es]))
        margin = model.calculate_margin_init(
            es, Quantity.from_int(2), Price.from_str(price), Decimal(1)
        )
        assert margin == Money(Decimal("2") * get_contract_spec("ES").margin.initial_usd, USD)

    def test_maintenance_margin_is_dollars_per_contract(self):
        gc = _contract("GC")
        model = FuturesMarginModel(terms_by_instrument([gc]))
        margin = model.calculate_margin_maint(
            gc, PositionSide.SHORT, Quantity.from_int(3), Price.from_str("2650.4"), Decimal(1)
        )
        assert margin == Money(Decimal("3") * get_contract_spec("GC").margin.maintenance_usd, USD)

    def test_other_instruments_keep_nautilus_default_margin(self, equity):
        model = FuturesMarginModel({})
        qty, px = Quantity.from_int(100), Price.from_str("150.00")
        expected = LeveragedMarginModel().calculate_margin_init(equity, qty, px, Decimal(1))
        assert model.calculate_margin_init(equity, qty, px, Decimal(1)) == expected


class TestVenueCostModels:
    def test_an_equity_only_venue_is_set_up_exactly_as_before(self, equity):
        equity_fee = IBKRCommissionModel()
        fee_model, margin_model = venue_cost_models([equity], equity_fee)
        assert fee_model is equity_fee
        assert margin_model is None

    def test_a_futures_venue_gets_per_contract_fees_and_dollar_margin(self):
        es, nq = _contract("ES"), _contract("NQ")
        equity_fee = IBKRCommissionModel()
        fee_model, margin_model = venue_cost_models([es, nq], equity_fee, Decimal("1.5"))
        assert isinstance(fee_model, FuturesCommissionModel)
        assert fee_model.rates == {es.id: Decimal("2.25"), nq.id: Decimal("2.25")}
        assert fee_model.fallback is equity_fee
        assert fee_model.scale == Decimal("1.5")
        assert isinstance(margin_model, FuturesMarginModel)
        assert set(margin_model.terms) == {es.id, nq.id}


class TestFuturesSnapshot:
    def test_equity_runs_record_nothing(self, equity):
        assert futures_snapshot([equity]) == {}

    def test_futures_runs_record_the_terms_used(self):
        snapshot = futures_snapshot([_contract("ES")])
        assert snapshot["profit_pct_basis"] == "underlying"
        es = snapshot["futures_contracts"]["ES.CME"]
        assert es["multiplier"] == "50"
        assert es["margin_initial_usd"] == str(get_contract_spec("ES").margin.initial_usd)
        assert es["commission_per_contract_usd"] == "2.25"

    def test_the_snapshot_survives_the_persisted_snapshot_model(self):
        """Persistence keeps only declared keys: the futures ones must be declared."""
        from src.models.config_snapshot import StrategyConfigSnapshot

        base = {"strategy_path": "src.x:Y", "config_path": "x.yaml"}
        dumped = StrategyConfigSnapshot(**base, **futures_snapshot([_contract("ES")])).model_dump()
        assert dumped["futures_contracts"]["ES.CME"]["multiplier"] == "50"
        assert dumped["profit_pct_basis"] == "underlying"


class TestRunnerSnapshot:
    """The legacy runner's catalog path persists the same futures keys as the orchestrator."""

    def test_futures_runs_carry_the_contract_terms(self):
        from types import SimpleNamespace

        from src.core.backtest_runner import _runtime_config_snapshot

        engine = SimpleNamespace(cache=SimpleNamespace(instruments=lambda: [_contract("ES")]))
        snapshot = _runtime_config_snapshot("sma_crossover", {"a": 1}, engine)
        assert snapshot["futures_contracts"]["ES.CME"]["multiplier"] == "50"
        assert snapshot["profit_pct_basis"] == "underlying"
        assert snapshot["config"] == {"a": 1}

    def test_equity_and_engineless_runs_are_unchanged(self, equity):
        from types import SimpleNamespace

        from src.core.backtest_runner import _runtime_config_snapshot

        expected = {
            "strategy_path": "src.core.strategies.sma_crossover",
            "config_path": "runtime_config",
            "version": "1.0",
            "config": {"a": 1},
        }
        engine = SimpleNamespace(cache=SimpleNamespace(instruments=lambda: [equity]))
        assert _runtime_config_snapshot("sma_crossover", {"a": 1}, engine) == expected
        assert _runtime_config_snapshot("sma_crossover", {"a": 1}, None) == expected
