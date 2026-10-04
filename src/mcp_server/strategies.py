"""What can be run: registered strategies and backtest-only benchmarks (S1.1, S1.4)."""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from src.config import get_settings
from src.core.benchmarks import BENCHMARKS
from src.core.strategy_factory import StrategyLoader
from src.core.strategy_registry import StrategyRegistry
from src.mcp_server.errors import ToolFailure
from src.mcp_server.jsonable import to_jsonable


@dataclass(frozen=True)
class StrategyRef:
    """A runnable strategy or benchmark, resolved to its canonical name and module paths."""

    name: str
    kind: Literal["strategy", "benchmark"]
    description: str
    aliases: tuple[str, ...]
    strategy_path: str
    config_path: str | None
    param_model: type[BaseModel] | None


def _all_refs() -> list[StrategyRef]:
    StrategyRegistry.discover()
    refs = [
        StrategyRef(
            name=d.name,
            kind="strategy",
            description=d.description,
            aliases=tuple(d.aliases),
            strategy_path=d.strategy_path,
            config_path=d.config_path,
            param_model=d.param_model,
        )
        for d in StrategyRegistry.get_all().values()
    ]
    refs += [
        StrategyRef(
            b.name, "benchmark", b.description, (), b.strategy_path, b.config_path, b.param_model
        )
        for b in BENCHMARKS.values()
    ]
    return sorted(refs, key=lambda r: (r.kind, r.name))


def list_strategies() -> list[dict[str, Any]]:
    """Every runnable name, with its kind, description and aliases."""
    return [
        {"name": r.name, "kind": r.kind, "description": r.description, "aliases": list(r.aliases)}
        for r in _all_refs()
    ]


def resolve_strategy(name: str) -> StrategyRef:
    """Resolve a name or alias; benchmarks are matched by exact name."""
    key = name.strip().lower()
    for ref in _all_refs():
        if key == ref.name or key in ref.aliases:
            return ref
    available = ", ".join(r.name for r in _all_refs())
    raise ToolFailure(
        "unknown_strategy",
        f"No strategy or benchmark named '{name}'.",
        fix=f"Use one of: {available}. Call list_strategies for descriptions.",
    )


def _defaults(ref: StrategyRef) -> dict[str, Any]:
    if ref.param_model is None:
        return {}
    if ref.kind == "strategy":
        return StrategyLoader.build_strategy_params(ref.name, {}, get_settings())
    return ref.param_model().model_dump()


def _settings_map(ref: StrategyRef) -> dict[str, str]:
    """Fields whose default a global setting supplies (strategies only; see ``_defaults``)."""
    if ref.kind != "strategy" or ref.param_model is None:
        return {}
    mapping = getattr(ref.param_model, "_settings_map", {})
    if not isinstance(mapping, dict):  # pydantic may wrap the private attribute
        mapping = getattr(mapping, "default", {}) or {}
    return dict(mapping)


def default_sources(
    ref: StrategyRef, explicit: frozenset[str] = frozenset()
) -> tuple[dict[str, str], list[str]]:
    """Where each default comes from, and a warning per setting that overrides the schema.

    A mapped global setting always beats the parameter model's default (the CLI's
    precedence), so the schema's ``default`` is not what a run gets for that field.
    Fields named in ``explicit`` were given by the caller and are not warned about.
    """
    if ref.param_model is None:
        return {}, []
    mapping, resolved = _settings_map(ref), _defaults(ref)
    sources: dict[str, str] = {}
    warnings: list[str] = []
    for name, field in ref.param_model.model_fields.items():
        setting = mapping.get(name)
        sources[name] = f"setting {setting.upper()}" if setting else "model"
        if setting and name not in explicit and resolved.get(name) != field.default:
            warnings.append(
                f"{name} resolves to {resolved.get(name)} from the {setting.upper()} setting "
                f"(.env), not the schema default {field.default}. Pass it in params to be explicit."
            )
    return sources, warnings


def resolve_params(ref: StrategyRef, overrides: dict[str, Any]) -> dict[str, Any]:
    """Validate ``overrides`` against the parameter model and return the full parameter set.

    Precedence matches ``StrategyLoader.build_strategy_params``: explicit value,
    then the global setting the model maps, then the model default. Unknown names
    are refused rather than silently ignored.
    """
    if ref.param_model is None:
        if overrides:
            raise ToolFailure(
                "invalid_params", f"'{ref.name}' takes no parameters.", fix="Pass params={}."
            )
        return {}
    known = set(ref.param_model.model_fields)
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise ToolFailure(
            "invalid_params",
            f"Unknown parameter(s) for '{ref.name}': {', '.join(unknown)}.",
            fix=f"Valid parameters: {', '.join(sorted(known))}.",
        )
    explicit = {k: v for k, v in overrides.items() if v is not None}
    try:
        return ref.param_model(**{**_defaults(ref), **explicit}).model_dump()
    except ValidationError as exc:
        fields = [
            {"field": ".".join(str(p) for p in e["loc"]) or "(model)", "error": e["msg"]}
            for e in exc.errors()
        ]
        raise ToolFailure(
            "invalid_params",
            f"Parameters for '{ref.name}' are invalid.",
            fix="Correct the listed fields; describe_strategy shows the schema.",
            details={"fields": fields},
        ) from None


def describe_strategy(name: str) -> dict[str, Any]:
    """Parameter schema, resolved defaults and a minimal submit_backtest request."""
    ref = resolve_strategy(name)
    schema = ref.param_model.model_json_schema() if ref.param_model else {"properties": {}}
    sources, warnings = default_sources(ref)
    return {
        "name": ref.name,
        "kind": ref.kind,
        "description": ref.description,
        "aliases": list(ref.aliases),
        "param_schema": schema,
        "defaults": to_jsonable(_defaults(ref)),
        "default_sources": sources,
        "example_request": {
            "strategy": ref.name,
            "symbol": "QQQ",
            "start": "2010-01-01",
            "end": "2015-12-31",
            "timeframe": "1-DAY",
            "catalog": "firstrate-etf",
            "params": {},
        },
        "warnings": warnings,
    }
