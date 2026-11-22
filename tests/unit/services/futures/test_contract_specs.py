"""Futures contract-spec registry: the shipped file and its validation (futures M1)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from src.services.futures.contract_specs import (
    DEFAULT_SPEC_PATH,
    FuturesSpecError,
    get_contract_spec,
    load_contract_specs,
)

pytestmark = pytest.mark.unit

EXPECTED = {
    # root: (venue, multiplier, tick_size, price_precision, continuous_price_precision)
    "ES": ("CME", Decimal("50"), Decimal("0.25"), 2, 2),
    "NQ": ("CME", Decimal("20"), Decimal("0.25"), 2, 2),
    "CL": ("NYMEX", Decimal("1000"), Decimal("0.01"), 2, 3),
    "GC": ("COMEX", Decimal("100"), Decimal("0.1"), 1, 1),
    "ZN": ("CBOT", Decimal("1000"), Decimal("0.015625"), 6, 6),
}


def _raw() -> dict:
    return yaml.safe_load(DEFAULT_SPEC_PATH.read_text())


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "specs.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


class TestShippedSpecFile:
    def test_all_five_roots_load(self):
        registry = load_contract_specs()
        assert set(registry.roots) == set(EXPECTED)
        assert registry.schema_version == 1
        assert isinstance(registry.specs_as_of, date)

    @pytest.mark.parametrize("root", sorted(EXPECTED))
    def test_root_economics(self, root):
        venue, mult, tick, precision, cont_precision = EXPECTED[root]
        spec = get_contract_spec(root)
        assert spec.root == root
        assert spec.venue == venue
        assert spec.multiplier == mult
        assert spec.tick_size == tick
        assert spec.price_precision == precision
        assert spec.continuous_price_precision == cont_precision
        assert spec.currency == "USD"

    @pytest.mark.parametrize("root", sorted(EXPECTED))
    def test_root_live_and_cost_fields(self, root):
        spec = get_contract_spec(root)
        assert spec.ib.sec_type == "FUT"
        assert spec.ib.exchange == spec.venue
        assert spec.ib.trading_class == root
        assert Decimal(spec.ib.multiplier) == spec.multiplier
        assert spec.margin.initial_usd >= spec.margin.maintenance_usd > 0
        assert spec.margin.as_of <= date.today()
        assert "approx" in spec.margin.note.lower()
        assert spec.commission_per_contract_usd > 0
        assert spec.session.timezone == "America/New_York"
        assert spec.underlying

    def test_lookup_is_case_insensitive(self):
        assert get_contract_spec("es").root == "ES"


class TestValidation:
    def test_unknown_root_lists_known(self):
        with pytest.raises(FuturesSpecError, match="Unknown futures root 'XX'.*ES"):
            get_contract_spec("XX")

    def test_unknown_key_is_rejected(self, tmp_path):
        data = _raw()
        data["roots"]["ES"]["multipler"] = 50
        with pytest.raises(FuturesSpecError, match=r"roots\.ES\.multipler"):
            load_contract_specs(_write(tmp_path, data))

    def test_missing_field_names_path(self, tmp_path):
        data = _raw()
        del data["roots"]["NQ"]["multiplier"]
        with pytest.raises(FuturesSpecError, match=r"roots\.NQ\.multiplier"):
            load_contract_specs(_write(tmp_path, data))

    def test_error_names_the_file(self, tmp_path):
        data = _raw()
        data["roots"]["ES"]["multiplier"] = -1
        path = _write(tmp_path, data)
        with pytest.raises(FuturesSpecError, match=str(path)):
            load_contract_specs(path)

    def test_tick_finer_than_precision_is_rejected(self, tmp_path):
        data = _raw()
        data["roots"]["ES"]["tick_size"] = "0.125"
        with pytest.raises(FuturesSpecError, match="tick_size"):
            load_contract_specs(_write(tmp_path, data))

    def test_continuous_precision_coarser_than_tick_is_rejected(self, tmp_path):
        data = _raw()
        data["roots"]["ZN"]["continuous_price_precision"] = 2
        with pytest.raises(FuturesSpecError, match="continuous_price_precision"):
            load_contract_specs(_write(tmp_path, data))

    def test_initial_below_maintenance_is_rejected(self, tmp_path):
        data = _raw()
        data["roots"]["CL"]["margin"]["initial_usd"] = "1"
        with pytest.raises(FuturesSpecError, match="initial_usd"):
            load_contract_specs(_write(tmp_path, data))

    @pytest.mark.parametrize(
        ("field", "value"),
        [("exchange", "NYMEX"), ("currency", "EUR"), ("multiplier", "5")],
    )
    def test_ib_block_must_agree(self, tmp_path, field, value):
        data = _raw()
        data["roots"]["ES"]["ib"][field] = value
        with pytest.raises(FuturesSpecError, match=f"ib.{field}"):
            load_contract_specs(_write(tmp_path, data))

    def test_unknown_venue_is_rejected(self, tmp_path):
        data = _raw()
        data["roots"]["ES"]["venue"] = "XCME"
        with pytest.raises(FuturesSpecError, match=r"roots\.ES\.venue"):
            load_contract_specs(_write(tmp_path, data))

    def test_unknown_schema_version_is_rejected(self, tmp_path):
        data = _raw()
        data["schema_version"] = 2
        with pytest.raises(FuturesSpecError, match="schema_version"):
            load_contract_specs(_write(tmp_path, data))

    def test_missing_file(self, tmp_path):
        with pytest.raises(FuturesSpecError, match="not found"):
            load_contract_specs(tmp_path / "nope.yaml")

    def test_not_a_mapping(self, tmp_path):
        path = tmp_path / "specs.yaml"
        path.write_text("- just\n- a list\n")
        with pytest.raises(FuturesSpecError, match="mapping"):
            load_contract_specs(path)

    def test_malformed_yaml(self, tmp_path):
        path = tmp_path / "specs.yaml"
        path.write_text("roots: {ES: [unclosed\n")
        with pytest.raises(FuturesSpecError, match="YAML"):
            load_contract_specs(path)


class TestRootKey:
    def test_explicit_matching_root_is_accepted(self, tmp_path):
        data = _raw()
        data["roots"]["ES"]["root"] = "ES"
        assert load_contract_specs(_write(tmp_path, data)).roots["ES"].root == "ES"

    def test_explicit_root_disagreeing_with_key_is_rejected(self, tmp_path):
        data = _raw()
        data["roots"]["ES"]["root"] = "NQ"
        with pytest.raises(FuturesSpecError, match=r"roots\.ES\.root"):
            load_contract_specs(_write(tmp_path, data))
