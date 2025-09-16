"""Create market_data table

Revision ID: 7d28f3a711e7
Revises:
Create Date: 2025-09-14 20:59:46.405631

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7d28f3a711e7"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create market_data table
    op.create_table(
        "market_data",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("timestamp", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("high", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("low", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("close", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "timestamp", name="uq_symbol_timestamp"),
    )

    # Create indexes for performance
    op.create_index(
        "idx_market_data_symbol_timestamp", "market_data", ["symbol", "timestamp"]
    )
    op.create_index("idx_market_data_timestamp", "market_data", ["timestamp"])


def downgrade() -> None:
    """Downgrade schema."""
    # Drop indexes
    op.drop_index("idx_market_data_timestamp", table_name="market_data")
    op.drop_index("idx_market_data_symbol_timestamp", table_name="market_data")

    # Drop table
    op.drop_table("market_data")
