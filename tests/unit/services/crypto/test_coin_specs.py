"""Spot crypto coin-spec registry: the shipped file and its validation (crypto M1, task 1.1)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from src.services.crypto.coin_specs import (
    DEFAULT_SPEC_PATH,
    FEE_SOURCED_FIELDS,
    SOURCED_FIELDS,
    CryptoSpecError,
    coin_names,
    get_coin_spec,
    get_fee_schedule,
    load_coin_specs,
)

pytestmark = pytest.mark.unit

FEE_SOURCE_URL = "https://www.kraken.com/ca/pro/fees"

EXPECTED = {
    # coin: (kraken pair, rest, altname, spread bps)
    "BTC": ("BTC/USD", "XXBTZUSD", "XBTUSD", Decimal("2")),
    "ETH": ("ETH/USD", "XETHZUSD", "ETHUSD", Decimal("3")),
    "SOL": ("SOL/USD", "SOLZUSD", "SOLUSD", Decimal("5")),
}

# (tier, maker %, taker %) as published on the Kraken Pro CA fee page, 2026-11-24.
PUBLISHED_TIERS = [
    (1, "0.40", "0.80"),
    (2, "0.30", "0.60"),
    (3, "0.22", "0.38"),
    (4, "0.20", "0.35"),
    (5, "0.15", "0.30"),
    (6, "0.12", "0.25"),
    (7, "0.10", "0.22"),
    (8, "0.08", "0.20"),
    (9, "0.06", "0.18"),
    (10, "0.04", "0.15"),
    (11, "0.02", "0.12"),
    (12, "0.00", "0.10"),
    (13, "0.00", "0.09"),
    (14, "0.00", "0.08"),
    (15, "0.00", "0.07"),
    (16, "0.00", "0.06"),
    (17, "0.00", "0.05"),
]


def _raw() -> dict:
    return yaml.safe_load(DEFAULT_SPEC_PATH.read_text())


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "coins.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


class TestShippedSpecFile:
    def test_btc_eth_sol_load(self):
        registry = load_coin_specs()
        assert set(registry.coins) == set(EXPECTED)
        assert coin_names() == frozenset(EXPECTED)
        assert registry.schema_version == 1
        assert isinstance(registry.specs_as_of, date)

    @pytest.mark.parametrize("coin", sorted(EXPECTED))
    def test_coin_terms(self, coin):
        pair, rest, altname, spread = EXPECTED[coin]
        spec = get_coin_spec(coin)
        assert spec.coin == coin
        assert (spec.base, spec.quote) == (coin, "USD")
        assert spec.data == "firstrate"
        assert spec.price_precision == 4
        assert spec.size_precision == 8
        assert spec.size_increment == Decimal("0.00000001")
        assert spec.typical_spread_bps == spread

    @pytest.mark.parametrize("coin", sorted(EXPECTED))
    def test_kraken_block(self, coin):
        pair, rest, altname, _ = EXPECTED[coin]
        kraken = get_coin_spec(coin).kraken
        assert kraken.pair == pair
        assert kraken.rest == rest
        assert kraken.altname == altname
        assert kraken.min_order_base > 0
        assert kraken.min_order_usd > 0
        assert kraken.tick_size > 0
        assert kraken.discounted_pair is False  # decision C23: assume not discounted

    @pytest.mark.parametrize("coin", sorted(EXPECTED))
    def test_every_value_has_a_dated_source(self, coin):
        spec = get_coin_spec(coin)
        assert set(spec.sources) == set(SOURCED_FIELDS)
        for source in spec.sources.values():
            assert source.status in ("VERIFIED", "UNVERIFIED")
            assert source.source
            assert source.as_of <= date.today()

    @pytest.mark.parametrize("coin", sorted(EXPECTED))
    def test_kraken_venue_terms_are_flagged_unverified(self, coin):
        unverified = set(get_coin_spec(coin).unverified_fields())
        assert {
            "typical_spread_bps",
            "kraken_rest",
            "kraken_altname",
            "kraken_min_order_base",
            "kraken_min_order_usd",
            "kraken_tick_size",
            "kraken_discounted_pair",
        } <= unverified

    def test_data_backed_precision_is_verified(self):
        for coin in EXPECTED:
            assert get_coin_spec(coin).sources["price_precision"].status == "VERIFIED"

    def test_fee_schedule_has_the_17_published_tiers(self):
        schedule = get_fee_schedule()
        assert sorted(schedule.tiers) == list(range(1, 18))
        got = [(n, schedule.tiers[n].maker_pct, schedule.tiers[n].taker_pct) for n in range(1, 18)]
        assert got == [(n, Decimal(m), Decimal(t)) for n, m, t in PUBLISHED_TIERS]
        assert schedule.source == FEE_SOURCE_URL
        assert schedule.as_of == date(2026, 11, 24)
        assert schedule.status == "VERIFIED"

    def test_fee_schedule_defaults(self):
        schedule = get_fee_schedule()
        assert schedule.default_tier == 2  # decision C21: Allay's Kraken Pro tier
        assert (schedule.tiers[2].maker_pct, schedule.tiers[2].taker_pct) == (
            Decimal("0.30"),
            Decimal("0.60"),
        )
        assert schedule.discounted_pair_bps_off == Decimal("2")
        assert schedule.min_fee_usd == Decimal("0")
        assert schedule.fee_currency == "USD"
        assert set(schedule.sources) == set(FEE_SOURCED_FIELDS)
        assert "fee_currency" in schedule.unverified_fields()
        assert "default_tier" not in schedule.unverified_fields()  # confirmed 2026-11-24

    def test_tier_lookup(self):
        schedule = get_fee_schedule()
        assert schedule.tier(1).taker_pct == Decimal("0.80")
        assert schedule.tier(17).maker_pct == Decimal("0.00")
        with pytest.raises(CryptoSpecError, match="tier 18"):
            schedule.tier(18)

    @pytest.mark.parametrize("name", ["btc", "BTC", "Btc"])
    def test_lookup_is_case_insensitive(self, name):
        assert get_coin_spec(name).coin == "BTC"


class TestValidation:
    def test_unknown_coin_lists_known(self):
        with pytest.raises(CryptoSpecError, match="Unknown crypto coin 'DOGE'.*BTC"):
            get_coin_spec("DOGE")

    def test_unknown_key_is_rejected(self, tmp_path):
        data = _raw()
        data["coins"]["BTC"]["size_incremnt"] = "0.00000001"
        with pytest.raises(CryptoSpecError, match=r"coins\.BTC\.size_incremnt"):
            load_coin_specs(_write(tmp_path, data))

    def test_unknown_kraken_key_is_rejected(self, tmp_path):
        data = _raw()
        data["coins"]["ETH"]["kraken"]["leverage"] = "5"
        with pytest.raises(CryptoSpecError, match=r"coins\.ETH\.kraken\.leverage"):
            load_coin_specs(_write(tmp_path, data))

    def test_missing_field_names_path(self, tmp_path):
        data = _raw()
        del data["coins"]["SOL"]["size_precision"]
        with pytest.raises(CryptoSpecError, match=r"coins\.SOL\.size_precision"):
            load_coin_specs(_write(tmp_path, data))

    def test_error_names_the_file(self, tmp_path):
        data = _raw()
        data["coins"]["BTC"]["typical_spread_bps"] = "-1"
        path = _write(tmp_path, data)
        with pytest.raises(CryptoSpecError, match=str(path)):
            load_coin_specs(path)

    @pytest.mark.parametrize("quote", ["USDT", "CAD", "EUR"])
    def test_non_usd_quote_is_rejected(self, tmp_path, quote):
        data = _raw()
        data["coins"]["BTC"]["quote"] = quote
        data["coins"]["BTC"]["kraken"]["pair"] = f"BTC/{quote}"
        with pytest.raises(CryptoSpecError, match=r"coins\.BTC\.quote"):
            load_coin_specs(_write(tmp_path, data))

    def test_key_must_equal_base(self, tmp_path):
        data = _raw()
        data["coins"]["BTC"]["base"] = "ETH"
        data["coins"]["BTC"]["kraken"]["pair"] = "ETH/USD"
        with pytest.raises(CryptoSpecError, match=r"coins\.BTC.*base"):
            load_coin_specs(_write(tmp_path, data))

    def test_explicit_coin_must_equal_key(self, tmp_path):
        data = _raw()
        data["coins"]["BTC"]["coin"] = "XBT"
        with pytest.raises(CryptoSpecError, match="does not match its key"):
            load_coin_specs(_write(tmp_path, data))

    def test_base_equal_to_quote_is_rejected(self, tmp_path):
        data = _raw()
        data["coins"]["USD"] = {
            **data["coins"]["BTC"],
            "base": "USD",
            "kraken": {**data["coins"]["BTC"]["kraken"], "pair": "USD/USD"},
        }
        with pytest.raises(CryptoSpecError, match=r"coins\.USD.*base"):
            load_coin_specs(_write(tmp_path, data))

    def test_kraken_pair_must_be_base_slash_quote(self, tmp_path):
        data = _raw()
        data["coins"]["ETH"]["kraken"]["pair"] = "ETHUSD"
        with pytest.raises(CryptoSpecError, match=r"coins\.ETH.*kraken\.pair"):
            load_coin_specs(_write(tmp_path, data))

    @pytest.mark.parametrize(("increment", "precision"), [("0.000000001", 8), ("0.001", 2)])
    def test_increment_decimals_over_precision_are_rejected(self, tmp_path, increment, precision):
        data = _raw()
        data["coins"]["BTC"]["size_increment"] = increment
        data["coins"]["BTC"]["size_precision"] = precision
        with pytest.raises(CryptoSpecError, match=r"coins\.BTC.*size_increment"):
            load_coin_specs(_write(tmp_path, data))

    def test_missing_source_entry_is_rejected(self, tmp_path):
        data = _raw()
        sources = dict(data["coins"]["BTC"]["sources"])
        del sources["kraken_tick_size"]
        data["coins"]["BTC"]["sources"] = sources
        with pytest.raises(CryptoSpecError, match=r"coins\.BTC.*sources.*kraken_tick_size"):
            load_coin_specs(_write(tmp_path, data))

    def test_bad_source_status_is_rejected(self, tmp_path):
        data = _raw()
        sources = {k: dict(v) for k, v in data["coins"]["BTC"]["sources"].items()}
        sources["kraken_altname"]["status"] = "MAYBE"
        data["coins"]["BTC"]["sources"] = sources
        with pytest.raises(CryptoSpecError, match=r"coins\.BTC\.sources\.kraken_altname\.status"):
            load_coin_specs(_write(tmp_path, data))

    def test_tiers_must_be_numbered_1_to_n(self, tmp_path):
        data = _raw()
        del data["fee_schedule"]["tiers"][5]
        with pytest.raises(CryptoSpecError, match=r"fee_schedule.*tiers must be numbered 1\.\.16"):
            load_coin_specs(_write(tmp_path, data))

    def test_maker_above_taker_is_rejected(self, tmp_path):
        data = _raw()
        data["fee_schedule"]["tiers"][3]["maker"] = "0.50"
        with pytest.raises(CryptoSpecError, match=r"fee_schedule.*tier 3.*maker"):
            load_coin_specs(_write(tmp_path, data))

    def test_rates_rising_with_tier_are_rejected(self, tmp_path):
        data = _raw()
        data["fee_schedule"]["tiers"][10]["taker"] = "0.19"
        with pytest.raises(CryptoSpecError, match=r"fee_schedule.*tier 10.*taker"):
            load_coin_specs(_write(tmp_path, data))

    @pytest.mark.parametrize("tier", [0, 18])
    def test_default_tier_outside_the_schedule_is_rejected(self, tmp_path, tier):
        data = _raw()
        data["fee_schedule"]["default_tier"] = tier
        with pytest.raises(CryptoSpecError, match=r"fee_schedule.*default_tier"):
            load_coin_specs(_write(tmp_path, data))

    def test_negative_rate_is_rejected(self, tmp_path):
        data = _raw()
        data["fee_schedule"]["tiers"][17]["maker"] = "-0.01"
        with pytest.raises(CryptoSpecError, match=r"fee_schedule\.tiers\.17\.maker"):
            load_coin_specs(_write(tmp_path, data))

    def test_unknown_schema_version_is_rejected(self, tmp_path):
        data = _raw()
        data["schema_version"] = 2
        with pytest.raises(CryptoSpecError, match="schema_version"):
            load_coin_specs(_write(tmp_path, data))

    def test_missing_file(self, tmp_path):
        with pytest.raises(CryptoSpecError, match="not found"):
            load_coin_specs(tmp_path / "nope.yaml")

    def test_not_a_mapping(self, tmp_path):
        path = tmp_path / "coins.yaml"
        path.write_text("- just\n- a list\n")
        with pytest.raises(CryptoSpecError, match="mapping"):
            load_coin_specs(path)

    def test_malformed_yaml(self, tmp_path):
        path = tmp_path / "coins.yaml"
        path.write_text("coins: {BTC: [unclosed\n")
        with pytest.raises(CryptoSpecError, match="YAML"):
            load_coin_specs(path)
