"""Crypto dashboard (crypto M5, protected-file sign-off Allay 2026-11-24): panel, trades, list.

- Equity pages stay byte for byte ``tests/fixtures/equity_dashboard_golden/``
  (``test_futures_dashboard.py``), futures pages ``futures_dashboard_golden/``
  (``test_fx_dashboard.py``) and FX pages ``fx_dashboard_golden/``, captured here from the
  pre-M5 tree (``python -m tests.component.api.test_crypto_dashboard --capture-golden``, run
  once before crypto M5 touched a view). Don't recapture to make a failure pass: an FX diff is
  the bug.
- Crypto pages show the venue, fee tier, maker/taker commission split, modelled spread,
  fractional coin quantities at full precision, USD notional, the underlying return, USD P&L,
  the 365-day annualisation and the orders dropped under the minimum; the account return on
  USD equity stays the headline (decision C19).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.component.api import test_futures_dashboard as dash
from tests.component.api.test_fx_dashboard import fx_run

pytestmark = pytest.mark.component

FX_GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "fx_dashboard_golden"


class TestFxPagesAreUnchanged:
    @pytest.mark.parametrize("name", sorted(dash.PAGES))
    def test_fx_page_is_byte_for_byte_the_pre_crypto_m5_render(self, name):
        expected = (FX_GOLDEN / name).read_bytes().decode("utf-8")
        assert dash.render(fx_run())[name] == expected


def _capture_fx_golden() -> None:
    """Record the FX pages as the pre-crypto-M5 tree serves them (run once, before the edits)."""
    FX_GOLDEN.mkdir(parents=True, exist_ok=True)
    for name, html in dash.render(fx_run()).items():
        (FX_GOLDEN / name).write_bytes(html.encode("utf-8"))
        print(f"wrote {FX_GOLDEN / name} ({len(html)} chars)")


if __name__ == "__main__":
    if "--capture-golden" in sys.argv:
        _capture_fx_golden()
