"""AC1 guard (Story 2.1): the ``30-MINUTE-LAST`` bar-type literal is defined once.

The central timeframe enum (``ExplorerTimeframe`` in ``src/api/models/explorer.py``)
is the single source of truth for the 30min bar-type string. No other module under
``src/`` may hard-code the ``30-MINUTE-LAST`` literal — everything else references the
enum. This prevents 5min/1hour/30min conflation creeping back in via copy-paste.
"""

from pathlib import Path

import pytest

_SRC_ROOT = Path(__file__).resolve().parents[3] / "src"
_ENUM_MODULE = _SRC_ROOT / "api" / "models" / "explorer.py"
_LITERAL = "30-MINUTE-LAST"


@pytest.mark.unit
def test_30min_bar_type_literal_defined_in_exactly_one_place():
    """``30-MINUTE-LAST`` appears as a literal only in the central enum module."""
    offenders = [
        py.relative_to(_SRC_ROOT).as_posix()
        for py in _SRC_ROOT.rglob("*.py")
        if py != _ENUM_MODULE and _LITERAL in py.read_text(encoding="utf-8")
    ]
    assert offenders == [], (
        f"Inline '{_LITERAL}' literal found outside the central enum module: "
        f"{offenders}. Reference ExplorerTimeframe.THIRTY_MIN.bar_type_spec instead."
    )


@pytest.mark.unit
def test_enum_module_defines_the_literal():
    """Sanity: the central enum module does contain the single definition."""
    assert _LITERAL in _ENUM_MODULE.read_text(encoding="utf-8")
