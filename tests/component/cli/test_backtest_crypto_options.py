"""``backtest run --fee-tier N`` (crypto M3, design section 4, decision C21).

One Kraken Pro spot tier per run: the flag is validated against the shipped schedule (1..17)
and refused for a symbol that names no coin spec (usage errors, exit 2). It reaches the request
(``crypto_fee_tier``, part of the run's config hash only when set) and is applied to the
instrument (``info["fee_tier"]``), which the fee model charges and ``config_snapshot.fee_tier``
records. M5 adds the ``BTC/USD`` symbol forms and ``--spread-bps``.

Crypto M4 (task 4.5): ``--units`` takes a decimal coin size, a positive multiple of the coin's
size increment at or above Kraken's minimum order; anything else exits 2 naming the rule.
"""

from datetime import datetime, timezone
from decimal import Decimal

import click
import pytest
from click.testing import CliRunner
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from rich.console import Console

from src.cli.commands._backtest_helpers import resolve_backtest_request
from src.cli.commands._crypto_options import with_crypto_terms
from src.cli.commands.backtest import run_backtest
from src.core.crypto_costs import crypto_terms
from src.models.backtest_request import BacktestRequest
from src.services.crypto.coin_specs import get_coin_spec
from src.services.crypto.instrument_builder import build_crypto_pair
from src.services.provenance import compute_config_hash
from src.services.sizing_overrides import check_units_instrument

pytestmark = pytest.mark.component

START = datetime(2024, 1, 2, tzinfo=timezone.utc)
END = datetime(2024, 2, 1, tzinfo=timezone.utc)


def _resolve(symbol="BTC", **extra) -> BacktestRequest:
    return resolve_backtest_request(
        config_file=None,
        symbol=symbol,
        strategy="sma_crossover",
        start=START,
        end=END,
        data_source=None,
        starting_balance=None,
        persist=False,
        console=Console(),
        timeframe="1-DAY",
        catalog_name="firstrate-crypto",
        **extra,
    )[0]


class TestRequest:
    def test_the_tier_reaches_the_request(self):
        assert _resolve(fee_tier=4).crypto_fee_tier == 4

    def test_unset_is_none(self):
        assert _resolve().crypto_fee_tier is None

    def test_a_tier_outside_the_schedule_is_a_usage_error(self):
        with pytest.raises(click.UsageError, match=r"--fee-tier must be a Kraken tier 1\.\.17"):
            _resolve(fee_tier=18)

    def test_a_non_crypto_symbol_is_a_usage_error(self):
        with pytest.raises(click.UsageError, match="--fee-tier is for spot crypto only"):
            _resolve(symbol="AAPL", fee_tier=2)

    @pytest.mark.parametrize("tier", ["0", "18"])
    def test_the_command_exits_2(self, tier):
        result = CliRunner().invoke(
            run_backtest,
            ["--symbol", "BTC", "--start", "2024-01-02", "--end", "2024-02-01"]
            + ["--catalog", "firstrate-crypto", "--no-persist", "--fee-tier", tier],
        )
        assert result.exit_code == 2, result.output

    def test_the_tier_is_part_of_the_run_identity_only_when_set(self):
        plain = _resolve()
        assert compute_config_hash(plain) == compute_config_hash(_resolve())
        assert compute_config_hash(_resolve(fee_tier=4)) != compute_config_hash(plain)


class TestInstrument:
    def test_the_pair_is_rebuilt_at_the_tier(self):
        pair = build_crypto_pair(get_coin_spec("BTC"), price_precision=2)
        rebuilt = with_crypto_terms(pair, 4)
        assert rebuilt.id == pair.id and rebuilt.price_precision == 2
        assert rebuilt.info["fee_tier"] == 4
        assert crypto_terms(rebuilt).fee_tier == 4

    def test_no_tier_leaves_the_instrument(self):
        pair = build_crypto_pair(get_coin_spec("ETH"))
        assert with_crypto_terms(pair, None) is pair
        equity = TestInstrumentProvider.equity("AAPL", "XNAS")
        assert with_crypto_terms(equity, None) is equity

    def test_a_non_crypto_instrument_is_refused(self):
        with pytest.raises(ValueError, match="fee tier or spread applies to spot crypto only"):
            with_crypto_terms(TestInstrumentProvider.equity("AAPL", "XNAS"), 2)


class TestUnits:
    """Crypto M4 (task 4.5): ``--units`` takes a decimal coin size for spot crypto."""

    def test_decimal_units_reach_the_strategy_config_exactly(self):
        config = _resolve(units=Decimal("0.25")).strategy_config
        assert config["units"] == Decimal("0.25") and isinstance(config["units"], Decimal)

    def test_whole_coin_units_are_decimal_too(self):
        assert _resolve(symbol="ETH", units=Decimal("2")).strategy_config["units"] == 2

    def test_units_are_part_of_the_run_identity(self):
        plain, sized = _resolve(), _resolve(units=Decimal("0.25"))
        assert compute_config_hash(sized) != compute_config_hash(plain)
        assert compute_config_hash(sized) == compute_config_hash(_resolve(units=Decimal("0.250")))

    def test_off_the_increment_is_a_usage_error_naming_it(self):
        with pytest.raises(click.UsageError, match="size increment 0.00000001"):
            _resolve(units=Decimal("0.000000001"))

    def test_under_the_kraken_minimum_is_a_usage_error(self):
        with pytest.raises(click.UsageError, match="minimum order 0.00005"):
            _resolve(units=Decimal("0.00001"))

    def test_a_strategy_without_units_is_still_refused(self):
        with pytest.raises(click.UsageError, match="no 'units' setting"):
            resolve_backtest_request(
                config_file=None,
                symbol="BTC",
                strategy="sma_momentum",
                start=START,
                end=END,
                data_source=None,
                starting_balance=None,
                persist=False,
                console=Console(),
                timeframe="1-DAY",
                catalog_name="firstrate-crypto",
                units=Decimal("0.25"),
            )

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (["--symbol", "BTC", "--units", "0.000000001"], "size increment 0.00000001"),
            (["--symbol", "BTC", "--units", "0.00001"], "minimum order"),
            (["--symbol", "BTC", "--units", "0"], "positive"),
            (["--symbol", "BTC", "--units", "abc"], "decimal"),
            (["--symbol", "AAPL", "--units", "0.25"], "--units is for FX only"),
        ],
    )
    def test_the_command_exits_2(self, args, message):
        base = ["--start", "2024-01-02", "--end", "2024-02-01", "--no-persist"]
        result = CliRunner().invoke(run_backtest, args + base + ["--catalog", "firstrate-crypto"])
        assert result.exit_code == 2, result.output
        assert message in " ".join(result.output.split())

    def test_the_instrument_check_accepts_a_crypto_size(self):
        pair = build_crypto_pair(get_coin_spec("BTC"), price_precision=4)
        check_units_instrument({"units": Decimal("0.25")}, pair)
        check_units_instrument({"units": 0.25}, pair)  # a config file's YAML float
        check_units_instrument({"units": 1}, pair)

    @pytest.mark.parametrize(
        ("units", "message"),
        [
            (Decimal("0.000000015"), "size increment"),
            (Decimal("0.00001"), "minimum order"),
            (Decimal("-0.25"), "positive"),
            (True, "positive"),
            ("lots", "positive"),
        ],
    )
    def test_the_instrument_check_refuses_a_bad_crypto_size(self, units, message):
        pair = build_crypto_pair(get_coin_spec("BTC"), price_precision=4)
        with pytest.raises(ValueError, match=message):
            check_units_instrument({"units": units}, pair)
