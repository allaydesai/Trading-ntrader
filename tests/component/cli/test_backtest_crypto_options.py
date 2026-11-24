"""``backtest run --fee-tier N`` (crypto M3, design section 4, decision C21).

One Kraken Pro spot tier per run: the flag is validated against the shipped schedule (1..17)
and refused for a symbol that names no coin spec (usage errors, exit 2). It reaches the request
(``crypto_fee_tier``, part of the run's config hash only when set) and is applied to the
instrument (``info["fee_tier"]``), which the fee model charges and ``config_snapshot.fee_tier``
records. M5 adds the ``BTC/USD`` symbol forms and ``--spread-bps``.
"""

from datetime import datetime, timezone

import click
import pytest
from click.testing import CliRunner
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from rich.console import Console

from src.cli.commands._backtest_helpers import resolve_backtest_request
from src.cli.commands._crypto_options import with_fee_tier
from src.cli.commands.backtest import run_backtest
from src.core.crypto_costs import crypto_terms
from src.models.backtest_request import BacktestRequest
from src.services.crypto.coin_specs import get_coin_spec
from src.services.crypto.instrument_builder import build_crypto_pair
from src.services.provenance import compute_config_hash

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
        rebuilt = with_fee_tier(pair, 4)
        assert rebuilt.id == pair.id and rebuilt.price_precision == 2
        assert rebuilt.info["fee_tier"] == 4
        assert crypto_terms(rebuilt).fee_tier == 4

    def test_no_tier_leaves_the_instrument(self):
        pair = build_crypto_pair(get_coin_spec("ETH"))
        assert with_fee_tier(pair, None) is pair
        equity = TestInstrumentProvider.equity("AAPL", "XNAS")
        assert with_fee_tier(equity, None) is equity

    def test_a_non_crypto_instrument_is_refused(self):
        with pytest.raises(ValueError, match="fee tier applies to spot crypto only"):
            with_fee_tier(TestInstrumentProvider.equity("AAPL", "XNAS"), 2)
