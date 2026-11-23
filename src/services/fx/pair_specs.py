"""Hand-curated spot FX pair specs: models, loader and lookup (FX M1).

The specs live in ``pair_specs.yaml`` beside this module and follow the futures
``contract_specs`` discipline: validated on load with ``extra="forbid"``, and every failure
surfaces as :class:`FxSpecError` naming the file and the dotted field path
(``pairs.EURUSD.margin_pct: ...``). Every economic value carries a dated ``source`` entry
whose ``status`` says whether it was checked (``VERIFIED``) or is a best-known public figure
still to be confirmed against the IBKR account (``UNVERIFIED``).
See ``openspec/changes/add-fx-backtesting/design.md`` section 1.
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DEFAULT_SPEC_PATH = Path(__file__).with_name("pair_specs.yaml")

SUPPORTED_SCHEMA_VERSIONS = (1,)

# The per-pair values that must each carry a dated source entry.
SOURCED_FIELDS = (
    "pip_size",
    "price_precision",
    "min_tick",
    "size_increment",
    "min_order_usd",
    "typical_spread_pips",
    "margin_pct",
    "maint_margin_pct",
    "session",
)

CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Status = Literal["VERIFIED", "UNVERIFIED"]


class FxSpecError(ValueError):
    """An FX pair spec file is missing or invalid, or a pair is unknown."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ValueSource(_Strict):
    """Where one value came from, when, and whether it was checked."""

    status: Status
    source: str = Field(min_length=1)
    as_of: date


class FxSessionSpec(_Strict):
    """The FX trading week in New York wall-clock time (Sun 17:00 to Fri 17:00)."""

    tz: Literal["America/New_York"]
    open: time
    close: time
    days: str = Field(min_length=1)


class IBCashSpec(_Strict):
    """The IB contract fields for an IDEALPRO spot pair (live trading needs them)."""

    sec_type: Literal["CASH"]
    symbol: CurrencyCode
    currency: CurrencyCode
    exchange: str = Field(min_length=1)
    local_symbol: str = Field(min_length=1)


class FxCommissionSpec(_Strict):
    """One IB spot FX commission schedule, charged in USD (not per pair)."""

    rate_bps: Decimal = Field(ge=0)
    min_per_order_usd: Decimal = Field(ge=0)
    as_of: date
    status: Status
    source: str = Field(min_length=1)


def decimal_places(value: Decimal) -> int:
    """Number of significant decimal places (``0.00005`` -> 5, ``1`` -> 0)."""
    exponent = value.normalize().as_tuple().exponent
    return max(0, -int(exponent))


class FxPairSpec(_Strict):
    """One spot pair, e.g. EURUSD."""

    pair: str = Field(pattern=r"^[A-Z]{6}$")
    base: CurrencyCode
    quote: CurrencyCode
    venue: Literal["IDEALPRO"]
    data: Literal["firstrate", "none"]
    pip_size: Decimal = Field(gt=0)
    price_precision: int = Field(ge=0, le=9)
    min_tick: Decimal = Field(gt=0)
    size_increment: Decimal = Field(gt=0)
    min_order_usd: Decimal = Field(ge=0)
    typical_spread_pips: Decimal = Field(ge=0)
    margin_pct: Decimal = Field(gt=0, le=100)
    maint_margin_pct: Decimal = Field(gt=0, le=100)
    margin_as_of: date
    session: FxSessionSpec
    ib: IBCashSpec
    sources: dict[str, ValueSource]

    def unverified_fields(self) -> tuple[str, ...]:
        """The fields whose value is still UNVERIFIED, in ``SOURCED_FIELDS`` order."""
        return tuple(f for f in SOURCED_FIELDS if self.sources[f].status == "UNVERIFIED")

    @model_validator(mode="after")
    def _consistent(self) -> FxPairSpec:
        problems = _identity_problems(self) + _precision_problems(self) + _ib_problems(self)
        if self.maint_margin_pct > self.margin_pct:
            problems.append(
                f"maint_margin_pct {self.maint_margin_pct} must be <= margin_pct {self.margin_pct}"
            )
        problems += _source_problems(self.sources)
        if problems:
            raise ValueError("; ".join(problems))
        return self


def _identity_problems(spec: FxPairSpec) -> list[str]:
    problems = []
    if spec.base == spec.quote:
        problems.append(f"base {spec.base} must differ from quote {spec.quote}")
    if spec.pair != spec.base + spec.quote:
        problems.append(f"key {spec.pair} != base {spec.base} + quote {spec.quote}")
    return problems


def _precision_problems(spec: FxPairSpec) -> list[str]:
    return [
        f"{name} {value} needs more decimals than price_precision {spec.price_precision}"
        for name, value in (("pip_size", spec.pip_size), ("min_tick", spec.min_tick))
        if decimal_places(value) > spec.price_precision
    ]


def _ib_problems(spec: FxPairSpec) -> list[str]:
    expected = {
        "symbol": spec.base,
        "currency": spec.quote,
        "exchange": spec.venue,
        "local_symbol": f"{spec.base}.{spec.quote}",
    }
    return [
        f"ib.{name} {getattr(spec.ib, name)} != {want}"
        for name, want in expected.items()
        if getattr(spec.ib, name) != want
    ]


def _source_problems(sources: dict[str, ValueSource]) -> list[str]:
    problems = []
    missing = [f for f in SOURCED_FIELDS if f not in sources]
    unknown = sorted(set(sources) - set(SOURCED_FIELDS))
    if missing:
        problems.append(f"sources missing {', '.join(missing)}")
    if unknown:
        problems.append(f"sources has unknown {', '.join(unknown)}")
    return problems


def _with_pair(key: str, value: object) -> object:
    """Inject the registry key as ``pair``; an explicit, different ``pair`` is an error."""
    if not isinstance(value, dict):
        return value
    explicit = value.get("pair", key)
    if explicit != key:
        raise ValueError(f"pairs.{key}.pair '{explicit}' does not match its key '{key}'")
    return {**value, "pair": key}


class PairSpecRegistry(_Strict):
    """The whole spec file."""

    schema_version: int
    specs_as_of: date
    commission: FxCommissionSpec
    pairs: dict[str, FxPairSpec]

    @model_validator(mode="before")
    @classmethod
    def _inject_pairs(cls, data: object) -> object:
        """Let the YAML key be the pair so entries don't repeat it."""
        if isinstance(data, dict) and isinstance(data.get("pairs"), dict):
            data = dict(data)
            data["pairs"] = {key: _with_pair(key, value) for key, value in data["pairs"].items()}
        return data

    @model_validator(mode="after")
    def _known_version(self) -> PairSpecRegistry:
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"schema_version {self.schema_version} is not supported "
                f"(supported: {SUPPORTED_SCHEMA_VERSIONS})"
            )
        return self


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err["loc"]) or "<root>"
        lines.append(f"  {loc}: {err['msg']}")
    return "\n".join(lines)


def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        raise FxSpecError(f"FX pair spec file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise FxSpecError(f"FX pair spec file {path} is not valid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise FxSpecError(f"FX pair spec file {path} must be a mapping at top level")
    return data


def load_pair_specs(path: Path | None = None) -> PairSpecRegistry:
    """Load and validate a spec file (the shipped one by default)."""
    path = Path(path) if path is not None else DEFAULT_SPEC_PATH
    data = _read_yaml(path)
    try:
        return PairSpecRegistry.model_validate(data)
    except ValidationError as exc:
        raise FxSpecError(f"Invalid FX pair spec file {path}:\n{_format_errors(exc)}") from None


@lru_cache(maxsize=1)
def _default_registry() -> PairSpecRegistry:
    return load_pair_specs()


def get_pair_spec(pair: str) -> FxPairSpec:
    """Return the shipped spec for ``pair`` (``EURUSD``, case-insensitive)."""
    registry = _default_registry()
    spec = registry.pairs.get(pair.upper())
    if spec is None:
        known = ", ".join(sorted(registry.pairs))
        raise FxSpecError(f"Unknown FX pair '{pair}'. Known pairs: {known}")
    return spec


def get_commission_spec() -> FxCommissionSpec:
    """Return the shipped IB spot FX commission schedule."""
    return _default_registry().commission


def pair_names() -> frozenset[str]:
    """Every pair the shipped spec file defines (``EURUSD``, ...)."""
    return frozenset(_default_registry().pairs)
