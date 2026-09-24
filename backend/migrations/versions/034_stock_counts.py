"""stock_counts

Conteo físico: la foto de lo que hay en el estante. Dos tablas nuevas, sin tocar las demás —
la existencia sigue calculándose al vuelo, ahora sumando también las diferencias de conteo.

Revision ID: 034_stock_counts
Revises: 033_supplier_invu
Create Date: 2026-09-23 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '034_stock_counts'
down_revision: Union[str, None] = '033_supplier_invu'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not inspector.has_table('stock_counts'):
        op.create_table(
            'stock_counts',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('branch_id', sa.Integer(), nullable=False),
            sa.Column('counted_by_user_id', sa.Integer(), nullable=False),
            sa.Column('counted_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
            sa.ForeignKeyConstraint(['counted_by_user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_stock_counts_id'), 'stock_counts', ['id'], unique=False)
        op.create_index('ix_stock_count_branch_counted', 'stock_counts', ['branch_id', 'counted_at'], unique=False)

    if not inspector.has_table('stock_count_items'):
        op.create_table(
            'stock_count_items',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('stock_count_id', sa.Integer(), nullable=False),
            sa.Column('inventory_item_id', sa.Integer(), nullable=False),
            sa.Column('expected_quantity', sa.Numeric(precision=10, scale=3), nullable=False),
            sa.Column('counted_quantity', sa.Numeric(precision=10, scale=3), nullable=False),
            sa.Column('difference', sa.Numeric(precision=10, scale=3), nullable=False),
            sa.Column('unit_cost', sa.Numeric(precision=10, scale=2), nullable=True),
            sa.ForeignKeyConstraint(['stock_count_id'], ['stock_counts.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_stock_count_items_id'), 'stock_count_items', ['id'], unique=False)
        op.create_index('ix_stock_count_item_inventory', 'stock_count_items', ['inventory_item_id'], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    for table in ('stock_count_items', 'stock_counts'):
        if inspector.has_table(table):
            op.drop_table(table)
