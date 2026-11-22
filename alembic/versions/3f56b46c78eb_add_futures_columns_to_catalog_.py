"""add futures columns to catalog_instruments

Revision ID: 3f56b46c78eb
Revises: dc41856246ad
Create Date: 2026-11-21 00:00:00

Futures M1: the contract economics a futures catalog row needs (multiplier, exchange tick,
currency) plus what kind of contract it is (a continuous series or one specific expiry),
its expiry and the roll rule behind a continuous series. All nullable and not backfilled:
equity rows have none of these, and M2's futures importer fills them from the hand-curated
contract spec.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3f56b46c78eb"
down_revision: Union[str, Sequence[str], None] = "dc41856246ad"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "catalog_instruments"
_KIND_CHECK = "ck_catalog_instruments_contract_kind"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(_TABLE, sa.Column("multiplier", sa.Numeric(18, 6), nullable=True))
    op.add_column(_TABLE, sa.Column("tick_size", sa.Numeric(18, 9), nullable=True))
    op.add_column(_TABLE, sa.Column("currency", sa.String(length=3), nullable=True))
    op.add_column(_TABLE, sa.Column("contract_kind", sa.String(length=20), nullable=True))
    op.add_column(_TABLE, sa.Column("expiry", sa.Date(), nullable=True))
    op.add_column(_TABLE, sa.Column("roll_rule", sa.String(length=50), nullable=True))
    op.create_check_constraint(_KIND_CHECK, _TABLE, "contract_kind IN ('continuous', 'specific')")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(_KIND_CHECK, _TABLE, type_="check")
    for column in ("roll_rule", "expiry", "contract_kind", "currency", "tick_size", "multiplier"):
        op.drop_column(_TABLE, column)
