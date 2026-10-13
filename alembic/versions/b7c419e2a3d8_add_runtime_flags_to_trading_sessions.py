"""add runtime_flags to trading_sessions

Revision ID: b7c419e2a3d8
Revises: d08dfbd393f0
Create Date: 2026-08-23 15:10:42.118904

⚠️ **This is the phase's SECOND migration**, where ``epics.md:367-371`` gave
Story 2.2 the phase's *single* one and ``deferred-work.md:851-861`` re-pointed
the column question at Story 2.8. **Sanctioned by Allay, 2026-08-23** (Story
2.7, the blockquote under AC #4). The reasoning, in short:

1. **Containment without persistence is a regression, not a neutral omission.**
   Before Story 2.7, a raising strategy ends the process at ``os._exit(1)``,
   leaving the row ``running`` with a frozen heartbeat — which Story 2.8 renders
   ``stale``. Crude, but visible from another process. *After* containment the
   session heartbeats normally, and because the runner's ``note_bar`` is
   subscribed at the ``subscribe`` phase **before** any strategy subscribes at
   ``trading`` (measured dispatch order: ``['observer', 'note_bar', 's1']``),
   ``last_bar_at`` keeps advancing even when every strategy is dead. Story 2.8
   would render ``trading`` for a session that cannot place an order. Without
   this column, Story 2.7 ships a false green.
2. **Story 2.8 needs it anyway.** AR32 defines ``degraded`` as *"running +
   fresh heartbeat but ``connection.lost`` flagged"* and no column exists for
   that fact, while Story 2.8's AC #1 forbids depending on the runner's memory.
   The marginal cost of landing it here is **zero** migrations, not one.
3. **The cost is measured, not asserted.** ``trading_sessions`` holds 2 rows; a
   nullable column with no default is metadata-only on PG 11+ (no table
   rewrite, no long lock); ``grep -rn "trading_session\\|TradingSession"
   src/api/ templates/`` returns **zero** hits, so AR44's zero-UI-change
   criterion is untouched; and AR37's AST guard matches only
   ``t.attr == "status"``, so a non-status column is invisible to it.

**Not a fifth ``SessionStatus`` value.** ``deferred-work.md:855`` decided that
half and it stands: the enum and its Postgres type both have exactly four
labels, and a failed *strategy* is not a session lifecycle state. The column is
the answer.

**Shape**, versioned so Story 2.8 can add ``connection_lost_at`` without
guessing::

    {
      "v": 1,
      "all_failed": false,
      "failed_strategies": [
        {"strategy_id": "SMACrossover-000", "spec_strategy_id": "sma_crossover",
         "error_type": "DivisionByZero", "handler": "handle_bar",
         "at": "2026-08-23T14:03:11.482913+00:00", "detail": "…"}
      ]
    }

``NULL`` means *nothing to report*, so every existing row and every healthy
session costs nothing — which is why the column is nullable with **no**
server_default rather than defaulting to ``'{}'``. No traceback is stored: that
belongs in the structlog sink. ``detail`` is redacted (NFR26) before it is
written.

The column is **cleared to NULL on every ``-> running`` transition**
(``session_service._apply_transition``), so a failure from a previous process
run is never reported against the current one.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c419e2a3d8"
down_revision: Union[str, None] = "d08dfbd393f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the nullable ``runtime_flags`` JSONB column."""
    op.add_column(
        "trading_sessions",
        sa.Column("runtime_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    """Drop it. Nothing else read it, so the reversal is clean."""
    op.drop_column("trading_sessions", "runtime_flags")
