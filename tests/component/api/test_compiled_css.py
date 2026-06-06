"""Smoke test: compiled Tailwind CSS contains every arbitrary class used in templates.

Background — Story 2-3 shipped the stats grid with
`grid-cols-[repeat(auto-fit,minmax(180px,1fr))]` but `./scripts/build-css.sh`
was never re-run, so `static/css/app.css` did not include the rule and the
grid rendered as a single stacked column for a full story. Component tests
greped the rendered HTML for the class name, which passed because the
class *was* in the markup — just not in the compiled stylesheet.

This test prevents that exact regression: scan templates for arbitrary
Tailwind class names (the `prefix-[value]` form), then assert the *value*
substring appears in `static/css/app.css`. Tailwind emits the bracket
contents verbatim into the CSS declaration (e.g. the class
`grid-cols-[repeat(auto-fit,minmax(180px,1fr))]` produces
`grid-template-columns: repeat(auto-fit,minmax(180px,1fr));`), so a raw
substring check is sufficient.

If this fails, run `./scripts/build-css.sh` and bump the cache-bust version
in `templates/base.html` (the `?v=N` suffix on the stylesheet link).

See epic-2 retro B4 (2026-04-19).
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES_DIR = PROJECT_ROOT / "templates"
COMPILED_CSS = PROJECT_ROOT / "static" / "css" / "app.css"

ARBITRARY_CLASS_PATTERN = r"[a-z-]+-\[[^\]]+\]"


def _scan_arbitrary_classes() -> set[str]:
    """Return the set of arbitrary Tailwind class names used across templates."""
    import re

    classes: set[str] = set()
    for path in TEMPLATES_DIR.rglob("*.html"):
        for match in re.finditer(r'class="([^"]*)"', path.read_text()):
            for token in match.group(1).split():
                if re.fullmatch(ARBITRARY_CLASS_PATTERN, token):
                    classes.add(token)
    return classes


@pytest.mark.component
def test_all_arbitrary_tailwind_classes_are_compiled() -> None:
    """Every `prefix-[value]` class in templates must appear in compiled CSS."""
    assert COMPILED_CSS.exists(), (
        f"Compiled CSS missing at {COMPILED_CSS}. Run ./scripts/build-css.sh"
    )
    css_text = COMPILED_CSS.read_text()

    arbitrary_classes = _scan_arbitrary_classes()

    missing: list[tuple[str, str]] = []
    for cls in sorted(arbitrary_classes):
        # Reason: extract the value between the brackets; Tailwind emits it
        # verbatim into the CSS declaration, so a substring check catches a
        # missing compile without needing to replicate Tailwind's escape rules.
        value = cls[cls.index("[") + 1 : cls.rindex("]")]
        if value not in css_text:
            missing.append((cls, value))

    assert not missing, (
        "Arbitrary Tailwind classes used in templates are not in compiled CSS.\n"
        "Run ./scripts/build-css.sh and bump the cache-bust version in base.html.\n"
        "Missing:\n" + "\n".join(f"  {cls} (value: {value})" for cls, value in missing)
    )
