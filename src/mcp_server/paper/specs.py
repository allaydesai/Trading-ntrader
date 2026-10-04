"""Does a session trade exactly the frozen candidate? (the silent-drift guardrail).

Parameters are compared after both sides pass through the strategy's own
parameter model, the way ``live create`` stores them, so ``"1000"`` and
``1000`` are the same value and a real change is not hidden by its spelling.
"""

from collections.abc import Callable
from typing import Any

from src.mcp_server.strategies import resolve_strategy

Normalise = Callable[[dict[str, Any]], dict[str, Any]]


def model_normaliser(strategy: str) -> Normalise:
    """Parameters as the strategy's parameter model dumps them, in JSON form."""
    model = resolve_strategy(strategy).param_model
    if model is None:
        return dict
    return lambda params: model.model_validate(params).model_dump(mode="json")


def _param_differences(stored: Any, frozen: dict[str, Any], normalise: Normalise) -> list[str]:
    try:
        session = normalise(stored if isinstance(stored, dict) else {})
        candidate = normalise(frozen)
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        return [f"parameters do not validate: {str(exc).splitlines()[0]}"]
    return [
        f"param {key}: session {session.get(key)!r}, frozen {candidate.get(key)!r}"
        for key in sorted(set(session) | set(candidate))
        if session.get(key) != candidate.get(key)
    ]


def spec_differences(
    spec: Any,
    *,
    strategy: str,
    symbol: str,
    timeframe: str,
    params: dict[str, Any],
    normalise: Normalise,
) -> list[str]:
    """Every way the stored spec differs from the frozen candidate; empty when it matches."""
    strategies = spec.get("strategies") if isinstance(spec, dict) else None
    if not isinstance(strategies, list) or not strategies:
        return ["the stored spec has no strategies"]
    if len(strategies) != 1:
        return [f"the session runs {len(strategies)} strategies; the candidate is one"]
    entry = strategies[0] if isinstance(strategies[0], dict) else {}
    found = []
    if entry.get("strategy_id") != strategy:
        found.append(f"strategy: session {entry.get('strategy_id')!r}, frozen {strategy!r}")
    found += _param_differences(entry.get("parameters"), params, normalise)
    expected = f"-{timeframe.removesuffix('-LAST')}-"
    for bar_type in entry.get("bar_types") or []:
        if not (str(bar_type).startswith(f"{symbol}.") and expected in str(bar_type)):
            found.append(f"bar type {bar_type!r} is not {symbol} {timeframe}")
    return found
