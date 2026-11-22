"""Text rows the CLI renderers share for futures runs (M4)."""

from decimal import Decimal

import pytest

from src.services.futures.display import (
    history_return_cell,
    signed_pct,
    summary_rows,
    symbol_cell,
    terms_line,
)
from src.services.futures.returns import RATIO_ADJUSTED_NOTE, summarise_run, terms_from_snapshot
from tests.unit.services.futures.test_returns import ES_SNAPSHOT, TRADE_A, TRADE_B

pytestmark = pytest.mark.unit


def test_signed_pct():
    assert signed_pct(Decimal("2.70272")) == "+2.70%"
    assert signed_pct(Decimal("-1.8345")) == "-1.83%"
    assert signed_pct(Decimal("0")) == "0.00%"
    assert signed_pct(None) == "N/A"


def test_summary_rows_lead_with_return_on_margin():
    rows = summary_rows(summarise_run(ES_SNAPSHOT, [TRADE_A, TRADE_B]))
    assert rows[0] == ("Return on margin (headline)", "+2.70%")
    assert ("Return on underlying", "+0.20%") in rows
    assert ("Peak margin used", "$55,000.00") in rows
    assert ("Max contracts", "2") in rows
    assert ("Net P&L (ratio-adjusted)", "$1,486.50") in rows
    assert ("Points per contract (adjusted)", "+10.00") in rows
    assert ("ES.CME", terms_line(terms_from_snapshot(ES_SNAPSHOT)["ES.CME"])) in rows


def test_terms_line():
    line = terms_line(terms_from_snapshot(ES_SNAPSHOT)["ES.CME"])
    assert line == (
        "x50 · initial margin $27,500.00 · maintenance $25,000.00 · "
        "$2.25/contract/side · as of 2026-11-21"
    )


def test_history_cells():
    summary = summarise_run(ES_SNAPSHOT, [TRADE_A, TRADE_B])
    assert history_return_cell(summary) == "RoM +2.70%"
    assert symbol_cell("ES", ES_SNAPSHOT) == "ES (fut)"
    assert symbol_cell("AAPL", {"config": {}}) == "AAPL"


def test_note_mentions_ratio_adjustment():
    assert "ratio-adjusted" in RATIO_ADJUSTED_NOTE
