"""add EXCLUDED resolution status

Revision ID: a436f35f525c
Revises: ff982c8e1402
Create Date: 2026-07-25 10:39:41.522118

Adds the fourth ``resolution_status`` value backing the venue exclusion register.

The PRD's venue gate is "0 tickers with an unresolved venue" (prd.md:85, :97, :208).
A ticker can leave VENUE_UNRESOLVED two ways: its venue resolves, or it is entered in
``venue_exclusions.csv`` with a written reason and evidence (an ETF that is delisted,
untradeable, or that no authoritative source can qualify). EXCLUDED is the persisted,
queryable state for the second case, so the coverage report can count exclusions
separately and surface them rather than hiding them in the denominator.

Deliberately a distinct value rather than reusing UNRESOLVED (never-attempted) or
leaving the decision only in the CSV: consumers derive the backtestable universe from
``resolution_status``, so exclusion has to be visible to them without a file read.

Postgres 12+ permits ALTER TYPE ... ADD VALUE inside a transaction as long as the new
value is not *used* in the same transaction. This migration only adds it, so alembic's
surrounding transaction is fine.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a436f35f525c"
down_revision: Union[str, Sequence[str], None] = "ff982c8e1402"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add EXCLUDED to the resolution_status enum."""
    op.execute("ALTER TYPE resolution_status ADD VALUE IF NOT EXISTS 'EXCLUDED'")


def downgrade() -> None:
    """Not reversible -- Postgres cannot drop a value from an enum type."""
    raise NotImplementedError(
        "Postgres has no ALTER TYPE ... DROP VALUE. Removing EXCLUDED requires "
        "recreating the resolution_status type and rewriting every column that "
        "uses it, after re-resolving or deleting all EXCLUDED rows."
    )
