"""make catalog_instruments identity columns nullable

Revision ID: ff982c8e1402
Revises: 986f8a8d5bb4
Create Date: 2026-07-25 10:36:08.817004

Repairs a drift between the ORM and the migration chain.

``677ed1cdf56f`` created ``nautilus_id``, ``exchange`` and ``name`` as NOT NULL, and
no later revision relaxed them. The ORM (``src/db/models/catalog_instrument.py``)
declares all three nullable, and ``InstrumentMapper.sync_qualification`` writes NULL
to ``nautilus_id``/``exchange`` for every VENUE_UNRESOLVED ticker -- that null IS the
mechanism the backtest loader honors to keep an unqualified ticker out of backtests.

Existing databases were not built by that migration as written and are already
nullable, so this is a no-op there (Postgres DROP NOT NULL on an already-nullable
column costs microseconds). It matters for any freshly-migrated environment -- a new
dev box, CI, or a rebuilt production database -- where importing an unresolved-venue
ticker would otherwise raise IntegrityError.

``677ed1cdf56f`` is deliberately left untouched: it is already applied everywhere, so
editing it would make deployed databases disagree with their own recorded history.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ff982c8e1402"
down_revision: Union[str, Sequence[str], None] = "986f8a8d5bb4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (column, type) -- alter_column wants the existing type even when only nullability
# changes. Types mirror src/db/models/catalog_instrument.py.
_IDENTITY_COLUMNS = (
    ("nautilus_id", sa.String(length=50)),
    ("exchange", sa.String(length=20)),
    ("name", sa.String(length=200)),
)


def upgrade() -> None:
    """Relax the three identity columns to nullable."""
    for column_name, column_type in _IDENTITY_COLUMNS:
        op.alter_column(
            "catalog_instruments",
            column_name,
            existing_type=column_type,
            nullable=True,
        )


def downgrade() -> None:
    """Not reversible -- unresolved-venue rows legitimately hold NULL here."""
    raise NotImplementedError(
        "Cannot restore NOT NULL on catalog_instruments.nautilus_id/exchange/name: "
        "every VENUE_UNRESOLVED ticker stores NULL in these columns by design. "
        "Resolve or exclude every ticker first, then hand-write the constraint."
    )
