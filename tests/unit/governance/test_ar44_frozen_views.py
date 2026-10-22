"""AR44's zero-UI-change criterion, pinned byte for byte (Story 5.6, AC 5.6d).

Phase 3's standalone deliverable is a sealed paper session beside its backtest
"with **zero UI changes** — a success criterion, not an omission". AC 5.6d says
any change to the frozen surface "is treated as a success-criterion violation
rather than a judgment call", so this test makes it one: every file under the
AC's four surfaces is pinned by SHA-256, and a changed, added or removed file
goes red.

**The surface is the AC's four, and only those** (PO ruling R2b, 2026-10-11):
``src/api/**``, ``templates/**``, ``src/services/backtest_query.py`` and
``src/cli/commands/compare.py`` (the 2–10 run comparison view). ``static/js``
and the CLI's ``show``/``history`` are deliberately outside it: AR44 does not
name them, and a pin there would block legitimate CLI work.

**Only a recorded PO decision may regenerate** :data:`FROZEN_VIEWS` — the
size-cap baseline's "disclose and record" rule, made stricter. The manifest was
generated from a tree on which ``git diff --stat 740f8e6..HEAD`` over the four
surfaces is empty (740f8e6: the last Epic 4 commit, before Epic 5's first), so
it records the views as Epic 5 found them. The first story the PO allows to
change a view (Growth's "Paper" badge) retires or regenerates it, deliberately.

Regenerate, once the PO has ruled, with:
``python -m tests.unit.governance.test_ar44_frozen_views --print-manifest``,
pasted over :data:`FROZEN_VIEWS` as printed (it already wraps entries past 100 characters).

**It walks the file system, not git** (code review P14), so a unit run needs no
history: an untracked stray file left in a frozen tree (``web.py.orig`` from a
merge tool, ``detail.html~``) is reported as ``added`` too. Remove it — nothing
belongs in a frozen tree that the manifest does not name. Dot-prefixed paths
(``.DS_Store``) and ``__pycache__``/``*.pyc`` are not part of the surface; a
hidden or symlinked template could only be rendered through an ``include`` in a
frozen one, which this pin catches.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
#: The AC's two frozen trees — every file in them, whatever its type.
SURFACE_TREES = ("src/api", "templates")
#: The AC's two frozen files: the query service and the comparison view.
SURFACE_FILES = ("src/services/backtest_query.py", "src/cli/commands/compare.py")
#: Never part of the surface: Python's build output. Any dot-prefixed path part
#: (``.DS_Store``, an editor's ``.swp``) is skipped too, by :func:`_ignored`.
_IGNORED_PARTS = ("__pycache__",)
_IGNORED_SUFFIXES = (".pyc",)

#: ``{repo-relative path: sha256}`` of the frozen surface, as Epic 5 found it —
#: 59 files, equal to ``git ls-files`` over the four surfaces when generated.
#: **Regenerated 2026-10-11 by PO ruling**, merging 015-paper-trading into the MCP
#: branch (016-mcp-server): the MCP work changed two frozen files before AR44 was
#: pinned — ``src/api/rest/equity.py`` reads the curve via the new
#: ``BacktestQueryService.get_equity_curve`` (the ``run_equity_curves`` table). No
#: paper view changed; a sealed session still gets the 2-point fallback curve.
FROZEN_VIEWS: dict[str, str] = {
    "src/api/__init__.py": "bd3217228ed0c9f9600496636aa8c83a8401eea63f70bab2ef0019778ccb101e",
    "src/api/chart_bars.py": "1c966577ac331da69ad6e3b5c05857c999facc7c11cab3b2cf1d77c40428cb00",
    "src/api/dependencies.py": "4dbc2333b175f9673f8bc42663ac8a0a3a3cdcb44c9bbab31aa0a19489f0bedc",
    "src/api/explorer_verification.py": (
        "d057e383e2bce9a0679168386563fe2462166b371acc3757567500a872bad675"
    ),
    "src/api/metadata_panel_service.py": (
        "35dd2d0b1d4c894bbb480bd34ac14895c48628d07c77d89773f7e9f8b18d4199"
    ),
    "src/api/models/__init__.py": (
        "24111e8724ac4804fa632c0f7d2bab5b54608e65b389e4d8211ffec07e5e67a5"
    ),
    "src/api/models/backtest_detail.py": (
        "a6ce6dbd169ce6ccf024fc361a2f1c7a3ef5f09a5059b3840ef3475a76112e0a"
    ),
    "src/api/models/backtest_list.py": (
        "fb8f17848fd9b3ed1be633f5472627f367ae87ff8b4c3a095cfcfb9b3f22cffe"
    ),
    "src/api/models/chart_equity.py": (
        "612c65768abd41994fcd7f6c02abafb939802ca5d13b9132dfb416cb484488b2"
    ),
    "src/api/models/chart_errors.py": (
        "10f37372696f3d00f96e4b8e572682237b27d57677ff02c3eb6c50232190dd4d"
    ),
    "src/api/models/chart_indicators.py": (
        "1cbf4e33538f8f381d87b80046ec438fb1ad4e2a2405b0f0ecba1f08af7eb661"
    ),
    "src/api/models/chart_timeseries.py": (
        "f07cbd44cae0c1f6f9fd4be1ddd11e8481f7777653e395abb1c406af419fc3d2"
    ),
    "src/api/models/chart_trades.py": (
        "53d4d8322f4179f715259ed544ea9d9805ec967d8a9c69d84a665daf8a03abb7"
    ),
    "src/api/models/common.py": "977ab745090a07e7dc981825ae6938fc31f1770bfda2b59af30c32f395579841",
    "src/api/models/dashboard.py": (
        "dedec4c5b623736c3e8fc5b675863a25d55e440108e1d52cb617e95ed1151d52"
    ),
    "src/api/models/explorer.py": (
        "1f4ec1d0e1e77e90e05e90a5db45641add1a17a3598c06c45c920400ad50562a"
    ),
    "src/api/models/filter_models.py": (
        "ddad14d40bbd2abbc90bd4c8dae53093c38c43b8eb10be2485f497a2a272e345"
    ),
    "src/api/models/metadata_panel.py": (
        "bf812315ed73dbf07b90e2b40d5696b3ddbc973931214e49b6223a4ccba1ed98"
    ),
    "src/api/models/navigation.py": (
        "ffe42ceda6739a61f2546fa8ed5fde5daad022ca516c4c7a8d670e16ae3510fa"
    ),
    "src/api/models/run_backtest.py": (
        "2151db82df6c15b6b48ecb1a21d82a817a1546389b4a79ce119d047111588e49"
    ),
    "src/api/rest/__init__.py": "b8006fe2b3ba41a20e50d84a57c07620f9094c33a715bd72ead2d1b3dfd858c0",
    "src/api/rest/equity.py": "0bc035658be969be7aa5608a4b1db68fba2f777ae295389fdeb5669258fd7f54",
    "src/api/rest/explorer.py": "cd6194347d3abf8b97d266312a638ffbee7ddde016e1092e2f2f1eb256577af0",
    "src/api/rest/indicators.py": (
        "8a4537ae2daec2bee204c5f3b4fd9ce3b5f204cf60c440e4f5ade02d0a34dab3"
    ),
    "src/api/rest/timeseries.py": (
        "b038af31a01132ff4b4a878d3cc70a9ce9e9fb4388b014a0427a8cf4228a4a5d"
    ),
    "src/api/rest/trades.py": "842b916180c4e81dab9a185b3291e44cf742e1a5b5a71da32f77d2bf9e197837",
    "src/api/stats_service.py": "aebe188728379366b365c1a12993b3817b5a2cdd8c7aaee09b46712e7cc35fa9",
    "src/api/supplementary_service.py": (
        "4ae532d4155697d67b30fb436635576b3f30385ad6acea87f5139cc9da050f9a"
    ),
    "src/api/ui/__init__.py": "4eb820a851a97b3242ffeabe826021b52ed9ed8243e92839829086c05bd74457",
    "src/api/ui/backtests.py": "ef02660f3a4940015cfa025e809e6f74477ac3fcb41a671c3b60c527a0025606",
    "src/api/ui/dashboard.py": "56e5152ebf0123debc0ce4e11f70036d5a5b69c6d4e6da58034731b7575fd5b6",
    "src/api/ui/explorer.py": "601685c8c98d90ff3aad21733709a6caebc0cbf7a54e1e6e6ff2a7ab2d141444",
    "src/api/web.py": "881521499c53a6ac85cf2a46e5a94cf6f5a4a0ce4238927c92ab6a5c2fbd7dd0",
    "templates/backtests/detail.html": (
        "d4ab0653454adab2a64feac2f5b4c2567d9abd806291be554ecf011728416980"
    ),
    "templates/backtests/list.html": (
        "8088f00de4a8bcd6b28b3f4db93b6773209ea216440003f19253084da0c0fe84"
    ),
    "templates/backtests/list_fragment.html": (
        "f0730662a142dfc8b577c2770a43fb070ad76f796c080b386903d45865d48591"
    ),
    "templates/backtests/partials/strategy_params.html": (
        "5b4df67383ab929545d6c48777df3bd445d5ec6dde1dbb966a66efd33f8e2a48"
    ),
    "templates/backtests/run.html": (
        "56f94640820cedc0aa2581e141b0be4b0f6ebb55f429f3a04e7629d41eaab035"
    ),
    "templates/base.html": "107475cbc620190341d240a26ce9b4713d154db24c683aca126f17f1b1397c8e",
    "templates/dashboard.html": "5db92798b139bfe2e80f53262dfc0306488c658da973eaff54ebf4f065c621ae",
    "templates/explorer/chart_panel.html": (
        "32ff1c19301d5fb1a305b94d2ec0fd6cda70af796e260291eb6b0365d1469137"
    ),
    "templates/explorer/explorer.html": (
        "7c53fde0ded7c54c8b866278bf1cfd645c4b25250b6f1e32481e233ce8dfe403"
    ),
    "templates/explorer/metadata_panel.html": (
        "9e9384b8abe8f73381c0ecb2180f05721fccebca1fe46a821d42e81ebca19b1f"
    ),
    "templates/explorer/metadata_panel_skeleton.html": (
        "eb627a4ff60783ca74e5bbfb80480629ca3b85cf2b8ebc0c905c1c2a4b4244b1"
    ),
    "templates/explorer/stats_panel.html": (
        "9fd8a792f3e72b49b8b4a7b9f2f72427cd806a76cc513977198345d7d7ce5df3"
    ),
    "templates/explorer/stats_panel_skeleton.html": (
        "0363caac82217c6497ba0b6e7237b70906718a24417b74b8b3ce1b818831678a"
    ),
    "templates/explorer/supplementary_panel.html": (
        "9db4134c44b9e187183b3c0532e626f6e3a29ccf544c14cf8974e906b5d98d5d"
    ),
    "templates/explorer/supplementary_panel_skeleton.html": (
        "566a1621de62ec98dba9e8a27882bf88e054756211c1fc4b0d1ed620c903deeb"
    ),
    "templates/explorer/ticker_list.html": (
        "6b806170caa0c266a3627cafe1c2d8536b7d9f19e88993459e11215ce3880a88"
    ),
    "templates/partials/breadcrumbs.html": (
        "df8fc796e2198e9e05df915b467c15d95cac1aa77aa4395df751e8e305cd8de9"
    ),
    "templates/partials/config_snapshot.html": (
        "a5d8ad73458964fafdb6663465312f99a6e405f7ccbf5aad4c1ac073b2f9d551"
    ),
    "templates/partials/footer.html": (
        "5c6153ee9b7e7981776ac769e79150a27806bfe0527055d6f5ba4337b13adbfa"
    ),
    "templates/partials/htmx_error_handler.html": (
        "2dd569882b9c1213812dfd6508378e0eedbac1dcbfaadfc1360a46f76edc14bb"
    ),
    "templates/partials/metrics_panel.html": (
        "ef41a5b39af9c2ffd5575ce07814d34e347902d01378094fdc2801cc4a086b98"
    ),
    "templates/partials/nav.html": (
        "dbc124eb8c2c42947f04708009f2ea971a72e675f2095c1f2c93858a140f44d2"
    ),
    "templates/partials/trades_table.html": (
        "d8d012553f453aa00465ad8ec0c169c8871c5e98feeefb621c07a83a19a95adf"
    ),
    "templates/partials/trading_summary.html": (
        "354c60d895f95c731f917942bb6c50fe4e8a06067cda11d13571bfba3b784a65"
    ),
    "src/services/backtest_query.py": (
        "2d27d4ad648bbb0a8d4dccab913283f0a67c65866308aedbbcc8610022cd0db0"
    ),
    "src/cli/commands/compare.py": (
        "ae73dccbd662c4797a9778f1b2cbf9287fb13556d100af1857438286a9455fea"
    ),
}


def _ignored(relative: Path) -> bool:
    return (
        any(part in _IGNORED_PARTS or part.startswith(".") for part in relative.parts)
        or relative.suffix in _IGNORED_SUFFIXES
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def surface_digests(root: Path) -> dict[str, str]:
    """Every file of the frozen surface under ``root``, by repo-relative path."""
    digests: dict[str, str] = {}
    for tree in SURFACE_TREES:
        for path in sorted((root / tree).rglob("*")):
            relative = path.relative_to(root)
            if path.is_file() and not _ignored(relative):
                digests[relative.as_posix()] = _digest(path)
    for name in SURFACE_FILES:
        if (root / name).is_file():
            digests[name] = _digest(root / name)
    return digests


def surface_changes(recorded: dict[str, str], found: dict[str, str]) -> list[str]:
    """What differs, one line per file: ``changed``, ``added`` or ``removed``."""
    changes = [
        f"changed: {p}" for p in sorted(recorded.keys() & found.keys()) if recorded[p] != found[p]
    ]
    changes += [f"added: {p}" for p in sorted(found.keys() - recorded.keys())]
    changes += [f"removed: {p}" for p in sorted(recorded.keys() - found.keys())]
    return changes


def _violation(changes: list[str]) -> str:
    return (
        "AR44 — Phase 3's zero-UI-change success criterion (Story 5.6, AC 5.6d):\n  "
        + "\n  ".join(changes)
        + "\nA change to this surface is a success-criterion violation, not a judgment call. "
        "Revert it, or bring it to the PO: only a recorded PO decision may regenerate "
        "FROZEN_VIEWS (ruling R2b)."
    )


class TestTheFrozenSurfaceIsUntouched:
    @pytest.mark.acceptance_criterion(
        "5.6d",
        "src/api/**, templates/**, src/services/backtest_query.py and the comparison views are "
        "untouched, and any change to them is a success-criterion violation",
        "_bmad-output/planning-artifacts/prd-epic5-scope.md",
    )
    def test_every_file_is_byte_for_byte_as_recorded(self):
        changes = surface_changes(FROZEN_VIEWS, surface_digests(REPO_ROOT))

        assert not changes, _violation(changes)

    def test_the_manifest_covers_each_of_the_four_surfaces(self):
        """Against the manifest as recorded, an emptied tree reads as every file
        ``removed``. But a manifest *regenerated* from a glob that matched nothing
        would hold no entry for that tree, and the pin above would then pass
        vacuously: this catches that regeneration."""
        assert any(path.startswith("src/api/") for path in FROZEN_VIEWS)
        assert any(path.startswith("templates/") for path in FROZEN_VIEWS)
        assert set(SURFACE_FILES) <= FROZEN_VIEWS.keys()
        # The files Story 5.6 reads a sealed record through are in it by name.
        assert {
            "src/api/ui/backtests.py",
            "src/api/models/backtest_detail.py",
            "src/api/rest/trades.py",
            "templates/partials/config_snapshot.html",
            "templates/backtests/list_fragment.html",
        } <= FROZEN_VIEWS.keys()

    def test_the_surface_is_the_acs_four_and_nothing_else(self):
        """R2b, against the ruling's own words — not against the constants, which
        a widening would change together with the manifest (code review P14):
        ``static/js`` and the CLI's ``show``/``history`` stay out."""
        ruled_trees = ("src/api", "templates")
        ruled_files = {"src/services/backtest_query.py", "src/cli/commands/compare.py"}

        assert SURFACE_TREES == ruled_trees
        assert set(SURFACE_FILES) == ruled_files
        outside = {
            path
            for path in FROZEN_VIEWS
            if path not in ruled_files and not path.startswith(tuple(f"{t}/" for t in ruled_trees))
        }
        assert outside == set()


@pytest.fixture
def surface_copy(tmp_path: Path) -> Path:
    """The frozen surface, copied: the mutations below never touch the repo."""
    for tree in SURFACE_TREES:
        shutil.copytree(
            REPO_ROOT / tree, tmp_path / tree, ignore=shutil.ignore_patterns("__pycache__")
        )
    for name in SURFACE_FILES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / name, tmp_path / name)
    return tmp_path


class TestThePinCanFail:
    """Each way the surface can change is seen — the pin is not decorative."""

    def test_an_untouched_copy_matches_the_manifest(self, surface_copy):
        assert surface_changes(FROZEN_VIEWS, surface_digests(surface_copy)) == []

    def test_a_changed_byte_is_named(self, surface_copy):
        target = surface_copy / "templates/partials/config_snapshot.html"
        target.write_bytes(target.read_bytes() + b" ")

        assert surface_changes(FROZEN_VIEWS, surface_digests(surface_copy)) == [
            "changed: templates/partials/config_snapshot.html"
        ]

    def test_an_added_file_is_named(self, surface_copy):
        (surface_copy / "templates/partials/paper_badge.html").write_text("<span>Paper</span>")

        assert surface_changes(FROZEN_VIEWS, surface_digests(surface_copy)) == [
            "added: templates/partials/paper_badge.html"
        ]

    def test_a_removed_file_is_named(self, surface_copy):
        (surface_copy / "src/cli/commands/compare.py").unlink()

        assert surface_changes(FROZEN_VIEWS, surface_digests(surface_copy)) == [
            "removed: src/cli/commands/compare.py"
        ]

    def test_os_litter_is_not_part_of_the_surface(self, surface_copy):
        (surface_copy / "templates/.DS_Store").write_bytes(b"\x00")
        (surface_copy / "src/api/__pycache__").mkdir(exist_ok=True)
        (surface_copy / "src/api/__pycache__/web.cpython-311.pyc").write_bytes(b"\x00")

        assert surface_changes(FROZEN_VIEWS, surface_digests(surface_copy)) == []


def _print_manifest() -> None:
    """The manifest as it is written above: an entry past 100 characters wraps."""
    print("FROZEN_VIEWS: dict[str, str] = {")
    for path, digest in surface_digests(REPO_ROOT).items():
        line = f'    "{path}": "{digest}",'
        print(line if len(line) <= 100 else f'    "{path}": (\n        "{digest}"\n    ),')
    print("}")


if __name__ == "__main__":
    if "--print-manifest" in sys.argv:
        _print_manifest()
