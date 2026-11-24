"""Hand-curated spot crypto coin specs and the Kraken Pro fee schedule (crypto M1).

The specs live in ``coin_specs.yaml`` beside this module and follow the FX ``pair_specs``
discipline: validated on load with ``extra="forbid"``, and every failure surfaces as
:class:`CryptoSpecError` naming the file and the dotted field path
(``coins.BTC.quote: ...``). Every per-coin value and fee-schedule default carries a dated
``source`` entry whose ``status`` says whether it was checked (``VERIFIED``) or is a best-known
public figure still to be confirmed (``UNVERIFIED``). Only USD-quoted coins load (decision C6).
See ``openspec/changes/add-crypto-backtesting/design.md`` section 1.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.services.fx.pair_specs import ValueSource, decimal_places

DEFAULT_SPEC_PATH = Path(__file__).with_name("coin_specs.yaml")

SUPPORTED_SCHEMA_VERSIONS = (1,)
QUOTE_CURRENCY = "USD"

# The per-coin values that must each carry a dated source entry.
SOURCED_FIELDS = (
    "price_precision",
    "size_increment",
    "typical_spread_bps",
    "kraken_rest",
    "kraken_altname",
    "kraken_min_order_base",
    "kraken_min_order_usd",
    "kraken_tick_size",
    "kraken_discounted_pair",
)

# The fee-schedule defaults that must each carry one (the tier rates share the schedule's own).
FEE_SOURCED_FIELDS = ("default_tier", "discounted_pair_bps_off", "min_fee_usd", "fee_currency")

CoinCode = Annotated[str, Field(pattern=r"^[A-Z0-9]{2,10}$")]
Pct = Annotated[Decimal, Field(ge=0, le=100)]
UsdAmount = Annotated[Decimal, Field(ge=0)]


class CryptoSpecError(ValueError):
    """A crypto coin spec file is missing or invalid, or a coin or tier is unknown."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _source_problems(sources: dict[str, ValueSource], required: tuple[str, ...]) -> list[str]:
    problems = []
    missing = [f for f in required if f not in sources]
    unknown = sorted(set(sources) - set(required))
    if missing:
        problems.append(f"sources missing {', '.join(missing)}")
    if unknown:
        problems.append(f"sources has unknown {', '.join(unknown)}")
    return problems


class FeeTier(_Strict):
    """One Kraken Pro spot tier: maker/taker percent and its qualifying thresholds (USD)."""

    maker_pct: Pct = Field(alias="maker")
    taker_pct: Pct = Field(alias="taker")
    spot_30d_usd: UsdAmount
    futures_30d_usd: UsdAmount
    assets_usd: UsdAmount | None


class FeeSchedule(_Strict):
    """The dated Kraken Pro spot fee schedule; a run charges one fixed tier (decision C21)."""

    venue: Literal["KRAKEN"]
    product: str = Field(min_length=1)
    source: str = Field(min_length=1)
    as_of: date
    status: Literal["VERIFIED", "UNVERIFIED"]
    default_tier: int
    discounted_pair_bps_off: Decimal = Field(ge=0)
    min_fee_usd: UsdAmount
    fee_currency: Literal["USD"]
    tiers: dict[int, FeeTier]
    sources: dict[str, ValueSource]

    def tier(self, number: int) -> FeeTier:
        """The tier numbered ``number`` (1-based)."""
        found = self.tiers.get(number)
        if found is None:
            raise CryptoSpecError(
                f"Unknown Kraken fee tier {number}; tiers are 1..{len(self.tiers)}"
            )
        return found

    def unverified_fields(self) -> tuple[str, ...]:
        """The defaults whose value is still UNVERIFIED, in ``FEE_SOURCED_FIELDS`` order."""
        return tuple(f for f in FEE_SOURCED_FIELDS if self.sources[f].status == "UNVERIFIED")

    @model_validator(mode="after")
    def _consistent(self) -> FeeSchedule:
        problems = _tier_problems(self.tiers)
        if self.default_tier not in self.tiers:
            problems.append(f"default_tier {self.default_tier} is not a tier in the schedule")
        problems += _source_problems(self.sources, FEE_SOURCED_FIELDS)
        if problems:
            raise ValueError("; ".join(problems))
        return self


def _tier_problems(tiers: dict[int, FeeTier]) -> list[str]:
    expected = list(range(1, len(tiers) + 1))
    if sorted(tiers) != expected:
        return [f"tiers must be numbered 1..{len(tiers)}, got {sorted(tiers)}"]
    problems = []
    previous: FeeTier | None = None
    for number in expected:
        tier = tiers[number]
        if tier.maker_pct > tier.taker_pct:
            problems.append(f"tier {number} maker {tier.maker_pct} > taker {tier.taker_pct}")
        if previous is not None:
            for side in ("maker_pct", "taker_pct"):
                if getattr(tier, side) > getattr(previous, side):
                    problems.append(
                        f"tier {number} {side.removesuffix('_pct')} {getattr(tier, side)} "
                        f"is above tier {number - 1}'s {getattr(previous, side)}"
                    )
        previous = tier
    return problems


class KrakenPairSpec(_Strict):
    """The Kraken symbols, minimums and tick for one USD pair (live orders and sizing)."""

    pair: str = Field(min_length=3)
    rest: str = Field(min_length=1)
    altname: str = Field(min_length=1)
    min_order_base: Decimal = Field(gt=0)
    min_order_usd: Decimal = Field(gt=0)
    tick_size: Decimal = Field(gt=0)
    discounted_pair: bool


class CoinSpec(_Strict):
    """One spot coin quoted in USD, e.g. BTC."""

    coin: CoinCode
    base: CoinCode
    quote: Literal["USD"]
    data: Literal["firstrate", "none"]
    price_precision: int = Field(ge=0, le=9)
    size_precision: int = Field(ge=0, le=9)
    size_increment: Decimal = Field(gt=0)
    typical_spread_bps: Decimal = Field(ge=0)
    kraken: KrakenPairSpec
    sources: dict[str, ValueSource]

    def unverified_fields(self) -> tuple[str, ...]:
        """The fields whose value is still UNVERIFIED, in ``SOURCED_FIELDS`` order."""
        return tuple(f for f in SOURCED_FIELDS if self.sources[f].status == "UNVERIFIED")

    @model_validator(mode="after")
    def _consistent(self) -> CoinSpec:
        problems = []
        if self.base == self.quote:
            problems.append(f"base {self.base} must differ from quote {self.quote}")
        if self.coin != self.base:
            problems.append(f"key {self.coin} != base {self.base}")
        if self.kraken.pair != f"{self.base}/{self.quote}":
            problems.append(f"kraken.pair {self.kraken.pair} != {self.base}/{self.quote}")
        if decimal_places(self.size_increment) > self.size_precision:
            problems.append(
                f"size_increment {self.size_increment} needs more decimals than "
                f"size_precision {self.size_precision}"
            )
        problems += _source_problems(self.sources, SOURCED_FIELDS)
        if problems:
            raise ValueError("; ".join(problems))
        return self


def _with_coin(key: str, value: object) -> object:
    """Inject the registry key as ``coin``; an explicit, different ``coin`` is an error."""
    if not isinstance(value, dict):
        return value
    explicit = value.get("coin", key)
    if explicit != key:
        raise ValueError(f"coins.{key}.coin '{explicit}' does not match its key '{key}'")
    return {**value, "coin": key}


class CoinSpecRegistry(_Strict):
    """The whole spec file."""

    schema_version: int
    specs_as_of: date
    fee_schedule: FeeSchedule
    coins: dict[str, CoinSpec]

    @model_validator(mode="before")
    @classmethod
    def _inject_coins(cls, data: object) -> object:
        """Let the YAML key be the coin so entries don't repeat it."""
        if isinstance(data, dict) and isinstance(data.get("coins"), dict):
            data = dict(data)
            data["coins"] = {key: _with_coin(key, value) for key, value in data["coins"].items()}
        return data

    @model_validator(mode="after")
    def _known_version(self) -> CoinSpecRegistry:
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
        raise CryptoSpecError(f"Crypto coin spec file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise CryptoSpecError(f"Crypto coin spec file {path} is not valid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise CryptoSpecError(f"Crypto coin spec file {path} must be a mapping at top level")
    return data


def load_coin_specs(path: Path | None = None) -> CoinSpecRegistry:
    """Load and validate a spec file (the shipped one by default)."""
    path = Path(path) if path is not None else DEFAULT_SPEC_PATH
    data = _read_yaml(path)
    try:
        return CoinSpecRegistry.model_validate(data)
    except ValidationError as exc:
        raise CryptoSpecError(
            f"Invalid crypto coin spec file {path}:\n{_format_errors(exc)}"
        ) from None


@lru_cache(maxsize=1)
def _default_registry() -> CoinSpecRegistry:
    return load_coin_specs()


def get_coin_spec(coin: str) -> CoinSpec:
    """Return the shipped spec for ``coin`` (``BTC``, case-insensitive)."""
    registry = _default_registry()
    spec = registry.coins.get(coin.upper())
    if spec is None:
        known = ", ".join(sorted(registry.coins))
        raise CryptoSpecError(f"Unknown crypto coin '{coin}'. Known coins: {known}")
    return spec


def get_fee_schedule() -> FeeSchedule:
    """Return the shipped Kraken Pro spot fee schedule."""
    return _default_registry().fee_schedule


def coin_names() -> frozenset[str]:
    """Every coin the shipped spec file defines (``BTC``, ...)."""
    return frozenset(_default_registry().coins)
