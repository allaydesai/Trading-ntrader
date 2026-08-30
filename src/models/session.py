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

Known, accepted limits. Each is pinned by a test in
``tests/unit/models/test_session_spec.py::TestKnownLimits`` so it stays true,
and none of them is a guarantee this module makes:

1. ``frozen=True`` blocks attribute rebinding, not mutation of a field's
   contents. ``StrategySpec.parameters`` is a plain ``dict[str, Any]`` and can
   still be mutated in place after construction.
2. ``model_copy(update=...)`` bypasses both ``frozen=True`` and the
   before-validator, so it can mint a ``SessionSpec`` with zero strategies or
   an unregistered ``strategy_id``. Pydantic behaves this way for every model;
   it is not closed here.
3. The specs are **not hashable**. ``frozen=True`` generates a ``__hash__``
   over the field values, and the ``parameters`` dict makes it raise
   ``TypeError``, so a spec cannot go in a ``set`` or serve as a dict key.
4. ``schema_version`` is enforced on the way *in*, not on the way out.
   ``from_stored`` refuses a payload whose version is newer than
   ``SPEC_SCHEMA_VERSION`` or is not an integer, but nothing downstream branches
   on the version to reinterpret an *older* payload — there is only one version
   so far. A future v2 must add that branch itself.

"JSON-losslessly round-trippable" above means the
``model_dump_json()``/``model_validate_json()`` pair specifically.
``model_dump()`` is python-mode and preserves ``Decimal``, so
``json.dumps(spec.model_dump())`` raises ``TypeError`` — persist via
``model_dump_json()`` or ``model_dump(mode="json")``.

What none of the above threatens is the persisted record: the immutability
FR14 needs is enforced by there being no write-back path to a stored spec's
JSONB column. Story 2.2 added the write path (``TradingSessionRepository`` and
its sync twin) and kept it write-once — those repositories expose ``create``
and three finders, and no setter of any kind.

The stored form is ``SessionSpec.to_stored()`` / ``SessionSpec.from_stored()``:
one sanctioned serialiser and one sanctioned reader, so the ``mode="json"`` rule
and the version gate each live in exactly one greppable function.
"""

from collections.abc import Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

if TYPE_CHECKING:
    from src.core.strategy_registry import StrategyDefinition


#: Written once into a session's stored JSONB payload and read forever. Lets a
#: future field addition tell old rows from new. See ``SessionSpec.from_stored``
#: for the refusal this constant gates.
SPEC_SCHEMA_VERSION = 1

#: AR32's heartbeat write cadence ("~every 30s"). Declared *here*, in the one
#: session module that imports no framework at all, because three layers need
#: the same number and they cannot all reach the same place: Story 2.3's
#: ``session_service`` derives its staleness threshold from it (and imports
#: SQLAlchemy), Story 2.5's ``live_session_runner`` drives its heartbeat from it
#: (and may not import SQLAlchemy, AR38), and Story 2.8's health derivation will
#: read it too. ``session_service`` re-exports it, so Story 2.3's callers and
#: tests are unaffected by where it lives.
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30.0


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


_NO_PARAM_MODEL = (
    "Strategy {name!r} registers no parameter model, so its parameters cannot be validated "
    "or stored losslessly. Add StrategyRegistry.set_param_model() at the bottom of its module."
)


def _normalise_parameters(definition: "StrategyDefinition", parameters: Any) -> dict[str, Any]:
    """Validate and normalise parameters through the strategy's own param model.

    Args:
        definition: The resolved strategy definition.
        parameters: Raw parameter values, e.g. from CLI overrides or a JSON
            round trip (where a prior serialisation may have stringified a
            ``Decimal``). Not necessarily a dict — a wrong-typed value must
            surface as a validation failure, not a crash.

    Returns:
        The parameters re-coerced through ``definition.param_model``, so
        ``Decimal`` fields round-trip losslessly.

    Raises:
        ValueError: The strategy has no registered parameter model, or the
            parameters fail that model's validation.
    """
    if definition.param_model is None:
        raise ValueError(_NO_PARAM_MODEL.format(name=definition.name))
    try:
        return definition.param_model.model_validate(parameters).model_dump()
    except PydanticValidationError as exc:
        # Interpolating ``exc`` whole would embed pydantic's full multi-line
        # block — header, ``input_value=``, docs URL — inside a message pydantic
        # then wraps and re-renders with a second header and second URL. Reduce
        # it to the one line per error that an operator can act on.
        detail = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
            for error in exc.errors()
        )
        raise ValueError(f"Invalid parameters for {definition.name!r}: {detail}") from exc


def _resolve_bar_types(raw: Any) -> tuple[str, ...]:
    """Validate bar-type strings and store them in canonical upper-case form.

    Args:
        raw: A list or tuple of bar-type strings, e.g.
            ``["AAPL.NASDAQ-1-MINUTE-LAST-EXTERNAL"]``. Typed ``Any`` rather
            than ``Sequence[str]`` because the value arrives unvalidated and a
            wrong shape has to become a ``ValidationError``, not a ``TypeError``.

    Returns:
        The bar types, upper-cased, in the order given.

        ``BarType.from_str`` upper-cases the aggregation but preserves the
        instrument-id case, so ``aapl.nasdaq-...`` survives parsing intact and
        would reach ``load_ids`` as a lowercase id IBKR cannot resolve — a
        silently dead subscription (``live_market_data.py:199-204``).
        Canonicalising here is the same rule ``strategy_id`` already follows:
        store the canonical form, so two specs describing one thing compare
        equal and a later comparison is not reading noise.

    Raises:
        ValueError: ``raw`` is not an ordered sequence, or an entry does not
            parse, is INTERNAL-aggregated, is composite, is not time-aggregated,
            or repeats an earlier entry (case-insensitively).
            ``resolve_live_bar_types`` raises a plain ``LiveMarketDataError``,
            which pydantic would not convert.
    """
    from src.core.live_market_data import LiveMarketDataError, resolve_live_bar_types

    if not isinstance(raw, (str, list, tuple)):
        # A set or dict has no stable order, and a non-iterable would raise
        # TypeError from inside the loop below — neither of which pydantic
        # converts into a ValidationError. A bare str is deliberately allowed
        # through: `resolve_live_bar_types` has its own purpose-built message
        # for that case, and pre-converting with `list()` would explode it into
        # one bar type per character and destroy that message.
        raise ValueError(
            f"Expected a list or tuple of bar-type strings, got {type(raw).__name__}. "
            "An unordered or non-sequence collection has no order to store."
        )

    try:
        return tuple(str(bar_type).upper() for bar_type in resolve_live_bar_types(raw))
    except LiveMarketDataError as exc:
        raise ValueError(str(exc)) from exc


def _resolve_order_id_tag(parameters: dict[str, Any], position: int) -> str:
    """Nautilus's own resolution, replicated exactly (Story 3.2, Task 6.4).

    Measured against the installed wheel (``trading/trader.py:406-412``): an
    explicit ``order_id_tag`` wins; otherwise the positional auto-assign
    ``f"{count:03d}"``, where ``count`` is the number of *already-resolved*
    entries — not the number lacking an explicit tag. ``str(None)`` is
    checked alongside ``None`` because a spec round-tripped through JSON can
    carry the literal string, and Nautilus's own comparison
    (``strategy.order_id_tag in (None, str(None))``) does too.

    ⚠️ Drift-pin: if Nautilus changes this shape, the component pins in
    ``tests/component/core/test_client_order_id_determinism.py`` (format and
    positional-assignment) go red first.

    Args:
        parameters: One strategy spec's resolved parameters.
        position: How many entries have already been resolved in this walk.
    """
    explicit = parameters.get("order_id_tag")
    if explicit is None or explicit == "None":
        return f"{position:03d}"
    return str(explicit)


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

    model_config = ConfigDict(frozen=True, extra="forbid")

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
        if not isinstance(raw_id, str):
            return data  # let the field's type constraint report the real problem
        if not raw_id:
            return data  # let min_length report `string_too_short`
        if not raw_id.strip():
            # `min_length=1` counts characters, so a whitespace-only id satisfies
            # it. Returning here would skip the registry, the parameter model and
            # the bar-type policy alike, yielding a fully unvalidated spec.
            raise ValueError(
                "strategy_id is blank. Give a registered StrategyRegistry name, "
                "e.g. 'sma_crossover'."
            )

        definition = _lookup_strategy(raw_id.strip())
        resolved = dict(data)
        resolved["strategy_id"] = definition.name
        raw_parameters = data.get("parameters")
        if raw_parameters is None:
            # `or {}` would also swallow `[]`, `0`, `""` and `False`, turning a
            # wrong-typed value into a silent all-defaults spec.
            raw_parameters = {}
        resolved["parameters"] = _normalise_parameters(definition, raw_parameters)
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

        Raises:
            ValueError: ``strategy_id`` does not resolve, or the strategy
                registers no parameter model. This runs *before* pydantic, so
                these surface as a plain ``ValueError`` — a factory is not a
                constructor. Callers that accept operator input should catch
                ``(ValidationError, ValueError)``.
            ValidationError: The resolved values fail the model's own
                validation, e.g. an unusable ``bar_types`` entry.
        """
        from src.core.strategy_factory import StrategyLoader

        # Resolve once, here, and hand the canonical name to both the parameter
        # chain and the constructor. `build_strategy_params` gates on
        # `StrategyRegistry.exists()`, which lacks the hyphen/underscore-stripping
        # branch `StrategyRegistry.get()` has — so without this the two disagree
        # about which identifiers are valid, and a fuzzy id that the constructor
        # accepts is refused here.
        definition = _lookup_strategy(strategy_id)
        if definition.param_model is None:
            # `build_strategy_params` would reach `param_model_cls.model_fields`
            # and die with `AttributeError: 'NoneType' has no attribute ...`,
            # putting `_normalise_parameters`' remediation message out of reach
            # on the primary construction path.
            raise ValueError(_NO_PARAM_MODEL.format(name=definition.name))

        parameters = StrategyLoader.build_strategy_params(
            definition.name, dict(overrides), settings
        )
        # `bar_types` is passed through unconverted: `tuple(bar_types)` on a bare
        # string would split it into one entry per character before the validator
        # could report the real mistake. The before-validator normalises any
        # ordered sequence into the declared tuple and refuses everything else,
        # so the cast narrows for the type checker only.
        return cls(
            strategy_id=definition.name,
            parameters=parameters,
            bar_types=cast("tuple[str, ...]", bar_types),
        )


class SessionSpec(BaseModel):
    """An immutable, ordered collection of strategy specifications (FR13, FR14).

    Attributes:
        schema_version: Written once into JSONB and read forever; lets a
            future field addition tell old rows from new.
        strategies: One or more ``StrategySpec`` entries, in the order given.
            A ``tuple``, not a ``list`` — see the module docstring's note on
            ``frozen=True`` being shallow.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(
        default=SPEC_SCHEMA_VERSION, ge=1, description="Spec schema version"
    )
    strategies: tuple[StrategySpec, ...] = Field(
        ..., min_length=1, description="Ordered, non-empty collection of strategy specs"
    )

    @model_validator(mode="after")
    def _reject_duplicate_strategies(self) -> "SessionSpec":
        """Refuse two entries naming one strategy, at config time.

        Nautilus builds a ``StrategyId`` as ``f"{component_id}-{order_id_tag}"``
        and ``Trader.add_strategy`` raises ``RuntimeError`` on a repeat, so a
        session listing one strategy twice validates, persists, and then dies at
        node start — exactly the deferred failure this module exists to prevent.

        Two entries for one strategy with *different* parameters is a legitimate
        want; it needs a per-entry instance tag, which this phase does not model.
        Refusing now is the reversible choice: widening validation later is
        backward-compatible, narrowing it is not.
        """
        seen: set[str] = set()
        for strategy in self.strategies:
            if strategy.strategy_id in seen:
                raise ValueError(
                    f"Strategy {strategy.strategy_id!r} appears more than once. Nautilus derives "
                    "a StrategyId from the strategy's own order_id_tag, so two entries for one "
                    "strategy collide when the node starts. Distinct instance tags are not "
                    "modelled in this phase — see Story 2.5."
                )
            seen.add(strategy.strategy_id)
        return self

    @model_validator(mode="after")
    def _reject_order_id_tag_collision(self) -> "SessionSpec":
        """Refuse a create-time ``order_id_tag`` collision (Story 3.2, Task 6.4).

        Closes ``deferred-work.md:1748-1757``: measured against a real
        ``Trader``, ``Trader.add_strategy`` assigns
        ``order_id_tag = f"{len(existing):03d}"`` to any strategy whose own
        tag is unset, then raises ``RuntimeError`` if the resolved tag is
        already taken — a failure that used to surface only at the
        ``trading`` phase, for an operator's *choice of strategy order*, on a
        spec that had validated perfectly at create time.

        **Why this simulates resolution rather than refusing explicit
        duplicates only**: same-strategy duplicates are already refused by
        :meth:`_reject_duplicate_strategies` above; only
        ``MomentumParameters`` declares ``order_id_tag`` at all
        (``src/models/strategy.py:72``); and ``_normalise_parameters``
        materialises every param model's defaults via ``model_dump()``
        (module docstring), so an "explicit vs. defaulted" distinction does
        not exist at this layer — an "explicit duplicates only" validator
        could never fire against today's built-ins. The measured failure is
        the *explicit-vs-auto* collision instead, so this walks the spec in
        order exactly as ``Trader.add_strategy`` does.
        """
        taken: list[str] = []
        for strategy in self.strategies:
            resolved = _resolve_order_id_tag(strategy.parameters, len(taken))
            if resolved in taken:
                raise ValueError(
                    f"Strategy {strategy.strategy_id!r} resolves to order_id_tag {resolved!r}, "
                    "which an earlier entry already holds. Nautilus assigns positional tags "
                    "(f'{count:03d}') to any strategy whose own order_id_tag is unset, in "
                    "registration order — so this session would validate here and then raise "
                    "RuntimeError('order_id_tag conflict') when the node starts. Give the "
                    "colliding strategy an explicit, distinct order_id_tag, or reorder the "
                    "strategies."
                )
            taken.append(resolved)
        return self

    @property
    def subscription_bar_types(self) -> tuple[str, ...]:
        """Every strategy's bar types, flattened and deduplicated across strategies.

        Two strategies legitimately trading the same instrument is not a
        duplicate at the session level — only within one strategy's own
        ``bar_types`` is a repeat rejected. Deduplication is case-insensitive,
        keyed the same way ``resolve_live_bar_types`` keys its own dedup, and
        preserves first-seen order. This is what Story 2.5 hands to
        ``build_trading_node_config(bar_types=...)``.

        The **canonical** form is emitted, never the caller's casing. Appending
        the original string would let a lowercase entry win the dedup and reach
        ``load_ids`` as an id IBKR cannot resolve. ``_resolve_bar_types`` already
        stores the canonical form, so this is belt-and-braces for a spec
        deserialised from a row written before that rule existed.
        """
        ordered: list[str] = []
        seen: set[str] = set()
        for strategy in self.strategies:
            for bar_type in strategy.bar_types:
                key = bar_type.upper()
                if key not in seen:
                    seen.add(key)
                    ordered.append(key)
        return tuple(ordered)

    def to_stored(self) -> dict[str, Any]:
        """Serialise for the ``spec`` JSONB column — the one sanctioned writer.

        JSON-mode dumping and nothing else. A bare ``model_dump()`` is
        python-mode and preserves ``Decimal``, which a JSONB column cannot
        accept (``TypeError: Object of type Decimal is not JSON serializable``
        — verified against this repo's live Postgres). JSON mode stringifies
        it instead, and ``from_stored`` restores the ``Decimal`` by re-running
        ``StrategySpec``'s ``mode="before"`` validator, which re-coerces every
        parameter through the strategy's own param model.

        Returns:
            A JSON-safe dict, ready for ``json.dumps`` or a JSONB column.
        """
        return self.model_dump(mode="json")

    @classmethod
    def from_stored(cls, payload: dict[str, Any]) -> "SessionSpec":
        """Reconstruct from a stored JSONB payload, refusing an unreadable version.

        Args:
            payload: The raw dict read back from the ``spec`` column.

        Returns:
            The reconstructed, fully re-validated ``SessionSpec``.

        Raises:
            ValueError: ``payload`` is not a dict, or its ``schema_version`` is
                not an integer, or it is newer than this build understands. A
                session spec is frozen for the life of a multi-week forward
                test; reading it wrong and continuing would corrupt the sample
                undetectably, so this refuses loudly instead of guessing.
                Every one of those three refusals is a ``ValueError`` by
                design: a hand-edited JSONB row is the case this gate exists
                for, and it must never surface as a bare ``KeyError`` (hence
                ``payload.get(...)``, never ``payload[...]``) or as a bare
                ``TypeError`` (hence the ``isinstance`` check before ``>``).
            ValidationError: The payload fails ``SessionSpec``'s own
                validation once the version gate passes.
        """
        if not isinstance(payload, dict):
            raise ValueError(
                f"Expected a dict read from the spec column, got {type(payload).__name__}."
            )
        version = payload.get("schema_version", SPEC_SCHEMA_VERSION)
        # `>` against a str/None/float raises TypeError — the same illegible
        # failure the `.get(...)` default above exists to prevent, and equally
        # reachable from a hand-edited JSONB row. `bool` is excluded explicitly
        # because it is an int subclass, so `True` would otherwise sail through
        # as version 1.
        if not isinstance(version, int) or isinstance(version, bool):
            raise ValueError(
                f"Stored spec has a non-integer schema_version={version!r}. Refusing to "
                "read a payload whose version cannot be compared."
            )
        if version > SPEC_SCHEMA_VERSION:
            raise ValueError(
                f"Stored spec has schema_version={version}, but this build only "
                f"understands up to schema_version={SPEC_SCHEMA_VERSION}. Refusing to "
                "guess at a newer schema rather than silently mis-reading it."
            )
        return cls.model_validate(payload)
