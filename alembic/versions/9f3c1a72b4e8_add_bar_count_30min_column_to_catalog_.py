"""add bar_count_30min column to catalog_instruments

Revision ID: 9f3c1a72b4e8
Revises: f051a079629c
Create Date: 2026-06-22 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9f3c1a72b4e8'
down_revision: Union[str, Sequence[str], None] = 'f051a079629c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add bar_count_30min column to catalog_instruments."""
    op.add_column(
        "catalog_instruments",
        sa.Column(
            "bar_count_30min", sa.Integer(), server_default="0", nullable=False
        ),
    )


def downgrade() -> None:
    """Remove bar_count_30min column from catalog_instruments."""
    op.drop_column("catalog_instruments", "bar_count_30min")
