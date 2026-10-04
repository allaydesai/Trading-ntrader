"""The data split: an in-sample window to explore and a later, locked out-of-sample window.

Pure rules, no database. Inside a study every run, bar export and benchmark
must end on or before the in-sample end; only the out-of-sample tools may reach
past it, and only once a candidate is frozen (S1.2).
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from src.mcp_server.errors import ToolFailure


@dataclass(frozen=True)
class Split:
    """In-sample and out-of-sample windows, both inclusive."""

    is_start: date
    is_end: date
    oos_start: date
    oos_end: date

    @classmethod
    def of(cls, study: Any) -> "Split":
        return cls(study.is_start, study.is_end, study.oos_start, study.oos_end)

    def to_dict(self) -> dict[str, Any]:
        return {
            "in_sample": {"start": self.is_start.isoformat(), "end": self.is_end.isoformat()},
            "out_of_sample": {
                "start": self.oos_start.isoformat(),
                "end": self.oos_end.isoformat(),
                "locked": True,
            },
        }


def check_split(split: Split) -> None:
    """Refuse a split whose windows are reversed or whose holdout does not come after."""
    if split.is_end < split.is_start or split.oos_end < split.oos_start:
        raise ToolFailure(
            "invalid_split",
            "A window ends before it starts.",
            fix="Give each window as start <= end.",
        )
    if split.oos_start <= split.is_end:
        raise ToolFailure(
            "invalid_split",
            f"The out-of-sample window ({split.oos_start}) must start after the in-sample "
            f"window ends ({split.is_end}).",
            fix="Put the out-of-sample window after the in-sample one, e.g. IS 2000-2015, "
            "OOS 2016-2025.",
        )


def require_in_sample(split: Split, start: date, end: date) -> None:
    """Refuse a window that reaches past the in-sample end (into the locked holdout)."""
    if end > split.is_end:
        raise ToolFailure(
            "holdout_locked",
            f"{start} to {end} reaches past the in-sample end ({split.is_end}); the "
            f"out-of-sample window {split.oos_start} to {split.oos_end} is locked.",
            fix=f"End on or before {split.is_end}. Out-of-sample data is reached only by "
            "run_out_of_sample, once a candidate is frozen.",
        )


def require_out_of_sample(split: Split, start: date, end: date) -> None:
    """Refuse a window that is not exactly the study's out-of-sample window."""
    if (start, end) != (split.oos_start, split.oos_end):
        raise ToolFailure(
            "invalid_window",
            f"Out-of-sample runs use the study's window {split.oos_start} to {split.oos_end}.",
            fix="Omit start and end.",
        )


def clamp_to_in_sample(
    split: Split, start: date | None, end: date | None
) -> tuple[date, date, list[str]]:
    """The part of a requested window inside the in-sample window, and what was cut."""
    start = start or split.is_start
    end = end or split.is_end
    notes = []
    if end > split.is_end:
        notes.append(
            f"Clamped the end from {end} to {split.is_end}: the out-of-sample window is locked."
        )
        end = split.is_end
    if start < split.is_start:
        notes.append(f"Clamped the start from {start} to the in-sample start {split.is_start}.")
        start = split.is_start
    if start > end:
        raise ToolFailure(
            "holdout_locked",
            f"Nothing of the requested window lies in-sample ({split.is_start} to {split.is_end}).",
            fix="Request dates inside the in-sample window.",
        )
    return start, end, notes
