"""Provenance recorded on every backtest run (research MCP spec, story S2.3)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RunProvenance:
    """What code and which configuration produced a run.

    Attributes:
        git_commit: HEAD of the NTrader repository, or None outside a git checkout.
        git_dirty: True when the working tree (submodules included) had uncommitted
            changes; None when git state could not be read.
        strategies_commit: HEAD of the ``src/core/strategies/custom`` submodule.
        config_hash: sha256 of the run-defining request fields
            (see ``src.services.provenance.compute_config_hash``).
    """

    git_commit: str | None = None
    git_dirty: bool | None = None
    strategies_commit: str | None = None
    config_hash: str | None = None
