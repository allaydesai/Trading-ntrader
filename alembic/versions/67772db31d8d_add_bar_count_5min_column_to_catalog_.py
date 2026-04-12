"""add bar_count_5min column to catalog_instruments                                                                                                                                 
                                                                                                                                                                                    
Revision ID: 67772db31d8d                                                                                                                                                           
Revises: 677ed1cdf56f                                                                                                                                                               
Create Date: 2026-04-12 13:59:45.307944                                                                                                                                             
                                                                                                                                                                                    
"""                                                                                                                                                                                 
from typing import Sequence, Union                                                                                                                                                  
                                                                                                                                                                                    
from alembic import op                                                                                                                                                           
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '67772db31d8d'
down_revision: Union[str, Sequence[str], None] = '677ed1cdf56f'                                                                                                                     
branch_labels: Union[str, Sequence[str], None] = None                                                                                                                               
depends_on: Union[str, Sequence[str], None] = None                                                                                                                                  
                                                                                                                                                                                    
                                                                                                                                                                                    
def upgrade() -> None:                                                                                                                                                              
    """Add bar_count_5min column to catalog_instruments."""                                                                                                                      
    op.add_column(
        "catalog_instruments",
        sa.Column(                                                                                                                                                                  
            "bar_count_5min", sa.Integer(), server_default="0", nullable=False                                                                                                      
        ),                                                                                                                                                                          
    )                                                                                                                                                                               
                                                                                                                                                                                 
                                                                                                                                                                                    
def downgrade() -> None:                                                                                                                                                            
    """Remove bar_count_5min column from catalog_instruments."""                                                                                                                    
    op.drop_column("catalog_instruments", "bar_count_5min")