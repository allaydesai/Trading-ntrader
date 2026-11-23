"""Strategy-config params the backtest CLI sets: CLI-mode sizing and ``--contracts``.

Both decide by what the strategy's config class declares (``portfolio_value``,
``contracts``), not by strategy name, so a new strategy picks them up without an edit here.
The shared checks live in :mod:`src.services.sizing_overrides` (the MCP uses them too).
"""

from decimal import Decimal

import click

from src.models.backtest_request import BacktestRequest
from src.services.sizing_overrides import (
    CONTRACTS_KEY,
    config_declares,
    is_futures_symbol,
    strategies_declaring,
)


def apply_contracts(request: BacktestRequest, contracts: int | None) -> BacktestRequest:
    """``--contracts N``: set the strategy's whole-contract size; ``None`` changes nothing.

    Raises:
        click.UsageError: The strategy's config has no ``contracts`` setting, or the symbol
            is not a futures root (a symbol that only collides with one, an equity ``CL``,
            is caught after the instrument loads:
            :func:`src.services.sizing_overrides.check_contracts_instrument`).
    """
    if contracts is None:
        return request
    if not config_declares(request.config_path, CONTRACTS_KEY):
        supported = strategies_declaring(CONTRACTS_KEY)
        raise click.UsageError(
            f"--contracts is not supported by strategy '{request.strategy_type}': its config "
            f"has no 'contracts' setting. Strategies with one: {supported}."
        )
    if not is_futures_symbol(request.symbol):
        raise click.UsageError(
            f"--contracts applies to futures only; '{request.symbol}' is not a futures root "
            "(ES, NQ, CL, GC, ZN in catalog firstrate-futures)."
        )
    config = {**request.strategy_config, CONTRACTS_KEY: contracts}
    return request.model_copy(update={"strategy_config": config})


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

    ``portfolio_value`` is the account's starting balance (``--starting-balance``, else
    ``trade_size x 10``); a config left on its own default would size every trade off a
    different amount. Configs without the field get the legacy ``trade_size``. An unknown
    strategy falls through to ``trade_size`` and is refused by
    :meth:`BacktestRequest.from_cli_args`.
    """
    from src.core.strategy_registry import StrategyRegistry

    try:
        config_path = StrategyRegistry.get(strategy).config_path
    except KeyError:
        config_path = None
    if not config_declares(config_path, "portfolio_value"):
        return {"trade_size": trade_size}
    params: dict = {"portfolio_value": portfolio_value}
    if strategy in _SMA_CLI_STRATEGIES:
        params.update(
            position_size_pct=Decimal("10.0"), fast_period=fast_period, slow_period=slow_period
        )
    return params
