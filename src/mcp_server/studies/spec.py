"""What a researcher declares when opening a study, and its validation (S1.1).

The parameter space is checked against the strategy's own parameter model: every
listed value, or both ends of a range, must resolve, so a typo or an impossible
range is refused when the study is opened, not after a sweep.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.mcp_server.errors import ToolFailure
from src.mcp_server.strategies import StrategyRef, resolve_params

SLUG_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,80}$"


class ParamRange(BaseModel):
    """Either an explicit list of values or an inclusive range with an optional step."""

    model_config = ConfigDict(extra="forbid")

    values: list[Any] | None = Field(default=None, min_length=1)
    min: float | None = None
    max: float | None = None
    step: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _one_form(self) -> "ParamRange":
        is_range = self.min is not None and self.max is not None
        if (self.values is None) == (not is_range):
            raise ValueError("give either values, or min and max")
        if is_range and self.min > self.max:  # type: ignore[operator]
            raise ValueError("min must not exceed max")
        return self

    def probes(self) -> list[Any]:
        """The values that must each be valid: every listed value, or both range ends."""
        return list(self.values) if self.values is not None else [self.min, self.max]

    def contains(self, value: Any) -> bool:
        if self.values is not None:
            return value in self.values
        try:
            return self.min <= float(value) <= self.max  # type: ignore[operator]
        except (TypeError, ValueError):
            return False


class StudySpec(BaseModel):
    """The arguments of ``create_study``."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(pattern=SLUG_PATTERN, description="Matches the vault Idea, e.g. crsi-qqq")
    title: str = Field(min_length=1, max_length=255)
    hypothesis: str = Field(min_length=1)
    strategy: str = Field(min_length=1)
    symbols: list[str] = Field(min_length=1, description="The first is the primary instrument")
    in_sample_start: date
    in_sample_end: date
    out_of_sample_start: date
    out_of_sample_end: date
    timeframe: str = "1-DAY"
    catalog: str | None = None
    param_space: dict[str, ParamRange] = Field(default_factory=dict)
    pass_criteria: list[str] | None = None
    trial_budget: int = Field(default=40, gt=0)
    starting_balance: Decimal = Field(default=Decimal("1000000"), gt=0)
    tags: list[str] = Field(default_factory=list)


def build_study_spec(**fields: Any) -> StudySpec:
    """A ``StudySpec``, or a ``ToolFailure`` naming the first bad field."""
    try:
        return StudySpec(**fields)
    except ValidationError as exc:
        first = exc.errors()[0]
        name = ".".join(str(p) for p in first["loc"]) or "(study)"
        raise ToolFailure(
            "invalid_study", f"{name}: {first['msg']}", fix="Correct the study fields."
        ) from None


def check_param_space(ref: StrategyRef, space: dict[str, ParamRange]) -> None:
    """Refuse names the strategy does not take and values its parameter model rejects."""
    for name, param_range in space.items():
        for value in param_range.probes():
            try:
                resolve_params(ref, {name: value})
            except ToolFailure as failure:
                raise ToolFailure(
                    "invalid_param_space",
                    f"param_space.{name}: {value!r} is not valid for '{ref.name}'. "
                    f"{failure.message}",
                    fix=failure.fix,
                    details=failure.details,
                ) from None


def outside_space(space: dict[str, Any], params: dict[str, Any]) -> list[str]:
    """Warnings for explicitly given parameters that fall outside the declared space."""
    warnings = []
    for name, raw in space.items():
        if name in params and not ParamRange(**raw).contains(params[name]):
            warnings.append(
                f"{name}={params[name]!r} is outside the study's declared space {raw}; "
                "it still counts as a trial."
            )
    return warnings
