"""Spot FX pair-spec registry: the shipped file and its validation (FX M1, task 1.1)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from src.services.fx.pair_specs import (
    DEFAULT_SPEC_PATH,
    SOURCED_FIELDS,
    FxSpecError,
    get_commission_spec,
    get_pair_spec,
    load_pair_specs,
)

pytestmark = pytest.mark.unit

EXPECTED = {
    # pair: (base, quote, pip_size, price_precision, min_tick, data)
    "EURUSD": ("EUR", "USD", Decimal("0.0001"), 5, Decimal("0.00005"), "firstrate"),
    "GBPUSD": ("GBP", "USD", Decimal("0.0001"), 5, Decimal("0.00005"), "firstrate"),
    "USDJPY": ("USD", "JPY", Decimal("0.01"), 3, Decimal("0.005"), "firstrate"),
    "EURGBP": ("EUR", "GBP", Decimal("0.0001"), 5, Decimal("0.00005"), "none"),
}


def _raw() -> dict:
    return yaml.safe_load(DEFAULT_SPEC_PATH.read_text())


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "pairs.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


class TestShippedSpecFile:
    def test_all_four_pairs_load(self):
        registry = load_pair_specs()
        assert set(registry.pairs) == set(EXPECTED)
        assert registry.schema_version == 1
        assert isinstance(registry.specs_as_of, date)

    @pytest.mark.parametrize("pair", sorted(EXPECTED))
    def test_pair_terms(self, pair):
        base, quote, pip, precision, tick, data = EXPECTED[pair]
        spec = get_pair_spec(pair)
        assert spec.pair == pair
        assert (spec.base, spec.quote) == (base, quote)
        assert spec.venue == "IDEALPRO"
        assert spec.pip_size == pip
        assert spec.price_precision == precision
        assert spec.min_tick == tick
        assert spec.size_increment == Decimal("1")
        assert spec.data == data
        assert spec.maint_margin_pct <= spec.margin_pct
        assert spec.margin_pct == Decimal("3.0")
        assert spec.typical_spread_pips > 0
        assert spec.min_order_usd == Decimal("25000")
        assert spec.session.tz == "America/New_York"

    @pytest.mark.parametrize("pair", sorted(EXPECTED))
    def test_ib_block_is_idealpro_cash(self, pair):
        spec = get_pair_spec(pair)
        assert spec.ib.sec_type == "CASH"
        assert spec.ib.exchange == "IDEALPRO"
        assert spec.ib.symbol == spec.base
        assert spec.ib.currency == spec.quote
        assert spec.ib.local_symbol == f"{spec.base}.{spec.quote}"

    @pytest.mark.parametrize("pair", sorted(EXPECTED))
    def test_every_value_has_a_dated_source(self, pair):
        spec = get_pair_spec(pair)
        assert set(spec.sources) == set(SOURCED_FIELDS)
        for source in spec.sources.values():
            assert source.status in ("VERIFIED", "UNVERIFIED")
            assert source.source
            assert source.as_of <= date.today()

    @pytest.mark.parametrize("pair", sorted(EXPECTED))
    def test_ib_economics_are_flagged_unverified(self, pair):
        unverified = set(get_pair_spec(pair).unverified_fields())
        assert {
            "min_tick",
            "size_increment",
            "min_order_usd",
            "typical_spread_pips",
            "margin_pct",
            "maint_margin_pct",
        } <= unverified

    def test_data_backed_precision_is_verified(self):
        for pair in ("EURUSD", "GBPUSD", "USDJPY"):
            assert get_pair_spec(pair).sources["price_precision"].status == "VERIFIED"
        assert get_pair_spec("EURGBP").sources["price_precision"].status == "UNVERIFIED"

    def test_commission_schedule(self):
        commission = get_commission_spec()
        assert commission.rate_bps == Decimal("0.20")
        assert commission.min_per_order_usd == Decimal("2.00")
        assert commission.status == "UNVERIFIED"
        assert commission.source
        assert commission.as_of <= date.today()

    def test_lookup_is_case_insensitive(self):
        assert get_pair_spec("eurusd").pair == "EURUSD"


class TestValidation:
    def test_unknown_pair_lists_known(self):
        with pytest.raises(FxSpecError, match="Unknown FX pair 'AUDUSD'.*EURUSD"):
            get_pair_spec("AUDUSD")

    def test_unknown_key_is_rejected(self, tmp_path):
        data = _raw()
        data["pairs"]["EURUSD"]["pip_sise"] = "0.0001"
        with pytest.raises(FxSpecError, match=r"pairs\.EURUSD\.pip_sise"):
            load_pair_specs(_write(tmp_path, data))

    def test_missing_field_names_path(self, tmp_path):
        data = _raw()
        del data["pairs"]["GBPUSD"]["pip_size"]
        with pytest.raises(FxSpecError, match=r"pairs\.GBPUSD\.pip_size"):
            load_pair_specs(_write(tmp_path, data))

    def test_error_names_the_file(self, tmp_path):
        data = _raw()
        data["pairs"]["EURUSD"]["margin_pct"] = "-1"
        path = _write(tmp_path, data)
        with pytest.raises(FxSpecError, match=str(path)):
            load_pair_specs(path)

    @pytest.mark.parametrize("field", ["pip_size", "min_tick"])
    def test_decimals_over_precision_are_rejected(self, tmp_path, field):
        data = _raw()
        data["pairs"]["USDJPY"][field] = "0.0001"
        with pytest.raises(FxSpecError, match=rf"pairs\.USDJPY.*{field}"):
            load_pair_specs(_write(tmp_path, data))

    def test_key_must_be_base_plus_quote(self, tmp_path):
        data = _raw()
        data["pairs"]["EURUSD"]["base"] = "GBP"
        data["pairs"]["EURUSD"]["ib"]["symbol"] = "GBP"
        data["pairs"]["EURUSD"]["ib"]["local_symbol"] = "GBP.USD"
        with pytest.raises(FxSpecError, match=r"pairs\.EURUSD.*base"):
            load_pair_specs(_write(tmp_path, data))

    def test_base_equal_to_quote_is_rejected(self, tmp_path):
        data = _raw()
        data["pairs"]["USDUSD"] = {
            **data["pairs"]["EURUSD"],
            "base": "USD",
            "ib": {**data["pairs"]["EURUSD"]["ib"], "symbol": "USD", "local_symbol": "USD.USD"},
        }
        with pytest.raises(FxSpecError, match=r"pairs\.USDUSD.*base"):
            load_pair_specs(_write(tmp_path, data))

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("symbol", "GBP"),
            ("currency", "JPY"),
            ("exchange", "SMART"),
            ("local_symbol", "EUR/USD"),
        ],
    )
    def test_ib_block_must_agree(self, tmp_path, field, value):
        data = _raw()
        data["pairs"]["EURUSD"]["ib"][field] = value
        with pytest.raises(FxSpecError, match=rf"pairs\.EURUSD.*ib\.{field}"):
            load_pair_specs(_write(tmp_path, data))

    def test_ib_sec_type_must_be_cash(self, tmp_path):
        data = _raw()
        data["pairs"]["EURUSD"]["ib"]["sec_type"] = "CFD"
        with pytest.raises(FxSpecError, match=r"pairs\.EURUSD\.ib\.sec_type"):
            load_pair_specs(_write(tmp_path, data))

    def test_maintenance_above_initial_is_rejected(self, tmp_path):
        data = _raw()
        data["pairs"]["GBPUSD"]["maint_margin_pct"] = "5.0"
        with pytest.raises(FxSpecError, match=r"pairs\.GBPUSD.*maint_margin_pct"):
            load_pair_specs(_write(tmp_path, data))

    def test_unknown_venue_is_rejected(self, tmp_path):
        data = _raw()
        data["pairs"]["EURUSD"]["venue"] = "SMART"
        with pytest.raises(FxSpecError, match=r"pairs\.EURUSD\.venue"):
            load_pair_specs(_write(tmp_path, data))

    def test_missing_source_entry_is_rejected(self, tmp_path):
        data = _raw()
        sources = dict(data["pairs"]["EURUSD"]["sources"])
        del sources["margin_pct"]
        data["pairs"]["EURUSD"]["sources"] = sources
        with pytest.raises(FxSpecError, match=r"pairs\.EURUSD.*sources.*margin_pct"):
            load_pair_specs(_write(tmp_path, data))

    def test_unknown_source_entry_is_rejected(self, tmp_path):
        data = _raw()
        sources = dict(data["pairs"]["EURUSD"]["sources"])
        sources["leverage"] = sources["margin_pct"]
        data["pairs"]["EURUSD"]["sources"] = sources
        with pytest.raises(FxSpecError, match=r"pairs\.EURUSD.*sources.*leverage"):
            load_pair_specs(_write(tmp_path, data))

    def test_bad_source_status_is_rejected(self, tmp_path):
        data = _raw()
        sources = {k: dict(v) for k, v in data["pairs"]["EURUSD"]["sources"].items()}
        sources["min_tick"]["status"] = "MAYBE"
        data["pairs"]["EURUSD"]["sources"] = sources
        with pytest.raises(FxSpecError, match=r"pairs\.EURUSD\.sources\.min_tick\.status"):
            load_pair_specs(_write(tmp_path, data))

    def test_unknown_schema_version_is_rejected(self, tmp_path):
        data = _raw()
        data["schema_version"] = 2
        with pytest.raises(FxSpecError, match="schema_version"):
            load_pair_specs(_write(tmp_path, data))

    def test_bad_commission_is_rejected(self, tmp_path):
        data = _raw()
        data["commission"]["min_per_order_usd"] = "-2"
        with pytest.raises(FxSpecError, match=r"commission\.min_per_order_usd"):
            load_pair_specs(_write(tmp_path, data))

    def test_missing_file(self, tmp_path):
        with pytest.raises(FxSpecError, match="not found"):
            load_pair_specs(tmp_path / "nope.yaml")

    def test_not_a_mapping(self, tmp_path):
        path = tmp_path / "pairs.yaml"
        path.write_text("- just\n- a list\n")
        with pytest.raises(FxSpecError, match="mapping"):
            load_pair_specs(path)

    def test_malformed_yaml(self, tmp_path):
        path = tmp_path / "pairs.yaml"
        path.write_text("pairs: {EURUSD: [unclosed\n")
        with pytest.raises(FxSpecError, match="YAML"):
            load_pair_specs(path)
