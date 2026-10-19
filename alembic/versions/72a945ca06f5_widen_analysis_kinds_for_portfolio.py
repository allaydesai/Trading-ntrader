"""widen analysis kinds for portfolio

Revision ID: 72a945ca06f5
Revises: 87090e0d5660
Create Date: 2026-10-19 10:01:58

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '72a945ca06f5'
down_revision: Union[str, Sequence[str], None] = '87090e0d5660'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Written by hand: autogenerate does not diff CHECK constraints. Phase 5a adds the
    # 'portfolio' analysis kind (mirrors ANALYSIS_KINDS in src/db/models/research.py).
    op.drop_constraint('chk_analysis_kind', 'research_analyses', type_='check')
    op.create_check_constraint('chk_analysis_kind', 'research_analyses', "kind IN ('sweep', 'sensitivity', 'monte_carlo', 'walk_forward', 'breadth', 'cost_stress', 'portfolio')")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('chk_analysis_kind', 'research_analyses', type_='check')
    op.create_check_constraint('chk_analysis_kind', 'research_analyses', "kind IN ('sweep', 'sensitivity', 'monte_carlo', 'walk_forward', 'breadth', 'cost_stress')")
