"""The immutable session specification and its status vocabulary.

Owns: ``SessionSpec`` and ``StrategySpec`` — the frozen, JSON-losslessly
round-trippable description of what a paper-trading session runs, resolved
and validated at construction time — and ``SessionStatus``, the four-value
lifecycle vocabulary a session moves through.

Does not own: the database row (Story 2.2's ``TradingSession`` ORM model and
repositories), the state machine that moves a session between statuses
(Story 2.3), the session's durable cache or ``trader_id`` (Story 2.4), any
CLI surface, or the runner that starts a Nautilus node from a spec
(Story 2.5). This module never imports Nautilus, IBKR, SQLAlchemy, or Redis
at the top level — every framework touch below is a lazy import inside the
function that needs it, so importing ``src.models`` stays cheap for callers
that never build a live session.

Known, accepted limit: ``frozen=True`` blocks attribute rebinding, not
mutation of a field's contents. ``StrategySpec.parameters`` is a plain
``dict[str, Any]`` and can still be mutated in place after construction. The
immutability guarantee this story's callers actually need is on the
persisted record: no code path writes back to a stored spec's JSONB column
(Story 2.2), so mutating an in-memory copy loaded from the database corrupts
nothing on disk.
"""

from collections.abc import Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

if TYPE_CHECKING:
    from src.core.strategy_registry import StrategyDefinition


class SessionStatus(StrEnum):
    """The four states a paper-trading session moves through (FR20, AR36).

    ``StrEnum`` is deliberate, not an inconsistency with the repo's usual
    ``(str, Enum)`` habit: it makes ``str()``, f-strings, and JSON encoding
    render the bare value (``"created"``) rather than ``"SessionStatus.CREATED"``,
    which is what a JSONB column and a ``--json`` CLI payload both need.
    """

    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"
    SEALED = "sealed"


def _lookup_strategy(strategy_id: str) -> "StrategyDefinition":
    """Resolve a strategy identifier through the registry, or raise ``ValueError``.

    Args:
        strategy_id: A canonical name, alias, or fuzzy-matchable identifier.

    Returns:
        The registered strategy definition.

    Raises:
        ValueError: No strategy matches. Pydantic converts ``ValueError`` (and
            only ``ValueError``/``AssertionError``) into a ``ValidationError``;
            the registry's own ``KeyError`` would otherwise escape unchanged.
    """
    from src.core.strategy_registry import StrategyRegistry

    try:
        return StrategyRegistry.get(strategy_id)
    except KeyError:
        raise ValueError(
            f"Unknown strategy {strategy_id!r}. Registered strategies: "
            f"{', '.join(sorted(StrategyRegistry.get_names()))}."
        ) from None


def _normalise_parameters(
    definition: "StrategyDefinition", parameters: dict[str, Any]
) -> dict[str, Any]:
    """Validate and normalise parameters through the strategy's own param model.

    Args:
        definition: The resolved strategy definition.
        parameters: Raw parameter values, e.g. from CLI overrides or a JSON
            round trip (where a prior serialisation may have stringified a
            ``Decimal``).

    Returns:
        The parameters re-coerced through ``definition.param_model``, so
        ``Decimal`` fields round-trip losslessly.

    Raises:
        ValueError: The strategy has no registered parameter model, or the
            parameters fail that model's validation.
    """
    if definition.param_model is None:
        raise ValueError(
            f"Strategy {definition.name!r} registers no parameter model, so its parameters "
            "cannot be validated or stored losslessly. Add "
            "StrategyRegistry.set_param_model() at the bottom of its module."
        )
    try:
        return definition.param_model.model_validate(parameters).model_dump()
    except PydanticValidationError as exc:
        raise ValueError(f"Invalid parameters for {definition.name!r}: {exc}") from exc


def _resolve_bar_types(raw: Sequence[str]) -> tuple[str, ...]:
    """Validate bar-type strings through the live market-data policy.

    Args:
        raw: Bar-type strings, e.g. ``["AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"]``.

    Returns:
        The same bar types as strings, in the order given.

    Raises:
        ValueError: An entry does not parse, is INTERNAL-aggregated, is
            composite, is not time-aggregated, or repeats an earlier entry
            (case-insensitively). ``resolve_live_bar_types`` raises a plain
            ``LiveMarketDataError``, which pydantic would not convert.
    """
    from src.core.live_market_data import LiveMarketDataError, resolve_live_bar_types

    try:
        return tuple(str(bar_type) for bar_type in resolve_live_bar_types(list(raw)))
    except LiveMarketDataError as exc:
        raise ValueError(str(exc)) from exc


class StrategySpec(BaseModel):
    """One strategy's identity, resolved parameters, and target instruments.

    Attributes:
        strategy_id: Canonical ``StrategyRegistry`` name — aliases resolve to
            this at construction, so two specs built from an alias and its
            canonical name compare equal.
        parameters: Fully resolved parameter values, validated and normalised
            through the strategy's own registered parameter model.
        bar_types: Bar-type strings this strategy subscribes to, each valid
            per ``resolve_live_bar_types`` (EXTERNAL, non-composite,
            time-aggregated, no intra-spec duplicate).

    Raises:
        ValidationError: ``strategy_id`` does not resolve, ``parameters``
            fail the strategy's parameter model, or ``bar_types`` is empty or
            contains an entry ``resolve_live_bar_types`` refuses.
    """

    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(..., min_length=1, description="Canonical StrategyRegistry name")
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Fully resolved strategy parameters"
    )
    bar_types: tuple[str, ...] = Field(
        ..., min_length=1, description="Target instruments as bar-type strings"
    )

    @model_validator(mode="before")
    @classmethod
    def _resolve_through_registry(cls, data: Any) -> Any:
        """Canonicalise ``strategy_id``, normalise ``parameters``, validate ``bar_types``.

        A ``mode="before"`` validator, not ``mode="after"``: an after-validator
        cannot assign to a frozen model's fields, and parameter normalisation
        needs ``strategy_id`` and ``parameters`` together, which a per-field
        validator cannot see. This also re-runs on ``model_validate_json``, so
        a ``Decimal`` parameter that a prior serialisation stringified is
        re-coerced back through the param model on the way in.
        """
        if not isinstance(data, dict):
            return data
        raw_id = data.get("strategy_id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            return data  # let the field constraint report the real problem

        definition = _lookup_strategy(raw_id.strip())
        resolved = dict(data)
        resolved["strategy_id"] = definition.name
        resolved["parameters"] = _normalise_parameters(definition, data.get("parameters") or {})
        if data.get("bar_types") is not None:
            resolved["bar_types"] = _resolve_bar_types(data["bar_types"])
        return resolved

    @classmethod
    def from_overrides(
        cls,
        *,
        strategy_id: str,
        overrides: dict[str, Any],
        settings: Any,
        bar_types: Sequence[str],
    ) -> "StrategySpec":
        """Build a spec by resolving parameters through the strategy-loader chain.

        Args:
            strategy_id: A canonical name, alias, or fuzzy-matchable identifier.
            overrides: Explicit parameter overrides, e.g. from the CLI.
            settings: The settings object to pull parameter defaults from.
                Injected, never fetched — this classmethod never loads
                settings on its own.
            bar_types: Target instruments as bar-type strings.

        Returns:
            A fully resolved, frozen ``StrategySpec``.
        """
        from src.core.strategy_factory import StrategyLoader

        parameters = StrategyLoader.build_strategy_params(strategy_id, dict(overrides), settings)
        return cls(strategy_id=strategy_id, parameters=parameters, bar_types=tuple(bar_types))


class SessionSpec(BaseModel):
    """An immutable, ordered collection of strategy specifications (FR13, FR14).

    Attributes:
        schema_version: Written once into JSONB and read forever; lets a
            future field addition tell old rows from new.
        strategies: One or more ``StrategySpec`` entries, in the order given.
            A ``tuple``, not a ``list`` — see the module docstring's note on
            ``frozen=True`` being shallow.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: int = Field(default=1, ge=1, description="Spec schema version")
    strategies: tuple[StrategySpec, ...] = Field(
        ..., min_length=1, description="Ordered, non-empty collection of strategy specs"
    )

    @property
    def subscription_bar_types(self) -> tuple[str, ...]:
        """Every strategy's bar types, flattened and deduplicated across strategies.

        Two strategies legitimately trading the same instrument is not a
        duplicate at the session level — only within one strategy's own
        ``bar_types`` is a repeat rejected. Deduplication is case-insensitive,
        keyed the same way ``resolve_live_bar_types`` keys its own dedup, and
        preserves first-seen order. This is what Story 2.5 hands to
        ``build_trading_node_config(bar_types=...)``.
        """
        ordered: list[str] = []
        seen: set[str] = set()
        for strategy in self.strategies:
            for bar_type in strategy.bar_types:
                key = bar_type.upper()
                if key not in seen:
                    seen.add(key)
                    ordered.append(bar_type)
        return tuple(ordered)
