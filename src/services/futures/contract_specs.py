"""Hand-curated futures contract specs: models, loader and lookup (futures M1).

The specs live in ``contract_specs.yaml`` beside this module. They are validated on load
with ``extra="forbid"`` so a misspelt key is an error rather than a silently ignored value,
and every failure surfaces as :class:`FuturesSpecError` naming the file and the dotted
field path (``roots.ES.multiplier: ...``).
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DEFAULT_SPEC_PATH = Path(__file__).with_name("contract_specs.yaml")

SUPPORTED_SCHEMA_VERSIONS = (1,)

Venue = Literal["CME", "NYMEX", "COMEX", "CBOT"]
SpecAssetClass = Literal["INDEX", "COMMODITY", "DEBT", "FX"]


class FuturesSpecError(ValueError):
    """A futures spec file is missing or invalid, or a root is unknown."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionSpec(_Strict):
    """Trading session in exchange-local (US/Eastern) wall-clock time."""

    timezone: Literal["America/New_York"]
    open: time
    close: time
    days: str = Field(min_length=1)
    daily_break: str | None = None


class MarginSpec(_Strict):
    """Approximate dollar margin per contract, dated."""

    initial_usd: Decimal = Field(gt=0)
    maintenance_usd: Decimal = Field(gt=0)
    as_of: date
    note: str = Field(min_length=1)

    @model_validator(mode="after")
    def _initial_covers_maintenance(self) -> MarginSpec:
        if self.initial_usd < self.maintenance_usd:
            raise ValueError(
                f"initial_usd ({self.initial_usd}) must be >= "
                f"maintenance_usd ({self.maintenance_usd})"
            )
        return self


class IBContractSpec(_Strict):
    """The IB contract fields live trading (M5) needs to resolve a specific contract."""

    sec_type: Literal["FUT"]
    symbol: str = Field(min_length=1)
    exchange: str = Field(min_length=1)
    trading_class: str = Field(min_length=1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    multiplier: str = Field(pattern=r"^\d+(\.\d+)?$")


def _decimals(value: Decimal) -> int:
    """Number of significant decimal places (``0.10`` -> 1, ``50`` -> 0)."""
    exponent = value.normalize().as_tuple().exponent
    return max(0, -int(exponent))


class FuturesContractSpec(_Strict):
    """One futures root, e.g. ES."""

    root: str = Field(pattern=r"^[A-Z0-9]{1,6}$")
    name: str = Field(min_length=1)
    venue: Venue
    asset_class: SpecAssetClass
    underlying: str = Field(min_length=1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    multiplier: Decimal = Field(gt=0)
    tick_size: Decimal = Field(gt=0)
    price_precision: int = Field(ge=0, le=9)
    continuous_price_precision: int = Field(ge=0, le=9)
    session: SessionSpec
    margin: MarginSpec
    commission_per_contract_usd: Decimal = Field(ge=0)
    ib: IBContractSpec

    @model_validator(mode="after")
    def _consistent(self) -> FuturesContractSpec:
        problems = []
        if _decimals(self.tick_size) > self.price_precision:
            problems.append(
                f"tick_size {self.tick_size} needs more decimals than "
                f"price_precision {self.price_precision}"
            )
        if self.continuous_price_precision < self.price_precision:
            problems.append(
                f"continuous_price_precision {self.continuous_price_precision} is coarser "
                f"than price_precision {self.price_precision}"
            )
        if self.ib.exchange != self.venue:
            problems.append(f"ib.exchange {self.ib.exchange} != venue {self.venue}")
        if self.ib.currency != self.currency:
            problems.append(f"ib.currency {self.ib.currency} != currency {self.currency}")
        if Decimal(self.ib.multiplier) != self.multiplier:
            problems.append(f"ib.multiplier {self.ib.multiplier} != multiplier {self.multiplier}")
        if problems:
            raise ValueError("; ".join(problems))
        return self


class ContractSpecRegistry(_Strict):
    """The whole spec file."""

    schema_version: int
    specs_as_of: date
    roots: dict[str, FuturesContractSpec]

    @model_validator(mode="before")
    @classmethod
    def _inject_roots(cls, data: object) -> object:
        """Let the YAML key be the root so entries don't repeat it."""
        if isinstance(data, dict) and isinstance(data.get("roots"), dict):
            data = dict(data)
            data["roots"] = {
                key: {"root": key, **value} if isinstance(value, dict) else value
                for key, value in data["roots"].items()
            }
        return data

    @model_validator(mode="after")
    def _known_version(self) -> ContractSpecRegistry:
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
        raise FuturesSpecError(f"Futures contract spec file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise FuturesSpecError(f"Futures contract spec file {path} is not valid YAML: {exc}")
    if not isinstance(data, dict):
        raise FuturesSpecError(f"Futures contract spec file {path} must be a mapping at top level")
    return data


def load_contract_specs(path: Path | None = None) -> ContractSpecRegistry:
    """Load and validate a spec file (the shipped one by default)."""
    path = Path(path) if path is not None else DEFAULT_SPEC_PATH
    data = _read_yaml(path)
    try:
        return ContractSpecRegistry.model_validate(data)
    except ValidationError as exc:
        raise FuturesSpecError(
            f"Invalid futures contract spec file {path}:\n{_format_errors(exc)}"
        ) from None


@lru_cache(maxsize=1)
def _default_registry() -> ContractSpecRegistry:
    return load_contract_specs()


def get_contract_spec(root: str) -> FuturesContractSpec:
    """Return the shipped spec for ``root`` (case-insensitive)."""
    registry = _default_registry()
    spec = registry.roots.get(root.upper())
    if spec is None:
        known = ", ".join(sorted(registry.roots))
        raise FuturesSpecError(f"Unknown futures root '{root}'. Known roots: {known}")
    return spec
