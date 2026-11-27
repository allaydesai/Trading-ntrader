"""Ground-truth regime labels never reach a trading path (design.md section 9, tasks.md 1.10).

An AST scan of ``src/**`` (and the strategies submodule when it is checked out) finds
every module that imports ``src.core.regimes.ground_truth`` (absolutely, relatively or by
its dotted name as a string) or calls the repository's ``ground_truth_labels`` reader.
Only the **named** files below may. There are no globs: a new reader is a deliberate edit
to this list. The filter-test modules are named as forbidden, so they can never be added.
"""

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE = "src.core.regimes.ground_truth"
READER = "ground_truth_labels"
#: Ways past the named reader: the repository's untyped reader, the label model and the
#: table name in raw SQL. Only the model and repository files may use them.
RAW_ACCESS = ("_read_labels", "RegimeLabel", "regime_labels")
#: The table right after a keyword, or later in a comma-separated FROM list
#: (``FROM foo f, regime_labels l``; the list ends at the next clause keyword), optionally
#: ``ONLY`` or ``ONLY (...)``, schema-qualified and quoted.
_FROM_LIST_END = r"\b(?:where|group|order|having|limit|offset|union|intersect|except|returning)\b"
RAW_SQL = re.compile(
    r"(?:\b(?:from|join|into|update|table)\s+"
    rf"|\bfrom\b(?:(?!{_FROM_LIST_END})[^;])*?,\s*)"
    r"(?:only\s*\(?\s*)?"
    r'(?:(?:"[^"]+"|\w+)\s*\.\s*)?'
    r'(?:"regime_labels"|regime_labels\b)',
    re.IGNORECASE,
)
RAW_OWNERS = frozenset(
    {
        "src/db/models/regime.py",
        "src/db/models/__init__.py",  # re-export only
        "src/db/repositories/regime_repository_sync.py",
    }
)

#: The only files that may import the ground-truth module or read ground-truth rows.
PERMITTED = frozenset(
    {
        "src/core/regimes/attribution.py",  # phase 2: profiles (stamped hindsight)
        "src/core/regimes/scorecard.py",  # phase 3: grades detectors against it
        "src/mcp_server/analysis/regime_calendar.py",  # build and read
        "src/mcp_server/analysis/regime_reports.py",  # phase 2: stored reports
    }
)
#: Where the module and the reader are defined: not callers.
DEFINERS = frozenset(
    {
        "src/core/regimes/ground_truth.py",
        "src/db/repositories/regime_repository_sync.py",
    }
)
#: Never permitted, whatever the list above says (spec hard rule: filters take detectors).
FORBIDDEN = frozenset(
    {
        "src/core/regimes/filter_test.py",
        "src/mcp_server/analysis/regime_filter.py",
    }
)


def _module_name(root: Path, path: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _resolve(root: Path, path: Path, node: ast.ImportFrom) -> str:
    """The absolute module an ``ImportFrom`` names, resolving ``.`` and ``..``."""
    if node.level == 0:
        return node.module or ""
    package = _module_name(root, path).split(".")
    if path.name != "__init__.py":
        package = package[:-1]
    base = package[: len(package) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def _uses_ground_truth(root: Path, path: Path) -> list[str]:
    """How ``path`` reaches ground truth: each import or reader call, or ``[]``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits += [a.name for a in node.names if a.name == MODULE or a.name.startswith(MODULE)]
        elif isinstance(node, ast.ImportFrom):
            target = _resolve(root, path, node)
            if target == MODULE or target.startswith(MODULE + "."):
                hits.append(f"from {target}")
            hits += [f"{target}.{a.name}" for a in node.names if f"{target}.{a.name}" == MODULE]
        elif isinstance(node, ast.Attribute) and node.attr == READER:
            hits.append(f".{READER}")
        elif isinstance(node, ast.Constant) and node.value == MODULE:
            hits.append(f"'{MODULE}'")
    return hits


def _raw_access(path: Path) -> list[str]:
    """Uses of the label table that bypass the named readers."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits = []
    for node in ast.walk(tree):
        name = (
            node.attr if isinstance(node, ast.Attribute)
            else node.id if isinstance(node, ast.Name)
            else None
        )  # fmt: skip
        if name in RAW_ACCESS[:2]:
            hits.append(name)
        elif isinstance(node, ast.alias) and node.name == "RegimeLabel":
            hits.append("import RegimeLabel")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if RAW_SQL.search(node.value):
                hits.append("'regime_labels' in SQL")
    return hits


def scan(root: Path) -> dict[str, list[str]]:
    """Every file under ``root/src`` that reaches ground truth or the raw label table."""
    found = {}
    for path in sorted((root / "src").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        hits = _uses_ground_truth(root, path)
        if relative not in RAW_OWNERS:
            hits += _raw_access(path)
        if hits:
            found[path.relative_to(root).as_posix()] = hits
    return found


def violations(root: Path) -> dict[str, list[str]]:
    """Files that reach ground truth without being permitted (definers excluded)."""
    allowed = (PERMITTED - FORBIDDEN) | DEFINERS
    return {f: h for f, h in scan(root).items() if f not in allowed}


def test_only_named_files_reach_ground_truth():
    assert violations(REPO_ROOT) == {}


def test_the_scan_sees_the_real_reader():
    """Not vacuous: the calendar glue imports the module and calls the reader."""
    hits = scan(REPO_ROOT)["src/mcp_server/analysis/regime_calendar.py"]
    assert f"from {MODULE}" in hits and f".{READER}" in hits


def test_strategies_submodule_is_scanned_when_present():
    custom = REPO_ROOT / "src" / "core" / "strategies" / "custom"
    if not any(custom.rglob("*.py")):
        pytest.skip("strategies submodule not checked out")
    assert not [f for f in violations(REPO_ROOT) if f.startswith("src/core/strategies/custom/")]


def test_filter_modules_are_forbidden_not_permitted():
    assert FORBIDDEN.isdisjoint(PERMITTED)
    assert all("*" not in f for f in PERMITTED | FORBIDDEN | DEFINERS)


@pytest.mark.parametrize(
    ("relative", "source"),
    [
        ("src/core/strategies/sneaky.py", "from src.core.regimes.ground_truth import atr\n"),
        ("src/core/regimes/sneaky.py", "from . import ground_truth\n"),
        ("src/core/regimes/sub/sneaky.py", "from ..ground_truth import leg_labels\n"),
        ("src/mcp_server/sneaky.py", "import src.core.regimes.ground_truth as gt\n"),
        ("src/mcp_server/sneaky.py", "from src.core.regimes import ground_truth\n"),
        ("src/services/sneaky.py", "def f(repo, v):\n    return repo.ground_truth_labels(v)\n"),
        (
            "src/services/sneaky.py",
            "import importlib\nimportlib.import_module('src.core.regimes.ground_truth')\n",
        ),  # fmt: skip
        ("src/core/regimes/filter_test.py", "from .ground_truth import atr\n"),
        ("src/mcp_server/sneaky.py", "def f(repo, v):\n    return repo._read_labels(v)\n"),
        ("src/mcp_server/sneaky.py", "from src.db.models.regime import RegimeLabel\n"),
        ("src/mcp_server/sneaky.py", "Q = 'SELECT labels FROM regime_labels'\n"),
        ("src/mcp_server/sneaky.py", "Q = 'SELECT labels FROM public.regime_labels'\n"),
        ("src/mcp_server/sneaky.py", 'Q = \'SELECT labels FROM "public"."regime_labels"\'\n'),
        ("src/mcp_server/sneaky.py", "Q = 'SELECT labels FROM foo, regime_labels'\n"),
        ("src/mcp_server/sneaky.py", "Q = 'SELECT labels FROM ONLY regime_labels'\n"),
        ("src/mcp_server/sneaky.py", "Q = 'SELECT labels FROM foo, ONLY (regime_labels)'\n"),
        ("src/mcp_server/sneaky.py", "Q = 'SELECT labels FROM \"my-schema\".regime_labels'\n"),
        ("src/mcp_server/sneaky.py", "Q = 'SELECT 1 FROM foo f, ONLY public.regime_labels l'\n"),
        ("src/mcp_server/sneaky.py", "Q = 'SELECT l.labels FROM foo f, public.regime_labels l'\n"),
        ("src/mcp_server/sneaky.py", "Q = 'SELECT 1 FROM foo AS f,\\n  \"regime_labels\" AS l'\n"),
    ],
)
def test_planted_modules_are_caught(tmp_path, relative, source):
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    (tmp_path / "src" / "core" / "regimes" / "ground_truth.py").parent.mkdir(
        parents=True, exist_ok=True
    )
    assert list(violations(tmp_path)) == [relative]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT a, regime_labels FROM foo",  # a column named like the table, before FROM
        "SELECT * FROM foo, regime_labels_archive",  # another table, same prefix
        "SELECT * FROM foo ORDER BY id, regime_labels",  # a column in a later clause
        'SELECT * FROM "regime_labels-old"',  # another quoted table, same prefix
    ],
)
def test_sql_that_does_not_read_the_table_is_not_flagged(sql):
    assert not RAW_SQL.search(sql)


def test_planted_permitted_file_is_allowed(tmp_path):
    path = tmp_path / "src/core/regimes/attribution.py"
    path.parent.mkdir(parents=True)
    path.write_text("from .ground_truth import atr\n", encoding="utf-8")
    assert violations(tmp_path) == {}
