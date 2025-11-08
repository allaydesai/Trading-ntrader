"""Add comprehensive Nautilus Trader metrics to performance_metrics table

Revision ID: 0937d13d2502
Revises: 9c7d5c448387
Create Date: 2025-11-07 21:32:33.402651

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0937d13d2502"
down_revision: Union[str, Sequence[str], None] = "9c7d5c448387"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add comprehensive Nautilus Trader metrics to performance_metrics table."""
    # Add returns-based metrics (from get_performance_stats_returns)
    op.add_column(
        "performance_metrics",
        sa.Column("risk_return_ratio", sa.Numeric(15, 6), nullable=True),
    )
    op.add_column(
        "performance_metrics", sa.Column("avg_return", sa.Numeric(15, 6), nullable=True)
    )
    op.add_column(
        "performance_metrics",
        sa.Column("avg_win_return", sa.Numeric(15, 6), nullable=True),
    )
    op.add_column(
        "performance_metrics",
        sa.Column("avg_loss_return", sa.Numeric(15, 6), nullable=True),
    )

    # Add PnL-based metrics (from get_performance_stats_pnls)
    op.add_column(
        "performance_metrics", sa.Column("total_pnl", sa.Numeric(20, 2), nullable=True)
    )
    op.add_column(
        "performance_metrics",
        sa.Column("total_pnl_percentage", sa.Numeric(15, 6), nullable=True),
    )
    op.add_column(
        "performance_metrics", sa.Column("max_winner", sa.Numeric(20, 2), nullable=True)
    )
    op.add_column(
        "performance_metrics", sa.Column("max_loser", sa.Numeric(20, 2), nullable=True)
    )
    op.add_column(
        "performance_metrics", sa.Column("min_winner", sa.Numeric(20, 2), nullable=True)
    )
    op.add_column(
        "performance_metrics", sa.Column("min_loser", sa.Numeric(20, 2), nullable=True)
    )


def downgrade() -> None:
    """Remove comprehensive Nautilus Trader metrics from performance_metrics table."""
    # Remove PnL-based metrics
    op.drop_column("performance_metrics", "min_loser")
    op.drop_column("performance_metrics", "min_winner")
    op.drop_column("performance_metrics", "max_loser")
    op.drop_column("performance_metrics", "max_winner")
    op.drop_column("performance_metrics", "total_pnl_percentage")
    op.drop_column("performance_metrics", "total_pnl")

    # Remove returns-based metrics
    op.drop_column("performance_metrics", "avg_loss_return")
    op.drop_column("performance_metrics", "avg_win_return")
    op.drop_column("performance_metrics", "avg_return")
    op.drop_column("performance_metrics", "risk_return_ratio")
