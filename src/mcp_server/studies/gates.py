"""Gate thresholds, read from the vault's ``System/Gates.md`` (S6.1).

The prose in ``Gates.md`` is the human copy; the server reads only a fenced
block tagged ``yaml ntrader-gates``, mapping gate ids to named checks and their
thresholds::

    ```yaml ntrader-gates
    G0:
      min_trades: 30
    G1:
      is_profit_factor: 1.3
    ```

The server never keeps its own copy: a missing file, block or key makes the
affected scorecard rows "missing", never "pass".
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.mcp_server.settings import McpSettings

_BLOCK = re.compile(r"^```yaml[ \t]+ntrader-gates[ \t]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
_GATE_ID = re.compile(r"^G\d+$")
#: Gate ids used when the vault's block cannot be read.
FALLBACK_GATE_IDS = ("G0", "G1", "G2", "G3", "G4")


@dataclass(frozen=True)
class Gates:
    """Thresholds per gate id, or why they could not be read."""

    thresholds: dict[str, dict[str, Any]] = field(default_factory=dict)
    source: str | None = None
    problem: str | None = None

    @property
    def ids(self) -> list[str]:
        return list(self.thresholds)

    def status(self) -> dict[str, Any]:
        """For ``server_info``: where the gates came from and whether they parsed."""
        return {"source": self.source, "gate_ids": self.ids, "problem": self.problem}


def parse_gates(text: str, source: str | None = None) -> Gates:
    """Thresholds from the ``ntrader-gates`` block of a Gates.md text."""
    match = _BLOCK.search(text)
    if match is None:
        return Gates(source=source, problem="No ```yaml ntrader-gates``` block found.")
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        return Gates(source=source, problem=f"The gates block is not valid YAML: {exc}")
    if not isinstance(data, dict):
        return Gates(source=source, problem="The gates block must map gate ids to checks.")
    thresholds: dict[str, dict[str, Any]] = {}
    for gate_id, checks in data.items():
        if not _GATE_ID.match(str(gate_id)) or not isinstance(checks, dict):
            return Gates(
                source=source,
                problem=f"'{gate_id}' must be a gate id like G1 mapping check names to values.",
            )
        thresholds[str(gate_id)] = {str(k): v for k, v in checks.items()}
    return Gates(thresholds=thresholds, source=source)


def load_gates(settings: McpSettings) -> Gates:
    """The vault's gates, or a ``Gates`` whose ``problem`` says why there are none."""
    if settings.vault_path is None:
        return Gates(problem="No vault is configured (NTRADER_MCP_VAULT_PATH).")
    path = Path(settings.vault_path) / settings.gates_file
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return Gates(source=str(path), problem=f"Cannot read {path}: {exc.strerror or exc}")
    return parse_gates(text, source=str(path))
