"""Epic 1 added no dependency — Story 1.3's last acceptance criterion (AR3).

The criterion is phrased as "``pyproject.toml`` and ``uv.lock`` are unchanged",
because the IB live adapter ships inside the ``nautilus-trader`` distribution the
project already depends on. Asserting *file* immutability against git would only
restate history and would go red on the first unrelated dependency bump, so what
is asserted here is the property that phrasing exists to protect: **every
third-party module the Epic-1 live code imports resolves to a distribution the
project already declares.** Adding ``import redis`` to a live module fails this
test until the dependency is declared, which is exactly the event the AC forbids
doing silently.

Component tier, and deliberately import-free with respect to the modules under
test: they are read and parsed as source (``ast``), never executed. That keeps
the check honest — an import-time side effect cannot hide from a parser — and
keeps this file safe for the shared, non-forked component worker.
"""

import ast
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, packages_distributions, requires, version
from pathlib import Path

import pytest
from nautilus_trader.common.component import is_logging_initialized

pytestmark = pytest.mark.component

REPO_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = REPO_ROOT / "pyproject.toml"

#: The modules Epic 1 added or owns. Globbed rather than listed so a new
#: ``src/core/live_*.py`` is covered the day it appears, not the day someone
#: remembers to add it here. ``src/cli/commands/live*.py`` (Story 2.8) widens
#: the CLI half beyond the single ``live.py`` file it used to name — closing a
#: disclosed gap that already left ``live_start.py`` unswept since Story 2.6's
#: split, and covering the new ``live_status.py`` from day one.
LIVE_MODULE_GLOBS = ("src/core/live_*.py", "src/cli/commands/live*.py")

#: Nautilus is pinned by AR3's reasoning: the IB adapter and the Redis cache
#: backend ship *inside* this distribution, which is why no adapter dependency
#: had to be added. If the installed version moves, that reasoning needs
#: rechecking rather than silently inheriting.
EXPECTED_NAUTILUS_VERSION = "1.220.0"


@pytest.fixture(autouse=True)
def _assert_c_logging_state_is_unchanged():
    """Mirror the sibling live suites: this file must not touch C logging."""
    before = is_logging_initialized()
    yield
    assert is_logging_initialized() == before, (
        "this component test changed the Nautilus C logging state "
        f"({before} -> {is_logging_initialized()}) — parsing source must never execute it"
    )


def _normalize(name: str) -> str:
    """PEP 503 name normalization, so ``PyYAML`` and ``pyyaml`` compare equal."""
    return name.lower().replace("_", "-").replace(".", "-")


def _requirement_name(requirement: str) -> str:
    """The bare distribution name from a requirement string, without extras/specifiers."""
    name = requirement.split(";")[0].strip()
    for boundary in ("[", "(", ">", "<", "=", "!", "~", " "):
        name = name.split(boundary)[0]
    return _normalize(name)


def _declared_requirements() -> dict[str, set[str]]:
    """Distributions declared in ``[project].dependencies``, mapped to their extras.

    ``nautilus-trader[ib]>=1.190.0`` becomes ``{"nautilus-trader": {"ib"}}``.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    declared: dict[str, set[str]] = {}
    for requirement in data["project"]["dependencies"]:
        head = requirement.split(";")[0].strip()
        extras: set[str] = set()
        if "[" in head:
            extras = {part.strip() for part in head.split("[", 1)[1].split("]", 1)[0].split(",")}
        declared.setdefault(_requirement_name(head), set()).update(extras)
    return declared


def _declared_distributions() -> set[str]:
    return set(_declared_requirements())


def _allowed_distributions() -> set[str]:
    """Declared distributions, plus what their *declared extras* bring with them.

    ``ibapi`` is the case this exists for: it is provided by ``nautilus-ibapi``,
    which the already-declared ``nautilus-trader[ib]`` extra requires. Importing
    it adds no dependency, so treating it as one would make this test fail on
    code that satisfies the AC. Resolved one level deep — enough for an extra's
    own requirements, and short of re-implementing a resolver over the whole
    transitive graph, which would dilute the check into "is it installed".
    """
    declared = _declared_requirements()
    allowed = set(declared)
    for distribution, extras in declared.items():
        if not extras:
            continue
        for requirement in _extra_requirements(distribution, extras):
            allowed.add(requirement)
    return allowed


def _extra_requirements(distribution: str, extras: set[str]) -> set[str]:
    """Names required by ``distribution`` only when one of ``extras`` is requested."""
    try:
        requirements = requires(distribution) or []
    except PackageNotFoundError:  # pragma: no cover - declared but not installed
        return set()

    gated: set[str] = set()
    for requirement in requirements:
        _, separator, marker = requirement.partition(";")
        if not separator:
            continue
        if any(
            f'extra == "{extra}"' in marker or f"extra == '{extra}'" in marker for extra in extras
        ):
            gated.add(_requirement_name(requirement))
    return gated


def _live_modules() -> list[Path]:
    paths: list[Path] = []
    for pattern in LIVE_MODULE_GLOBS:
        paths.extend(sorted(REPO_ROOT.glob(pattern)))
    return paths


def _imported_roots(source: str) -> set[str]:
    """Every absolute top-level module name imported by ``source``.

    Walks the whole tree, not just module level: a function-local import is still
    a dependency. Relative imports (``level > 0``) are first-party by definition
    and carry no distribution.
    """
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _third_party_roots(roots: set[str]) -> set[str]:
    return {
        root
        for root in roots
        if root != "src" and root not in sys.stdlib_module_names and not root.startswith("_")
    }


class TestNoDependencyWasAdded:
    """AC #7 — the epic introduced no new distribution."""

    def test_the_module_set_under_test_is_not_empty(self):
        """Guard the guard: a glob that stopped matching would pass everything."""
        modules = _live_modules()

        assert len(modules) >= 8, f"expected the Epic-1 live modules, found {modules}"
        assert (REPO_ROOT / "src/core/live_gate.py") in modules
        assert (REPO_ROOT / "src/cli/commands/live.py") in modules

    def test_every_third_party_import_is_already_declared(self):
        allowed = _allowed_distributions()
        distributions = packages_distributions()

        undeclared: dict[str, set[str]] = {}
        seen_third_party = False
        for path in _live_modules():
            roots = _third_party_roots(_imported_roots(path.read_text(encoding="utf-8")))
            seen_third_party = seen_third_party or bool(roots)
            for root in roots:
                names = {_normalize(name) for name in distributions.get(root, [])}
                if not names or names.isdisjoint(allowed):
                    undeclared.setdefault(path.name, set()).add(root)

        assert seen_third_party, (
            "no third-party import was found in any live module — the classifier is "
            "broken and this test proves nothing"
        )
        assert not undeclared, (
            "Epic 1 must add no dependency (Story 1.3 AC #7, AR3), but these live "
            f"modules import distributions not declared in pyproject: {undeclared}"
        )

    def test_the_check_would_catch_an_undeclared_import(self):
        """Meta-test: feed the classifier an import nothing declares."""
        allowed = _allowed_distributions()
        distributions = packages_distributions()

        roots = _third_party_roots(_imported_roots("import redis\nimport src.core.live_gate\n"))

        assert roots == {"redis"}, "the classifier mis-sorted a plainly third-party import"
        names = {_normalize(name) for name in distributions.get("redis", [])}
        assert not names or names.isdisjoint(allowed), (
            "`redis` is now reachable from a declared dependency, so it no longer stands "
            "in for an undeclared one — pick another module for this meta-test"
        )

    def test_ibapi_is_allowed_only_because_a_declared_extra_supplies_it(self):
        """The real case the extras resolution exists for (``live_market_data.py``).

        ``ibapi`` is provided by ``nautilus-ibapi``, which nothing in pyproject
        names directly — it arrives through the already-declared
        ``nautilus-trader[ib]`` extra. Pinned as its own test so that a future
        simplification of ``_allowed_distributions`` cannot quietly turn this
        into either a false failure or a blanket "anything installed passes".
        """
        assert packages_distributions().get("ibapi") == ["nautilus-ibapi"]
        assert "nautilus-ibapi" not in _declared_distributions()
        assert "nautilus-ibapi" in _allowed_distributions()
        assert _declared_requirements()["nautilus-trader"] == {"ib"}

    def test_stdlib_and_first_party_imports_are_not_mistaken_for_dependencies(self):
        roots = _imported_roots(
            "import asyncio\nimport math\nfrom src.config import IBKRSettings\nimport structlog\n"
        )

        assert _third_party_roots(roots) == {"structlog"}


class TestTheAdapterShipsWithNautilus:
    """AC #7's rationale — the IB adapter needed no distribution of its own."""

    def test_nautilus_is_the_expected_pinned_version(self):
        try:
            installed = version("nautilus-trader")
        except PackageNotFoundError:  # pragma: no cover - the project cannot run without it
            pytest.fail("nautilus-trader is not installed")

        assert installed == EXPECTED_NAUTILUS_VERSION, (
            f"AR3's reasoning was checked against {EXPECTED_NAUTILUS_VERSION}, but "
            f"{installed} is installed. Re-verify that the IB adapter still ships in-tree "
            "before updating this pin."
        )

    def test_the_ib_adapter_belongs_to_the_nautilus_distribution(self):
        """No separate `ibapi`-style distribution had to be declared for Epic 1."""
        distributions = packages_distributions()

        assert {_normalize(name) for name in distributions.get("nautilus_trader", [])} == {
            "nautilus-trader"
        }
        assert "nautilus-trader" in _declared_distributions()
