"""add data_quality_flag column to backtest_runs (Story 3-6)

Adds a nullable ``data_quality_flag`` VARCHAR(50) and back-fills
``'tz_corrupted_pre_3.6'`` for runs that referenced a pre-fix FirstRate
catalog. The fix-commit timestamp is ``2026-05-04T01:45:26+00:00``
(commit 2171e02 — Story 3-4 "ibkr vs firstrate parity comparison").

Revision ID: 79f6e07bee8b
Revises: 67772db31d8d
Create Date: 2026-05-10 21:25:01.986017

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "79f6e07bee8b"
down_revision: Union[str, Sequence[str], None] = "67772db31d8d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Inlined here (not imported) so the migration stays self-contained and survives
# future refactors of application code.
_TZ_FIX_COMMIT_UTC = "2026-05-04T01:45:26+00:00"
_TZ_CORRUPTED_FLAG = "tz_corrupted_pre_3.6"


def upgrade() -> None:
    """Add column and back-fill known-corrupt catalog runs."""
    op.add_column(
        "backtest_runs",
        sa.Column("data_quality_flag", sa.String(length=50), nullable=True),
    )
    # Back-fill: any run that sourced bars from a named catalog before the
    # FirstRate timezone fix landed referenced corrupt timestamps.
    op.execute(
        sa.text(
            "UPDATE backtest_runs "
            "SET data_quality_flag = :flag "
            "WHERE data_source LIKE 'catalog:%' "
            "AND created_at < :cutoff"
        ).bindparams(flag=_TZ_CORRUPTED_FLAG, cutoff=_TZ_FIX_COMMIT_UTC)
    )


def downgrade() -> None:
    """Drop the column. The back-fill data is lost on downgrade."""
    op.drop_column("backtest_runs", "data_quality_flag")
