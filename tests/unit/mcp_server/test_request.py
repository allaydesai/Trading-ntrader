"""Resolving a chat-level backtest spec into an NTrader BacktestRequest."""

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.mcp_server.errors import ToolFailure
from src.mcp_server.request import BacktestSpec, resolve
from src.models.backtest_request import DEFAULT_FILL_SEED
from src.services.provenance import compute_config_hash

pytestmark = pytest.mark.unit


def _spec(**overrides) -> BacktestSpec:
    fields = dict(
        strategy="sma",
        symbol="qqq",
        start=date(2000, 1, 1),
        end=date(2015, 12, 31),
        catalog="firstrate-etf",
        params={"fast_period": 5, "slow_period": 30},
    )
    fields.update(overrides)
    return BacktestSpec(**fields)


def test_resolves_a_strategy_run():
    resolved = resolve(_spec(), default_catalog="")
    request = resolved.request
    assert request.strategy_type == "sma_crossover"
    assert request.symbol == "QQQ"
    assert request.instrument_id == "QQQ.NAMED_CATALOG"
    assert request.bar_type == "1-DAY-LAST"
    assert request.catalog_name == "firstrate-etf"
    assert request.data_source == "catalog"
    assert request.persist is True
    assert request.start_date == datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert request.end_date.date() == date(2015, 12, 31)
    assert request.end_date.hour == 23
    assert request.strategy_config["fast_period"] == 5
    assert "position_size_pct" in request.strategy_config  # defaults filled in
    assert resolved.config_hash == compute_config_hash(request)
    assert request.fill_seed == DEFAULT_FILL_SEED


def test_resolves_a_benchmark():
    resolved = resolve(_spec(strategy="buy_and_hold", params={}), default_catalog="")
    assert resolved.request.strategy_path == "src.core.benchmarks.buy_and_hold:BuyAndHold"
    assert resolved.request.strategy_config == {"allocation_pct": Decimal("100")}
    assert resolved.kind == "benchmark"


def test_default_catalog_is_used_when_none_given():
    resolved = resolve(_spec(catalog=None), default_catalog="e2e-test")
    assert resolved.request.catalog_name == "e2e-test"


def test_no_catalog_at_all_is_refused():
    with pytest.raises(ToolFailure) as exc:
        resolve(_spec(catalog=None), default_catalog="")
    assert exc.value.code == "no_catalog"


def test_end_before_start_is_refused():
    with pytest.raises(ToolFailure) as exc:
        resolve(_spec(start=date(2015, 1, 1), end=date(2014, 1, 1)), default_catalog="")
    assert exc.value.code == "invalid_window"


def test_same_spec_same_hash_and_param_change_changes_it():
    a = resolve(_spec(), default_catalog="").config_hash
    b = resolve(_spec(), default_catalog="").config_hash
    c = resolve(_spec(params={"fast_period": 6, "slow_period": 30}), default_catalog="")
    assert a == b != c.config_hash


def test_starting_balance_must_be_positive():
    with pytest.raises(ValueError):
        _spec(starting_balance=Decimal("0"))


def test_summary_is_json_ready():
    summary = resolve(_spec(), default_catalog="").summary()
    assert summary["strategy"] == "sma_crossover"
    assert summary["params"]["fast_period"] == 5
    assert summary["start"] == "2000-01-01"
    assert summary["end"] == "2015-12-31"
    assert isinstance(summary["starting_balance"], float)


def test_a_stored_fill_seed_is_used_and_changes_the_hash():
    """reproduce_run re-runs a stored config with its own seed (S2.3)."""
    seeded = resolve(_spec(fill_seed=7), default_catalog="")
    assert seeded.request.fill_seed == 7
    assert seeded.config_hash != resolve(_spec(), default_catalog="").config_hash


def test_no_fill_seed_keeps_phase_1_hashes():
    unseeded = resolve(_spec(fill_seed=None), default_catalog="")
    explicit = resolve(_spec(fill_seed=DEFAULT_FILL_SEED), default_catalog="")
    assert unseeded.config_hash == explicit.config_hash


class TestFxSymbols:
    """FX (M5): ``EUR/USD`` names the ``EURUSD`` row, whose bars are ``MID``."""

    @pytest.fixture(autouse=True)
    def fx_row(self, monkeypatch):
        from types import SimpleNamespace

        from src.services.fx import symbols

        rows = {("firstrate-fx", "EURUSD"): SimpleNamespace(asset_class="FX")}
        monkeypatch.setattr(symbols, "_catalog_row", lambda c, t: rows.get((c, t)))

    @pytest.mark.parametrize("symbol", ["EURUSD", "eur/usd"])
    def test_fx_row_resolves_to_its_ticker_and_mid_bars(self, symbol):
        spec = _spec(symbol=symbol, catalog="firstrate-fx", timeframe="1-HOUR")
        request = resolve(spec, default_catalog="").request
        assert (request.symbol, request.instrument_id) == ("EURUSD", "EURUSD.NAMED_CATALOG")
        assert request.bar_type == "1-HOUR-MID"

    def test_a_non_fx_catalog_keeps_last(self):
        request = resolve(_spec(symbol="EURUSD"), default_catalog="").request
        assert request.bar_type == "1-DAY-LAST"


class TestSizingOverrides:
    """QA fix (c): ``units`` (FX) and ``contracts`` (futures), as ``--units``/``--contracts``."""

    @pytest.fixture(autouse=True)
    def fx_row(self, monkeypatch):
        from types import SimpleNamespace

        from src.services.fx import symbols

        rows = {("firstrate-fx", "EURUSD"): SimpleNamespace(asset_class="FX")}
        monkeypatch.setattr(symbols, "_catalog_row", lambda c, t: rows.get((c, t)))

    def _fx(self, **overrides):
        return _spec(symbol="EUR/USD", catalog="firstrate-fx", params={}, **overrides)

    def _es(self, **overrides):
        return _spec(symbol="ES", catalog="firstrate-futures", params={}, **overrides)

    def test_units_on_an_fx_pair_set_the_strategy_s_units(self):
        sized = resolve(self._fx(units=100_000), default_catalog="")
        assert sized.request.strategy_config["units"] == 100_000
        assert sized.config_hash != resolve(self._fx(), default_catalog="").config_hash
        assert sized.spec.units == 100_000

    def test_contracts_on_a_futures_root_set_the_strategy_s_contracts(self):
        sized = resolve(self._es(contracts=2), default_catalog="")
        assert sized.request.strategy_config["contracts"] == 2

    def test_unset_overrides_leave_the_config_and_hash_alone(self):
        plain = resolve(self._fx(), default_catalog="")
        assert "units" not in plain.request.strategy_config
        assert "contracts" not in plain.request.strategy_config

    def test_units_are_for_fx_only(self):
        with pytest.raises(ToolFailure) as exc:
            resolve(_spec(units=1000), default_catalog="")
        assert exc.value.code == "invalid_params"
        assert "units is for FX only" in exc.value.message
        assert "'QQQ'" in exc.value.message

    def test_contracts_are_for_futures_only(self):
        with pytest.raises(ToolFailure) as exc:
            resolve(self._fx(contracts=1), default_catalog="")
        assert exc.value.code == "invalid_params"
        assert "contracts applies to futures only" in exc.value.message

    def test_a_strategy_without_the_setting_is_refused_naming_those_with_it(self):
        with pytest.raises(ToolFailure) as exc:
            resolve(self._fx(strategy="buy_and_hold", units=1000), default_catalog="")
        assert exc.value.code == "invalid_params"
        assert "no 'units' setting" in exc.value.message
        assert "sma_crossover" in exc.value.fix

    @pytest.mark.parametrize("field", ["units", "contracts"])
    @pytest.mark.parametrize("bad", [0, -1])
    def test_sizes_must_be_positive(self, field, bad):
        with pytest.raises(ValueError):
            _spec(**{field: bad})
