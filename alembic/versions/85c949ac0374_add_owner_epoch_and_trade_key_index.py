"""add owner_epoch and trade key index

Revision ID: 85c949ac0374
Revises: b7c419e2a3d8
Create Date: 2026-09-12 11:38:50.058438

⚠️ **This is the phase's THIRD migration**, sanctioned by retrospective
decision **D1 (Allay, 2026-08-28, Story 2.7's blockquote under AC #4)**: *"the
'single migration is spent' argument does not apply and must not be
revived"* — Story 2.7's own second-migration precedent (``b7c419e2a3d8``)
already falsified the cost argument once, and this column closes the hazard
that argument was blocking.

**The hazard this closes.** ``session_service._refuse_if_reclaimed`` compared
two *application* clocks (``trading_sessions.last_started_at``), and its own
docstring recorded the hole: *"Skew between hosts that exceeds the real gap
between the two claims makes the reclaim undetectable… nothing can clamp it
here without the fencing column."* The heartbeat additionally loaded the row
**unlocked** (``record_activity`` → ``_load_or_raise`` without ``for_update``)
and mutated the ORM object in Python, so its status/ownership check and its
write were two separate statements with a window between them — a second
process could reclaim the session in that window and the write would still
land. An integer epoch, qualified in the ``UPDATE``'s own ``WHERE`` clause,
closes both: no clock, no window. The database refuses the write itself.

**The residual this does NOT close, stated rather than implied** (AC #8a): a
``mark_stopped`` write that itself fails (e.g. Postgres unreachable at
teardown) leaves the row ``running`` at the same epoch — no fence can refuse
the abandoned worker, because there is no second writer to refuse yet. Only a
*successor's* reclaim closes it, by incrementing the epoch on its own
``-> running`` claim.

**Why the index needs no new column** (Story 3.6 fact 2, measured): under
NETTING one ``PositionId`` hosts every successive round trip on an
instrument+strategy, so ``trade_id`` alone is not unique per round trip —
but ``(trade_id, client_order_id)`` is, and both are already columns on
``trades``. Idempotent writes key on the pair directly; no ``trade_key``
column is added.

**Why NULL ``session_id`` rows never conflict** (measured, Task 1.2): Postgres
treats NULL as distinct in a unique index, so every backtest-owned row
(``session_id IS NULL``) — 14,982 of them at drafting — inserts freely
against every other NULL row and against every session-owned row, regardless
of ``trade_id``/``client_order_id`` overlap.

**Cost, measured, not asserted** (Task 2.3): ``trading_sessions`` holds 2
rows; ``owner_epoch BIGINT NOT NULL DEFAULT 0`` is metadata-only on PG 11+ for
a constant default (no table rewrite, no long lock — the same reasoning
``b7c419e2a3d8`` already used for its own column, here with a default
instead of nullability). The unique index on ``trades`` is a genuine table
build; at 14,982+ rows it completed in well under a second against the local
Postgres.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "85c949ac0374"
down_revision: Union[str, Sequence[str], None] = "b7c419e2a3d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the fencing column and the idempotency index."""
    op.add_column(
        "trading_sessions",
        sa.Column("owner_epoch", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_index(
        "uq_trades_session_trade_key",
        "trades",
        ["session_id", "trade_id", "client_order_id"],
        unique=True,
    )


def downgrade() -> None:
    """Drop the index, then the column."""
    op.drop_index("uq_trades_session_trade_key", table_name="trades")
    op.drop_column("trading_sessions", "owner_epoch")
