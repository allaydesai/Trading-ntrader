"""add instrument_metadata table and resolution_status enum

Revision ID: f051a079629c
Revises: dbec2c1f25a6
Create Date: 2026-06-17 21:59:43.707792

"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f051a079629c"
down_revision: Union[str, Sequence[str], None] = "dbec2c1f25a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The migration owns the CREATE TYPE / DROP TYPE DDL. ``create_type=False``
# stops SQLAlchemy from auto-emitting its own CREATE TYPE (which would collide
# with the explicit ``.create()`` below and with the ORM column's sa.Enum).
resolution_status_enum = postgresql.ENUM(
    "UNRESOLVED",
    "RESOLVED",
    "VENUE_UNRESOLVED",
    name="resolution_status",
    create_type=False,
)


def upgrade() -> None:
    """Upgrade schema: create resolution_status enum, then the table + index."""
    # 1. Enum type MUST exist before the column that uses it.
    resolution_status_enum.create(op.get_bind(), checkfirst=True)

    # 2. Table keyed by ticker alone (no autoincrement id).
    op.create_table(
        "instrument_metadata",
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("metadata_provider", sa.String(length=20), nullable=False),
        sa.Column("venue", sa.String(length=20), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("asset_type", sa.String(length=20), nullable=True),
        sa.Column("company_name", sa.String(length=200), nullable=True),
        sa.Column("sector", sa.String(length=100), nullable=True),
        sa.Column("industry", sa.String(length=100), nullable=True),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("ipo_date", sa.Date(), nullable=True),
        sa.Column("resolution_status", resolution_status_enum, nullable=False),
        sa.Column(
            "resolved_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
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
        sa.PrimaryKeyConstraint("ticker"),
    )

    # 3. B-tree index powering the O(1) completeness gate (Story 3.4).
    op.create_index(
        "ix_instrument_metadata_resolution_status",
        "instrument_metadata",
        ["resolution_status"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema: drop index, table, then the enum type LAST."""
    op.drop_index(
        "ix_instrument_metadata_resolution_status",
        table_name="instrument_metadata",
    )
    op.drop_table("instrument_metadata")
    # op.drop_table does NOT drop the enum type — drop it explicitly or a
    # subsequent upgrade fails with "type already exists".
    resolution_status_enum.drop(op.get_bind(), checkfirst=True)
