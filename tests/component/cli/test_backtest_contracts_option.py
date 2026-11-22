"""``backtest run --contracts N`` (futures M4): validation and wiring into the strategy config."""

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner
from rich.console import Console

from src.cli.commands._backtest_helpers import resolve_backtest_request
from src.cli.commands._strategy_params import apply_contracts, check_contracts_instrument
from src.cli.commands.backtest import run_backtest

pytestmark = pytest.mark.component

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime(2024, 3, 31, tzinfo=timezone.utc)
ES = SimpleNamespace(multiplier=Decimal("50"), id="ES.CME")
AAPL = SimpleNamespace(multiplier=Decimal("1"), id="AAPL.NASDAQ")


def _resolve(symbol="ES", strategy="sma_crossover", contracts=None, **extra):
    return resolve_backtest_request(
        config_file=None,
        symbol=symbol,
        strategy=strategy,
        start=START,
        end=END,
        data_source=None,
        starting_balance=None,
        persist=False,
        console=Console(),
        timeframe="1-HOUR",
        catalog_name="firstrate-futures",
        contracts=contracts,
        **extra,
    )[0]


class TestResolve:
    def test_unset_leaves_the_request_unchanged(self):
        request = _resolve()
        assert "contracts" not in request.strategy_config
        assert request.strategy_config["position_size_pct"] == Decimal("10.0")

    def test_contracts_reach_the_strategy_config(self):
        assert _resolve(contracts=2).strategy_config["contracts"] == 2

    def test_lowercase_root_and_dotted_id_are_futures(self):
        assert _resolve(symbol="cl", contracts=1).strategy_config["contracts"] == 1

    def test_equity_symbol_is_a_usage_error(self):
        with pytest.raises(click.UsageError, match="futures"):
            _resolve(symbol="AAPL", contracts=1)

    def test_strategy_without_contracts_is_a_usage_error(self):
        with pytest.raises(click.UsageError, match="sma_momentum"):
            _resolve(strategy="sma_momentum", contracts=1)

    def test_refusal_names_the_strategies_that_support_contracts(self):
        pytest.importorskip("src.core.strategies.custom.riptide")
        with pytest.raises(click.UsageError) as excinfo:
            _resolve(strategy="sma_momentum", contracts=1)
        supported = str(excinfo.value).split("Strategies with one:", 1)[1]
        for name in ("sma_crossover", "bollinger_reversal", "riptide"):
            assert name in supported
        assert "momentum" not in supported

    def test_riptide_takes_contracts_in_cli_mode(self):
        pytest.importorskip("src.core.strategies.custom.riptide")
        assert _resolve(strategy="riptide", contracts=1).strategy_config["contracts"] == 1

    def test_config_mode_flag_overrides_yaml(self, tmp_path):
        config = tmp_path / "es.yaml"
        config.write_text(
            "strategy_path: src.core.strategies.sma_crossover:SMACrossover\n"
            "config_path: src.core.strategies.sma_crossover:SMAConfig\n"
            "config:\n"
            "  instrument_id: ES.CME\n"
            "  bar_type: ES.CME-1-HOUR-LAST-EXTERNAL\n"
            "  contracts: 1\n"
            "backtest:\n"
            '  start_date: "2024-01-01"\n'
            '  end_date: "2024-03-31"\n'
        )
        request = resolve_backtest_request(
            config_file=str(config),
            symbol=None,
            strategy=None,
            start=None,
            end=None,
            data_source=None,
            starting_balance=None,
            persist=False,
            console=Console(),
            contracts=3,
        )[0]
        assert request.strategy_config["contracts"] == 3


class TestApplyContracts:
    def test_none_is_identity(self):
        request = _resolve()
        assert apply_contracts(request, None) is request


class TestInstrumentCheck:
    def test_futures_instrument_passes(self):
        check_contracts_instrument({"contracts": 2}, ES)

    def test_no_contracts_passes_for_anything(self):
        check_contracts_instrument({}, AAPL)
        check_contracts_instrument({"contracts": None}, AAPL)

    def test_equity_instrument_is_refused(self):
        with pytest.raises(ValueError, match="futures"):
            check_contracts_instrument({"contracts": 1}, AAPL)

    @pytest.mark.parametrize("bad", [0, -1, 1.5, "2", True])
    def test_config_file_contracts_must_be_a_positive_int(self, bad):
        with pytest.raises(ValueError, match="positive whole number"):
            check_contracts_instrument({"contracts": bad}, ES)


class TestClickOption:
    def test_help_text_names_futures_and_contracts(self):
        result = CliRunner().invoke(run_backtest, ["--help"])
        assert "--contracts" in result.output
        assert "futures" in result.output.lower()

    def test_help_text_names_no_strategy(self):
        """A hard-coded list goes stale (it once named only sma_crossover)."""
        from src.core.strategy_registry import StrategyRegistry

        option = next(p for p in run_backtest.params if p.name == "contracts")
        assert "'contracts' setting" in option.help
        for name in StrategyRegistry.get_names():
            assert name not in option.help

    def test_zero_contracts_exits_2(self):
        result = CliRunner().invoke(
            run_backtest,
            ["--symbol", "ES", "--start", "2024-01-01", "--end", "2024-02-01", "--contracts", "0"],
        )
        assert result.exit_code == 2
        assert "--contracts" in result.output

    def test_equity_symbol_exits_2(self):
        result = CliRunner().invoke(
            run_backtest,
            [
                "--symbol",
                "AAPL",
                "--start",
                "2024-01-01",
                "--end",
                "2024-02-01",
                "--contracts",
                "1",
            ],
        )
        assert result.exit_code == 2
        assert "futures" in result.output
