"""add catalog instruments table

Revision ID: 677ed1cdf56f
Revises: 34f3c8e99016
Create Date: 2026-04-05 20:52:18.154506

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "677ed1cdf56f"
down_revision: Union[str, Sequence[str], None] = "34f3c8e99016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: Create catalog_instruments table."""
    op.create_table(
        "catalog_instruments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("nautilus_id", sa.String(length=50), nullable=False),
        sa.Column("asset_class", sa.String(length=20), nullable=False),
        sa.Column("catalog_name", sa.String(length=50), nullable=False),
        sa.Column("exchange", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("sector", sa.String(length=100), nullable=True),
        sa.Column("industry", sa.String(length=100), nullable=True),
        sa.Column("ipo_date", sa.Date(), nullable=True),
        sa.Column(
            "date_range_start",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "date_range_end",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "bar_count_daily",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "bar_count_hourly",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "bar_count_minute",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "catalog_name",
            "ticker",
            name="uq_catalog_instruments_catalog_ticker",
        ),
    )
    op.create_index(
        "ix_catalog_instruments_catalog_asset",
        "catalog_instruments",
        ["catalog_name", "asset_class"],
        unique=False,
    )
    op.create_index(
        "ix_catalog_instruments_ticker",
        "catalog_instruments",
        ["ticker"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema: Drop catalog_instruments table."""
    op.drop_index(
        "ix_catalog_instruments_ticker",
        table_name="catalog_instruments",
    )
    op.drop_index(
        "ix_catalog_instruments_catalog_asset",
        table_name="catalog_instruments",
    )
    op.drop_table("catalog_instruments")
