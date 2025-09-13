"""Integration tests for simple backtest functionality."""

import subprocess
import sys
from pathlib import Path

import pytest


def test_can_run_simple_sma_backtest():
    """End-to-end test - MUST FAIL INITIALLY.

    Test that we can run: ntrader run-simple --strategy sma --data mock
    and verify it returns exit code 0 and shows results.
    """
    # Get project root
    project_root = Path(__file__).parent.parent
    ntrader_script = project_root / "ntrader.py"

    # Run the command
    result = subprocess.run([
        sys.executable, str(ntrader_script),
        "run-simple", "--strategy", "sma", "--data", "mock"
    ], capture_output=True, text=True, cwd=project_root)

    # Should exit successfully
    assert result.returncode == 0, f"Command failed with output: {result.stderr}"

    # Should show backtest results (this will fail initially)
    output = result.stdout.lower()
    assert "total return" in output, "Should show total return in results"
    assert "trades" in output, "Should show number of trades"
    assert "win rate" in output, "Should show win rate"

    # Should show positive return (for predictable mock data)
    assert "profit" in output or "positive" in output, "Should show profitable results"