"""The conditions a sealed session ran under — AR7's ``session_conditions`` keys (Story 5.4).

Owns: the flat keys of the ``config_snapshot["session_conditions"]`` document a
seal writes (FR43, FR45, AR7) and the two the PO ruled beside them, plus the
vocabulary they are written in. Pure: no database, no clock, no I/O. Every value
is JSON-native, because the document is JSONB: money is a string (AR29's
convention), positions a list.

Does not own: the ``equity_series`` key (Story 5.2's ``session_equity``, merged
in beside these by ``SessionResults.merged_into``), what the seal reads to fill
them (``session_seal_service``), or open positions at a ``--force`` seal (Story
5.5 — ``open_positions_at_seal`` already records whatever view it is handed).

**Why none of this is recorded during the run.** Every condition is either in
the seal's hands or fixed by code a session cannot run without:

- ``starting_capital`` — the operator's ``--starting-capital`` (Story 5.3, PO
  ruling R1a), stored as given: the paper account's own balance, never scaled
  against a backtest's (AC #5). Comparability comes from matching the accounts.
- ``rth_only`` / ``market_data_tier`` — the node builder refuses to build a node
  on anything else (``resolve_live_use_rth``, ``resolve_live_market_data_type``),
  and a broker-side downgrade to delayed data stops the session
  (``live_bar_observer``). The tier is the builder's own constant, imported. RTH
  has no constant upstream (the resolver returns a literal ``True``), so
  ``RTH_ONLY`` is a literal **pinned by a test** to the resolver, both ways.

**What "recorded" means for those three.** ``rth_only``, ``market_data_tier``
and ``price_basis`` are asserted *at seal time* from the policy every Phase-3
build enforces on every start — not read from the run. That is honest only
while the policy holds: a build that relaxes RTH or REALTIME must record the
value per run first (deferred-work, Story 5.4 code review W2).
- ``price_basis`` — structural: live bars are IBKR's, unadjusted and
  SMART-routed; a backtest's are FirstRate's, adjusted and consolidated. The
  permanent divergence source, named so a comparison can attribute it. A
  backtest writes **no** ``session_conditions`` (Story 5.2 R2a keeps its rows
  byte-identical); ``BACKTEST_PRICE_BASIS`` exists so "distinct" is checkable.
- ``closed_trade_count`` — handed in from ``equity_series.closed_trades``: one
  computation, two keys, pinned equal by the seal's tests.
- ``open_positions_at_seal`` — the session's own view, every owner netted, so a
  split's extra shares held by a synthetic owner after the strategy exits are
  in it (Story 4.7's debt). Empty unless Story 5.5's ``--force`` sealed past
  held positions, and ``None`` (JSON ``null``) when the seal's holdings are
  unknown — a forced seal past an empty engine cache — so unknown never reads
  as flat (5.4 code review W1).

PO rulings (2026-10-11): **R1a** — ``start_date_basis`` states that the run's
``start_date`` is the session's creation, a disclosed deviation from "first
start" (nothing durable records one, and there is no migration). **R2a** —
``last_run_flags`` is the sealed row's ``runtime_flags`` as stored, or ``None``:
the **last process run's** facts only, since they are cleared on every start.
**R3a** — nothing per-session is claimed about reconciliation-owned activity;
``price_basis`` and ``equity_series.excludes`` state it structurally.
"""

import copy
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import Numeric

from src.core.live_market_data import REQUIRED_LIVE_MARKET_DATA_TYPE_NAME
from src.db.models.backtest import BacktestRun
from src.models.reconciliation import ViewPosition

#: The document's version — bumped only when a key's meaning changes.
SCHEMA_VERSION = 1
#: A paper session's bars: IBKR's, unadjusted, SMART-routed (AR7).
PAPER_PRICE_BASIS = "ibkr_unadjusted_smart"
#: A backtest's bars: FirstRate's, split- and dividend-adjusted (AR7). Never
#: written — a backtest carries no ``session_conditions`` — but named, so the
#: vocabulary is closed and "distinct from a backtest's" can be checked.
BACKTEST_PRICE_BASIS = "firstrate_adjusted"
PRICE_BASES = (BACKTEST_PRICE_BASIS, PAPER_PRICE_BASIS)
#: The only market-data type a live session runs on (FR3, AR21).
MARKET_DATA_TIER = REQUIRED_LIVE_MARKET_DATA_TYPE_NAME
#: The only trading-hours basis a live session runs on (FR4) —
#: ``resolve_live_use_rth`` returns this or refuses; a test pins the two.
RTH_ONLY = True
#: PO ruling R1a: the run's ``start_date`` is ``trading_sessions.created_at``.
START_DATE_BASIS = "session_created_at"

#: AR7's seven keys, exactly — pinned against the PRD's own list.
AR7_KEYS = (
    "starting_capital",
    "rth_only",
    "price_basis",
    "market_data_tier",
    "closed_trade_count",
    "open_positions_at_seal",
    "schema_version",
)
START_DATE_BASIS_KEY = "start_date_basis"
LAST_RUN_FLAGS_KEY = "last_run_flags"


def _capital_quantum() -> Decimal:
    """One unit of ``backtest_runs.initial_capital``'s scale, read from the column."""
    column_type = BacktestRun.__table__.c.initial_capital.type
    if not isinstance(column_type, Numeric) or column_type.scale is None:
        raise RuntimeError("backtest_runs.initial_capital is no longer a scaled Numeric")
    return Decimal(1).scaleb(-column_type.scale)


#: The cent the run row stores its capital to (``Numeric(20,2)``).
CAPITAL_QUANTUM = _capital_quantum()


def _capital(starting_capital: Decimal) -> str:
    """The capital as the run row stores it — the same value, the column's form.

    Raises:
        TypeError: Not a ``Decimal``.
        ValueError: The column would round it: a value change, refused rather
            than made (Story 5.1's whole-cent rule, held here too).
    """
    if not isinstance(starting_capital, Decimal):
        raise TypeError(
            f"the starting capital must be a Decimal, got {type(starting_capital).__name__}"
        )
    if not starting_capital.is_finite():
        raise ValueError(f"the starting capital {starting_capital} is not a finite amount")
    stored = starting_capital.quantize(CAPITAL_QUANTUM)
    if stored != starting_capital:
        raise ValueError(
            f"the starting capital {starting_capital} has fractions of a cent; the record "
            "stores it as given or not at all"
        )
    return str(stored)


def _count(closed_trade_count: int) -> int:
    if type(closed_trade_count) is not int:
        raise TypeError(
            f"the closed-trade count must be an int, got {type(closed_trade_count).__name__}"
        )
    if closed_trade_count < 0:
        raise ValueError(f"the closed-trade count cannot be negative ({closed_trade_count})")
    return closed_trade_count


def _positions(open_positions: Sequence[ViewPosition] | None) -> list[dict[str, str]] | None:
    if open_positions is None:  # unknown holdings — never recorded as flat (W1)
        return None
    recorded = []
    for position in open_positions:
        if not isinstance(position, ViewPosition):
            raise TypeError(
                f"an open position must be a ViewPosition, got {type(position).__name__}"
            )
        recorded.append(
            {"instrument_id": position.instrument_id, "quantity": str(position.quantity)}
        )
    return recorded


def build_session_conditions(
    *,
    starting_capital: Decimal,
    closed_trade_count: int,
    open_positions: Sequence[ViewPosition] | None,
    last_run_flags: Any,
) -> dict[str, Any]:
    """The flat ``session_conditions`` keys of one sealed record (FR43, FR45, AR7).

    Args:
        starting_capital: The operator's stated balance, whole cents (R1a).
        closed_trade_count: ``equity_series.closed_trades`` — never recounted.
        open_positions: The session's view at seal, every owner netted, or
            ``None`` when the seal's holdings are unknown (recorded as ``null``).
        last_run_flags: The sealed row's ``runtime_flags`` **as stored** (R2a):
            whatever JSON the column holds, or ``None``. Never judged — JSONB
            has no schema, and a stopped session must always be sealable, so a
            hand-edited or restored document is recorded, not refused (code
            review P4).

    Returns:
        A new, JSON-native mapping: AR7's seven keys, ``start_date_basis``
        (always :data:`START_DATE_BASIS` — the run's ``start_date`` is
        ``period_start``'s, so no caller may state another; code review P9)
        and ``last_run_flags``. Nothing given is kept by reference.

    Raises:
        TypeError: A capital, count or position of the wrong type.
        ValueError: A non-finite capital, one the column would round, or a
            negative count.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "starting_capital": _capital(starting_capital),
        "rth_only": RTH_ONLY,
        "price_basis": PAPER_PRICE_BASIS,
        "market_data_tier": MARKET_DATA_TIER,
        "closed_trade_count": _count(closed_trade_count),
        "open_positions_at_seal": _positions(open_positions),
        START_DATE_BASIS_KEY: START_DATE_BASIS,
        LAST_RUN_FLAGS_KEY: copy.deepcopy(last_run_flags),
    }
