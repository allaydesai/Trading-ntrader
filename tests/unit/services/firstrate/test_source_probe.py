"""Unit tests for :mod:`src.services.firstrate.source_probe`.

The probe MUST stay pure-Python with no ``nautilus_trader`` imports; the
subprocess isolation test at the bottom enforces that invariant at runtime
(AST walking alone would miss transitive re-exports).
"""

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.services.firstrate.source_probe import compute_source_last_date


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.unit
class TestComputeSourceLastDate:
    """Edge-case coverage for :func:`compute_source_last_date`."""

    def test_daily_format_returns_max_date_at_midnight_utc(self, tmp_path: Path) -> None:
        body = (
            "2025-01-01,100.0,101.0,99.0,100.5,1000\n"
            "2025-01-02,100.5,102.0,100.0,101.5,1100\n"
            "2025-01-03,101.5,103.0,101.0,102.5,1200\n"
        )
        file_path = _write(tmp_path / "A" / "SPY_full_1day_adjsplitdiv.txt", body)

        result = compute_source_last_date(file_path)

        # 2025-01-03 00:00 EST = 05:00 UTC (FirstRate timestamps are ET, not UTC).
        assert result == datetime(2025, 1, 3, 5, 0, 0, tzinfo=timezone.utc)

    def test_intraday_format_returns_max_second(self, tmp_path: Path) -> None:
        body = (
            "2025-01-01 09:30:00,100.0,101.0,99.0,100.5,1000\n"
            "2025-01-01 09:31:00,100.5,102.0,100.0,101.5,1100\n"
            "2025-01-01 09:32:30,101.5,103.0,101.0,102.5,1200\n"
        )
        file_path = _write(tmp_path / "A" / "SPY_full_1min_adjsplitdiv.txt", body)

        result = compute_source_last_date(file_path)

        # 2025-01-01 09:32:30 EST = 14:32:30 UTC.
        assert result == datetime(2025, 1, 1, 14, 32, 30, tzinfo=timezone.utc)

    def test_unsorted_rows_still_returns_true_max(self, tmp_path: Path) -> None:
        body = (
            "2025-01-03,101.5,103.0,101.0,102.5,1200\n"
            "2025-01-01,100.0,101.0,99.0,100.5,1000\n"
            "2025-01-02,100.5,102.0,100.0,101.5,1100\n"
        )
        file_path = _write(tmp_path / "A" / "SPY_full_1day_adjsplitdiv.txt", body)

        result = compute_source_last_date(file_path)

        # 2025-01-03 00:00 EST = 05:00 UTC.
        assert result == datetime(2025, 1, 3, 5, 0, 0, tzinfo=timezone.utc)

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        file_path = _write(tmp_path / "A" / "SPY_full_1day_adjsplitdiv.txt", "")

        assert compute_source_last_date(file_path) is None

    def test_blank_lines_and_crlf_tolerated(self, tmp_path: Path) -> None:
        body = (
            "\r\n"
            "2025-01-01,100.0,101.0,99.0,100.5,1000\r\n"
            "\r\n"
            "2025-01-02,100.5,102.0,100.0,101.5,1100\r\n"
        )
        file_path = _write(tmp_path / "A" / "SPY_full_1day_adjsplitdiv.txt", body)

        result = compute_source_last_date(file_path)

        # 2025-01-02 00:00 EST = 05:00 UTC.
        assert result == datetime(2025, 1, 2, 5, 0, 0, tzinfo=timezone.utc)

    def test_fewer_than_six_columns_returns_none(self, tmp_path: Path) -> None:
        body = "not,a,firstrate,file\nstill,not,it\n"
        file_path = _write(tmp_path / "A" / "SPY_full_1day_adjsplitdiv.txt", body)

        assert compute_source_last_date(file_path) is None

    def test_malformed_date_lines_are_skipped(self, tmp_path: Path) -> None:
        body = "garbage,100.0,101.0,99.0,100.5,1000\n2025-01-02,100.5,102.0,100.0,101.5,1100\n"
        file_path = _write(tmp_path / "A" / "SPY_full_1day_adjsplitdiv.txt", body)

        result = compute_source_last_date(file_path)

        # 2025-01-02 00:00 EST = 05:00 UTC.
        assert result == datetime(2025, 1, 2, 5, 0, 0, tzinfo=timezone.utc)

    def test_unreadable_file_returns_none(self, tmp_path: Path) -> None:
        file_path = _write(
            tmp_path / "A" / "SPY_full_1day_adjsplitdiv.txt",
            "2025-01-01,100.0,101.0,99.0,100.5,1000\n",
        )
        # Strip read permission; best-effort — skip if the harness cannot chmod
        # (e.g., running as root).
        try:
            os.chmod(file_path, 0o000)
            if os.access(file_path, os.R_OK):
                pytest.skip("Cannot make file unreadable in this environment")

            assert compute_source_last_date(file_path) is None
        finally:
            os.chmod(file_path, 0o644)

    def test_nonexistent_file_returns_none(self, tmp_path: Path) -> None:
        assert compute_source_last_date(tmp_path / "does_not_exist.txt") is None


@pytest.mark.unit
class TestSourceProbeImportIsolation:
    """Ensure the probe module stays free of ``nautilus_trader`` imports.

    A subprocess check is the only robust way: AST walking misses
    transitive imports, and ``sys.modules`` in the current process is
    polluted by other tests.
    """

    def test_module_does_not_import_nautilus(self) -> None:
        script = (
            "import sys\n"
            "import src.services.firstrate.source_probe  # noqa: F401\n"
            "assert 'nautilus_trader' not in sys.modules, (\n"
            "    'source_probe pulled nautilus_trader into sys.modules: '\n"
            "    + ', '.join(m for m in sys.modules if m.startswith('nautilus_trader'))\n"
            ")\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(Path(__file__).resolve().parents[4]),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"subprocess failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
