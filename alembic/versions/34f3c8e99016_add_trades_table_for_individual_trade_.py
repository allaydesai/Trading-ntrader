"""add trades table for individual trade tracking

Revision ID: 34f3c8e99016
Revises: 0937d13d2502
Create Date: 2025-11-22 17:01:00.464565

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "34f3c8e99016"
down_revision: Union[str, Sequence[str], None] = "0937d13d2502"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: Create trades table for individual trade tracking."""
    # Create trades table
    op.create_table(
        "trades",
        sa.Column("id", sa.BigInteger(), nullable=False, autoincrement=True),
        sa.Column("backtest_run_id", sa.BigInteger(), nullable=False),
        # Nautilus Trader identifiers
        sa.Column("instrument_id", sa.String(length=50), nullable=False),
        sa.Column("trade_id", sa.String(length=100), nullable=False),
        sa.Column("venue_order_id", sa.String(length=100), nullable=False),
        sa.Column("client_order_id", sa.String(length=100), nullable=True),
        # Trade details
        sa.Column("order_side", sa.String(length=10), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("entry_price", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("exit_price", sa.Numeric(precision=20, scale=8), nullable=True),
        # Costs
        sa.Column(
            "commission_amount", sa.Numeric(precision=20, scale=8), nullable=True
        ),
        sa.Column("commission_currency", sa.String(length=10), nullable=True),
        sa.Column("fees_amount", sa.Numeric(precision=20, scale=8), nullable=True),
        # Calculated fields
        sa.Column("profit_loss", sa.Numeric(precision=20, scale=8), nullable=True),
        sa.Column("profit_pct", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("holding_period_seconds", sa.Integer(), nullable=True),
        # Timestamps (UTC with timezone)
        sa.Column("entry_timestamp", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("exit_timestamp", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Constraints
        sa.CheckConstraint("quantity > 0", name="positive_quantity"),
        sa.CheckConstraint("entry_price > 0", name="positive_entry_price"),
        sa.CheckConstraint(
            "exit_price IS NULL OR exit_price > 0", name="positive_exit_price"
        ),
        sa.ForeignKeyConstraint(
            ["backtest_run_id"], ["backtest_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Create indexes
    op.create_index("idx_trades_backtest_run_id", "trades", ["backtest_run_id"])
    op.create_index("idx_trades_entry_timestamp", "trades", ["entry_timestamp"])
    op.create_index("idx_trades_instrument_id", "trades", ["instrument_id"])
    op.create_index(
        "idx_trades_backtest_time",
        "trades",
        ["backtest_run_id", sa.text("entry_timestamp DESC")],
    )


def downgrade() -> None:
    """Downgrade schema: Drop trades table."""
    # Drop indexes
    op.drop_index("idx_trades_backtest_time", table_name="trades")
    op.drop_index("idx_trades_instrument_id", table_name="trades")
    op.drop_index("idx_trades_entry_timestamp", table_name="trades")
    op.drop_index("idx_trades_backtest_run_id", table_name="trades")

    # Drop table
    op.drop_table("trades")
