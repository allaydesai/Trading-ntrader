"""add strategy_id to trades

Revision ID: dc41856246ad
Revises: 72a945ca06f5
Create Date: 2026-10-21 00:00:00

MCP phase 5b's first step: which strategy owned each trade, so a run holding
more than one strategy can be attributed per sleeve. Nullable and not
backfilled — a row written before this column has no recorded owner, and
inventing one would be worse than admitting it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dc41856246ad"
down_revision: Union[str, Sequence[str], None] = "72a945ca06f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("trades", sa.Column("strategy_id", sa.String(length=100), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("trades", "strategy_id")
