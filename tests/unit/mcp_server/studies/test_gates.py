"""Gate thresholds come only from the vault's ntrader-gates block (S6.1)."""

import pytest

from src.mcp_server.settings import McpSettings
from src.mcp_server.studies.gates import load_gates, parse_gates

pytestmark = pytest.mark.unit

GATES_MD = """# Promotion gates
## G1 — Backtested
- In-sample profit factor ≥ 1.3.

```yaml ntrader-gates
G0:
  min_trades: 30
G1:
  is_profit_factor: 1.3
  positive_sub_periods: {count: 3, of: 4}
```

## Changes
"""


def test_the_block_is_parsed_and_the_prose_ignored():
    gates = parse_gates(GATES_MD, source="Gates.md")
    assert gates.problem is None
    assert gates.ids == ["G0", "G1"]
    assert gates.thresholds["G1"]["positive_sub_periods"] == {"count": 3, "of": 4}


@pytest.mark.parametrize(
    "text, problem",
    [
        ("# just prose\n- PF >= 1.3\n", "No ```yaml ntrader-gates``` block"),
        ("```yaml ntrader-gates\nG0: [unclosed\n```\n", "not valid YAML"),
        ("```yaml ntrader-gates\n- a list\n```\n", "must map gate ids"),
        ("```yaml ntrader-gates\nGate0:\n  min_trades: 30\n```\n", "gate id like G1"),
        ("```yaml ntrader-gates\nG0: 30\n```\n", "gate id like G1"),
    ],
    ids=["no-block", "bad-yaml", "not-a-map", "bad-id", "not-checks"],
)
def test_an_unreadable_block_yields_no_thresholds_and_says_why(text, problem):
    gates = parse_gates(text)
    assert gates.thresholds == {}
    assert problem in gates.problem


def test_load_reads_the_configured_vault_file(tmp_path):
    (tmp_path / "System").mkdir()
    (tmp_path / "System" / "Gates.md").write_text(GATES_MD)
    gates = load_gates(McpSettings(_env_file=None, vault_path=tmp_path))
    assert gates.ids == ["G0", "G1"]
    assert gates.source.endswith("System/Gates.md")


def test_no_vault_or_no_file_is_a_problem_not_a_crash(tmp_path):
    assert "No vault" in load_gates(McpSettings(_env_file=None)).problem
    missing = load_gates(McpSettings(_env_file=None, vault_path=tmp_path))
    assert missing.thresholds == {} and "Cannot read" in missing.problem


def test_the_gates_file_must_stay_inside_the_vault():
    with pytest.raises(ValueError, match="vault-relative"):
        McpSettings(_env_file=None, gates_file="../Gates.md")
