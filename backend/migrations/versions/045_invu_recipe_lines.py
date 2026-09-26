"""invu_recipe_lines

Recetas de Invu (platos y modificadores) guardadas por sucursal, para cruzar el uso real de cada
insumo (platos vendidos × receta) con la merma. Ver models/invu_sales.py:InvuRecipeLine.

Revision ID: 045_invu_recipe_lines
Revises: 044_waste_photos
Create Date: 2026-09-26 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '045_invu_recipe_lines'
down_revision: Union[str, None] = '044_waste_photos'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if sa.inspect(conn).has_table('invu_recipe_lines'):
        return
    op.create_table(
        'invu_recipe_lines',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('branch_id', sa.Integer(), nullable=False),
        sa.Column('source_type', sa.String(length=10), nullable=False),
        sa.Column('source_invu_id', sa.Integer(), nullable=False),
        sa.Column('source_name', sa.String(length=200), nullable=True),
        sa.Column('product_invu_id', sa.Integer(), nullable=False),
        sa.Column('product_code', sa.String(length=50), nullable=True),
        sa.Column('product_name', sa.String(length=200), nullable=True),
        sa.Column('quantity', sa.Numeric(12, 4), nullable=False),
        sa.Column('unit_name', sa.String(length=30), nullable=True),
        sa.Column('synced_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('branch_id', 'source_type', 'source_invu_id', 'product_invu_id', name='uq_invu_recipe_line'),
    )
    op.create_index('ix_invu_recipe_lines_id', 'invu_recipe_lines', ['id'])
    op.create_index('ix_invu_recipe_lines_product_invu_id', 'invu_recipe_lines', ['product_invu_id'])
    op.create_index('ix_invu_recipe_source', 'invu_recipe_lines', ['branch_id', 'source_type', 'source_invu_id'])


def downgrade() -> None:
    conn = op.get_bind()
    if sa.inspect(conn).has_table('invu_recipe_lines'):
        op.drop_index('ix_invu_recipe_source', table_name='invu_recipe_lines')
        op.drop_index('ix_invu_recipe_lines_product_invu_id', table_name='invu_recipe_lines')
        op.drop_index('ix_invu_recipe_lines_id', table_name='invu_recipe_lines')
        op.drop_table('invu_recipe_lines')
