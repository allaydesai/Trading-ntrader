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
