"""The research MCP never reaches live trading or data fetching (spec: safety boundary).

Two scans, each with a planted-probe twin (repo rule since Story 2.3):

- **AST**, over every file under ``src/mcp_server`` (globbed, so a new module is
  scanned the day it appears), walking the whole tree so imports inside
  functions count, with relative imports resolved.
- **Fresh interpreter**, importing every server module plus every ``src.*``
  module they import anywhere (the worker imports lazily), then reading
  ``sys.modules``: catches what the AST scan cannot, a forbidden module pulled
  in transitively.

Not forbidden, on purpose: ``nautilus_trader.live`` (``BacktestEngine`` imports
it itself) and ``ibapi`` (``src.config`` imports one enum from it).
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
PACKAGE_DIR = ROOT / "src" / "mcp_server"
SERVER_FILES = sorted(PACKAGE_DIR.rglob("*.py"))

#: Live trading, the CLI (live commands and its stdout logging), and every path
#: that fetches data from a broker or falls back to a fake instrument.
FORBIDDEN_PACKAGES = (
    "src.cli",
    "src.services.ibkr_client",
    "src.services.kraken_client",
    "src.services.data_catalog",
    "nautilus_trader.adapters",
    "nautilus_trader.live.node",
)
FORBIDDEN_NAME_PREFIXES = ("src.core.live_",)


def forbidden(module: str) -> bool:
    return module.startswith(FORBIDDEN_NAME_PREFIXES) or any(
        module == p or module.startswith(p + ".") for p in FORBIDDEN_PACKAGES
    )


def _module_name(path: Path) -> str:
    parts = path.relative_to(ROOT).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def imported_modules(source: str, module: str, is_package: bool = False) -> set[str]:
    """Every module a source file imports, anywhere in it, relative imports resolved."""
    package = module if is_package else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)]
                name = ".".join(base + ([node.module] if node.module else []))
            else:
                name = node.module or ""
            found.add(name)
            found.update(f"{name}.{alias.name}" for alias in node.names)
    return found


def _server_imports() -> dict[str, set[str]]:
    return {
        _module_name(path): imported_modules(
            path.read_text(encoding="utf-8"), _module_name(path), path.name == "__init__.py"
        )
        for path in SERVER_FILES
    }


def test_the_glob_sees_the_package():
    names = {_module_name(p) for p in SERVER_FILES}
    assert {"src.mcp_server.server", "src.mcp_server.worker", "src.mcp_server.jobs.runner"} <= names


@pytest.mark.parametrize("path", SERVER_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_server_file_imports_a_forbidden_module(path):
    module = _module_name(path)
    imports = imported_modules(path.read_text(encoding="utf-8"), module, path.name == "__init__.py")
    assert sorted(m for m in imports if forbidden(m)) == []


def test_probe_lazy_and_relative_forbidden_imports_are_caught():
    source = (
        "def f():\n"
        "    from src.core.live_session_runner import LiveSessionRunner\n"
        "from ...cli.commands import live\n"
        "import nautilus_trader.adapters.interactive_brokers.factories\n"
    )
    imports = imported_modules(source, "src.mcp_server.tools.probe")
    assert sorted(m for m in imports if forbidden(m)) == [
        "nautilus_trader.adapters.interactive_brokers.factories",
        "src.cli.commands",
        "src.cli.commands.live",
        "src.core.live_session_runner",
        "src.core.live_session_runner.LiveSessionRunner",
    ]


def test_probe_permitted_modules_are_not_flagged():
    for module in (
        "src.core.backtest_orchestrator",
        "src.client",
        "nautilus_trader.live",
        "src.core.liveliness",
        "ibapi.common",
    ):
        assert not forbidden(module), module


def test_a_fresh_interpreter_loads_nothing_forbidden():
    """Imports every server module and every src.* module they reference, then scans."""
    targets = set(_server_imports())
    for imports in _server_imports().values():
        targets.update(m for m in imports if m.startswith("src.") and not forbidden(m))
    code = (
        "import importlib, json, sys\n"
        f"for name in {sorted(targets)!r}:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except ModuleNotFoundError:\n"
        "        pass  # 'from pkg import name' entries that are attributes, not modules\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True
    )
    loaded = json.loads(proc.stdout.strip().splitlines()[-1])
    assert "src.mcp_server.worker" in loaded and "src.core.backtest_orchestrator" in loaded
    assert sorted(m for m in loaded if forbidden(m)) == []
