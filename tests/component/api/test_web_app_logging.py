"""Importing the web app must not claim the Nautilus logging subsystem.

Why this file exists — the failure it locks out:

``src/api/web.py`` used to call Nautilus ``init_logging()`` as a *module-import-time*
side effect. Under ``pytest -n auto --forked`` that is fatal to unrelated tests:
module imports happen once in the persistent xdist **worker** during collection,
before any per-test fork, and ``--forked`` isolates only the test *call*, not
collection. Every later forked child in a worker that had collected any
``from src.api.web import app`` test therefore inherited a process image where
``is_logging_initialized()`` was already ``True`` — but Nautilus's logging
subsystem depends on native background threads, and ``fork()`` carries over only
the calling thread. Any test that then built a real ``BacktestEngine`` or
``TradingNode`` crashed with ``SIGTRAP`` (signal 5).

That made the whole integration tier nondeterministic: which tests crashed
depended on how xdist happened to distribute modules across workers, so the
failing set changed run to run — 23 crashes in one run, 0 in the next, with no
code change in between.

The guarantee restored here: importing the app is inert. Nautilus logging is
claimed when the application actually *starts* (its lifespan), which is the real
server process — never a test collector.
"""

import subprocess
import sys

import pytest

# Run in a genuinely fresh interpreter. Asserting this in-process would be
# meaningless: by the time this module runs, some earlier test in the same worker
# may already have initialised logging for entirely legitimate reasons.
_PROBE = """
import sys
from nautilus_trader.common.component import is_logging_initialized

assert not is_logging_initialized(), "logging was already initialized before the import"

import src.api.web  # noqa: F401  # the import under test

print("INITIALIZED" if is_logging_initialized() else "INERT")
"""


@pytest.mark.component
class TestWebAppImportIsInert:
    """Importing ``src.api.web`` must leave Nautilus logging unclaimed."""

    def test_importing_the_app_does_not_initialize_nautilus_logging(self):
        """The import-time side effect that made the integration tier flaky is gone."""
        result = subprocess.run(
            [sys.executable, "-c", _PROBE],
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0, (
            f"probe failed (rc={result.returncode})\nstdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
        assert "INERT" in result.stdout, (
            "Importing src.api.web initialized the Nautilus logging subsystem. Under "
            "pytest -n auto --forked this crashes every later BacktestEngine/TradingNode "
            "test in the same worker with SIGTRAP. Initialize logging in the app's "
            f"lifespan instead.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_the_probe_can_actually_detect_initialization(self):
        """Meta-test: the probe reports INITIALIZED when something really claims logging.

        Without this, a probe that silently stopped detecting initialization would
        make the test above pass vacuously forever.
        """
        impure = _PROBE.replace(
            "import src.api.web  # noqa: F401  # the import under test",
            "from nautilus_trader.common.component import init_logging\n_guard = init_logging()",
        )

        result = subprocess.run(
            [sys.executable, "-c", impure],
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0, (
            f"meta-probe failed (rc={result.returncode})\nstderr:\n{result.stderr}"
        )
        assert "INITIALIZED" in result.stdout, (
            "The probe failed to notice a real init_logging() call, so the test above "
            f"proves nothing.\nstdout:\n{result.stdout}"
        )
