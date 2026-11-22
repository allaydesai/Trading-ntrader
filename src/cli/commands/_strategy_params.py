"""Strategy-config params the backtest CLI sets: CLI-mode sizing and ``--contracts``.

Both decide by what the strategy's config class declares (``portfolio_value``,
``contracts``), not by strategy name, so a new strategy picks them up without an edit here.
"""

from decimal import Decimal

import click

from src.models.backtest_request import BacktestRequest

#: The setting ``--contracts`` writes into a strategy's config (``SMAConfig.contracts``).
CONTRACTS_KEY = "contracts"


def _config_declares(config_path: str | None, field_name: str) -> bool:
    """Whether the strategy config class at ``module:Class`` has ``field_name``."""
    if not config_path or ":" not in config_path:
        return False
    import importlib

    module_name, class_name = config_path.split(":", 1)
    config_cls = getattr(importlib.import_module(module_name), class_name, None)
    return field_name in getattr(config_cls, "__struct_fields__", ())


def _strategies_declaring(field_name: str) -> str:
    """Comma-separated registered strategies whose config has ``field_name``."""
    from src.core.strategy_registry import StrategyRegistry

    names = sorted(
        name
        for name, definition in StrategyRegistry.get_all().items()
        if _config_declares(definition.config_path, field_name)
    )
    return ", ".join(names) or "none"


def _is_futures_symbol(symbol: str) -> bool:
    """A root in the futures contract specs (ES, NQ, CL, GC, ZN), dotted or not."""
    from src.services.futures.contract_specs import load_contract_specs

    return symbol.split(".")[0].upper() in load_contract_specs().roots


def apply_contracts(request: BacktestRequest, contracts: int | None) -> BacktestRequest:
    """``--contracts N``: set the strategy's whole-contract size; ``None`` changes nothing.

    Raises:
        click.UsageError: The strategy's config has no ``contracts`` setting, or the symbol
            is not a futures root (a symbol that only collides with one, an equity ``CL``,
            is caught after the instrument loads: :func:`check_contracts_instrument`).
    """
    if contracts is None:
        return request
    if not _config_declares(request.config_path, CONTRACTS_KEY):
        supported = _strategies_declaring(CONTRACTS_KEY)
        raise click.UsageError(
            f"--contracts is not supported by strategy '{request.strategy_type}': its config "
            f"has no 'contracts' setting. Strategies with one: {supported}."
        )
    if not _is_futures_symbol(request.symbol):
        raise click.UsageError(
            f"--contracts applies to futures only; '{request.symbol}' is not a futures root "
            "(ES, NQ, CL, GC, ZN in catalog firstrate-futures)."
        )
    config = {**request.strategy_config, CONTRACTS_KEY: contracts}
    return request.model_copy(update={"strategy_config": config})


def check_contracts_instrument(strategy_config: dict, instrument: object) -> None:
    """Refuse a ``contracts`` size (flag or config file) that isn't N >= 1 on a futures contract.

    Raises:
        ValueError: ``contracts`` is not a positive whole number, or the instrument has no
            multiplier (not futures).
    """
    contracts = strategy_config.get(CONTRACTS_KEY)
    if contracts is None:
        return
    if isinstance(contracts, bool) or not isinstance(contracts, int) or contracts < 1:
        raise ValueError(f"contracts must be a positive whole number, got {contracts!r}")
    from src.core.position_sizing import instrument_multiplier

    if instrument_multiplier(instrument) == 1:
        raise ValueError(
            f"contracts applies to futures only; {getattr(instrument, 'id', instrument)} "
            "is not a futures contract"
        )


#: CLI-mode strategies that also take the SMA ``--fast-period``/``--slow-period`` flags.
_SMA_CLI_STRATEGIES = ("sma", "sma_crossover", "sma_crossover_long_only")


def _cli_strategy_params(
    strategy: str,
    portfolio_value: Decimal,
    trade_size: int,
    fast_period: int,
    slow_period: int,
) -> dict:
    """CLI-mode strategy params: size from the account for any config with ``portfolio_value``.

    The account starts at ``portfolio_value``; a config left on its own default would size
    every trade off a different amount. Configs without the field get the legacy
    ``trade_size``. An unknown strategy falls through to ``trade_size`` and is refused by
    :meth:`BacktestRequest.from_cli_args`.
    """
    from src.core.strategy_registry import StrategyRegistry

    try:
        config_path = StrategyRegistry.get(strategy).config_path
    except KeyError:
        config_path = None
    if not _config_declares(config_path, "portfolio_value"):
        return {"trade_size": trade_size}
    params: dict = {"portfolio_value": portfolio_value}
    if strategy in _SMA_CLI_STRATEGIES:
        params.update(
            position_size_pct=Decimal("10.0"), fast_period=fast_period, slow_period=slow_period
        )
    return params
