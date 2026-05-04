"""DEPRECATED (Story 3.4): superseded by ``test_aapl_2018_ibkr_vs_firstrate.py``.

The CSV-based ``Path A`` (``data/AAPL_1min.csv``) was replaced by an
IBKR autofetch path so both sides of the comparison are independently
authoritative. The harness's signature changed accordingly:
``--legacy-csv`` and ``filter_csv_to_2018`` were removed in 3.4.

This module is preserved as a transitional marker — Story 3.5 cleanup
will delete it once 3.4 is stable. Tests are skipped at module level
to avoid stale-signature collection errors.
"""

from __future__ import annotations

import pytest

pytest.skip(
    "DEPRECATED — see tests/integration/core/test_aapl_2018_ibkr_vs_firstrate.py "
    "(Story 3.4). Will be removed in Story 3.5 cleanup.",
    allow_module_level=True,
)
