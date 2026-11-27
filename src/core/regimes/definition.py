"""Regime calendar definitions, label payloads and hashes (design.md sections 1, 3 and 4).

A definition holds **parameters only**. Nothing derived from data lives here: the frozen
ground-truth volatility median is a version's ``frozen_stats``, so the default definition
is a constant and its hash can be pinned. A changed parameter changes the hash, and a new
hash is a new calendar version.
"""

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LabelSet = Literal["ground_truth", "detector"]
LABEL_SETS: tuple[str, ...] = ("ground_truth", "detector")

Direction = Literal["up", "down"]
VolatilityLevel = Literal["high", "low"]

#: Wave scales in the order they are reported; the label key is ``leg_<scale>``.
SCALES: tuple[str, ...] = ("minor", "intermediate", "major")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WaveScales(_Strict):
    """ATR multiples that confirm a turning point at each scale."""

    minor: float = Field(default=3.0, gt=0)
    intermediate: float = Field(default=6.0, gt=0)
    major: float = Field(default=12.0, gt=0)


class GroundTruthDefinition(_Strict):
    """Hindsight labels: wave legs per scale, ranging flag, volatility level."""

    label_set: Literal["ground_truth"] = "ground_truth"
    atr_window: int = Field(default=20, ge=2)
    scales: WaveScales = Field(default_factory=WaveScales)
    efficiency_window: int = Field(default=21, ge=3)
    ranging_threshold: float = Field(default=0.3, gt=0, lt=1)
    volatility_window: int = Field(default=21, ge=3)
    annualisation: int = Field(default=252, ge=1)

    @field_validator("efficiency_window", "volatility_window")
    @classmethod
    def _odd(cls, value: int) -> int:
        if value % 2 == 0:
            raise ValueError("a centred window needs an odd number of sessions")
        return value


class SmaRule(_Strict):
    """``up`` when the close is above the simple moving average of closes up to *t*."""

    window: int = Field(default=200, ge=2)


class DetectorDefinition(_Strict):
    """Past-data-only labels. Phase 1 has the long average only."""

    label_set: Literal["detector"] = "detector"
    warmup_sessions: int = Field(default=272, ge=0)
    sma200: SmaRule = Field(default_factory=SmaRule)


Definition = GroundTruthDefinition | DetectorDefinition


class GroundTruthLabels(_Strict):
    """One session's ground-truth payload. ``None`` means not final (yet) or undefined."""

    leg_minor: Direction | None
    leg_intermediate: Direction | None
    leg_major: Direction | None
    ranging: bool | None
    volatility: VolatilityLevel | None


class DetectorLabels(_Strict):
    """One session's detector payload (phase 1)."""

    sma200: Direction | None


PAYLOADS: dict[str, type[_Strict]] = {
    "ground_truth": GroundTruthLabels,
    "detector": DetectorLabels,
}
DEFINITIONS: dict[str, type[_Strict]] = {
    "ground_truth": GroundTruthDefinition,
    "detector": DetectorDefinition,
}


def default_definition(label_set: str) -> Definition:
    """The default definition of a label set."""
    return DEFINITIONS[label_set]()  # type: ignore[return-value]


def _merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def build_definition(label_set: str, overrides: dict[str, Any] | None = None) -> Definition:
    """The default definition with ``overrides`` deep-merged in. Unknown keys raise."""
    base = default_definition(label_set).model_dump(mode="json")
    merged = _merge(base, overrides or {})
    return DEFINITIONS[label_set].model_validate(merged)  # type: ignore[return-value]


def canonical_json(definition: Definition) -> str:
    """Sorted-key, whitespace-free JSON of a definition: the text that is hashed."""
    return json.dumps(definition.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def definition_hash(definition: Definition) -> str:
    """sha256 of the canonical definition JSON."""
    return hashlib.sha256(canonical_json(definition).encode()).hexdigest()


def bar_hash(open_: float, high: float, low: float, close: float) -> str:
    """Truncated sha256 of one session's OHLC, as read. Stable for the same prices."""
    text = "|".join(repr(float(v)) for v in (open_, high, low, close))
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def validate_labels(label_set: str, labels: dict[str, Any]) -> dict[str, Any]:
    """The payload checked against its label set's model; raises ``ValidationError``."""
    return PAYLOADS[label_set].model_validate(labels).model_dump(mode="json")
