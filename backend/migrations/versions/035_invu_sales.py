"""invu_sales

Farmhouse Link, etapa 1: las ventas de la caja de Invu llegan a la base. Cinco tablas nuevas
(menú por sucursal, órdenes, líneas, modificadores y la bitácora de días sincronizados), sin
tocar ninguna de las existentes.

Revision ID: 035_invu_sales
Revises: 034_stock_counts
Create Date: 2026-09-24 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '035_invu_sales'
down_revision: Union[str, None] = '034_stock_counts'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not inspector.has_table('invu_menu_items'):
        op.create_table(
            'invu_menu_items',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('branch_id', sa.Integer(), nullable=False),
            sa.Column('invu_id', sa.Integer(), nullable=False),
            sa.Column('code', sa.String(length=50), nullable=True),
            sa.Column('name', sa.String(length=200), nullable=False),
            sa.Column('suggested_price', sa.Numeric(precision=10, scale=2), nullable=True),
            sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('synced_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('branch_id', 'invu_id', name='uq_invu_menu_item_branch_invu'),
        )
        op.create_index(op.f('ix_invu_menu_items_id'), 'invu_menu_items', ['id'], unique=False)
        op.create_index(op.f('ix_invu_menu_items_code'), 'invu_menu_items', ['code'], unique=False)

    if not inspector.has_table('invu_sales'):
        op.create_table(
            'invu_sales',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('branch_id', sa.Integer(), nullable=False),
            sa.Column('invu_order_id', sa.Integer(), nullable=False),
            sa.Column('business_date', sa.Date(), nullable=False),
            sa.Column('opened_at', sa.DateTime(), nullable=True),
            sa.Column('closed_at', sa.DateTime(), nullable=True),
            sa.Column('status', sa.String(length=30), nullable=False),
            sa.Column('is_credit_note', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('order_type', sa.String(length=60), nullable=True),
            sa.Column('channel', sa.String(length=60), nullable=True),
            sa.Column('subtotal', sa.Numeric(precision=10, scale=2), nullable=True),
            sa.Column('discount', sa.Numeric(precision=10, scale=2), nullable=True),
            sa.Column('tax', sa.Numeric(precision=10, scale=2), nullable=True),
            sa.Column('total', sa.Numeric(precision=10, scale=2), nullable=True),
            sa.Column('synced_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('branch_id', 'invu_order_id', name='uq_invu_sale_branch_order'),
        )
        op.create_index(op.f('ix_invu_sales_id'), 'invu_sales', ['id'], unique=False)
        op.create_index('ix_invu_sale_branch_date', 'invu_sales', ['branch_id', 'business_date'], unique=False)

    if not inspector.has_table('invu_sale_lines'):
        op.create_table(
            'invu_sale_lines',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('sale_id', sa.Integer(), nullable=False),
            sa.Column('branch_id', sa.Integer(), nullable=False),
            sa.Column('business_date', sa.Date(), nullable=False),
            sa.Column('invu_line_id', sa.Integer(), nullable=False),
            sa.Column('invu_item_id', sa.Integer(), nullable=True),
            sa.Column('code', sa.String(length=50), nullable=True),
            sa.Column('name', sa.String(length=200), nullable=False),
            sa.Column('category', sa.String(length=100), nullable=True),
            sa.Column('quantity', sa.Numeric(precision=10, scale=3), nullable=False),
            sa.Column('unit_price', sa.Numeric(precision=10, scale=2), nullable=True),
            sa.Column('discount', sa.Numeric(precision=10, scale=2), nullable=True),
            sa.Column('total', sa.Numeric(precision=10, scale=2), nullable=True),
            sa.Column('status', sa.String(length=30), nullable=True),
            sa.Column('counted', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.ForeignKeyConstraint(['sale_id'], ['invu_sales.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('sale_id', 'invu_line_id', name='uq_invu_sale_line'),
        )
        op.create_index(op.f('ix_invu_sale_lines_id'), 'invu_sale_lines', ['id'], unique=False)
        op.create_index(op.f('ix_invu_sale_lines_code'), 'invu_sale_lines', ['code'], unique=False)
        op.create_index('ix_invu_sale_line_branch_date', 'invu_sale_lines', ['branch_id', 'business_date'], unique=False)

    if not inspector.has_table('invu_sale_modifiers'):
        op.create_table(
            'invu_sale_modifiers',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('line_id', sa.Integer(), nullable=False),
            sa.Column('invu_modifier_id', sa.Integer(), nullable=True),
            sa.Column('code', sa.String(length=50), nullable=True),
            sa.Column('name', sa.String(length=200), nullable=False),
            sa.Column('quantity', sa.Numeric(precision=10, scale=3), nullable=False),
            sa.Column('total', sa.Numeric(precision=10, scale=2), nullable=True),
            sa.ForeignKeyConstraint(['line_id'], ['invu_sale_lines.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_invu_sale_modifiers_id'), 'invu_sale_modifiers', ['id'], unique=False)
        op.create_index(op.f('ix_invu_sale_modifiers_line_id'), 'invu_sale_modifiers', ['line_id'], unique=False)

    if not inspector.has_table('invu_sync_days'):
        op.create_table(
            'invu_sync_days',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('branch_id', sa.Integer(), nullable=False),
            sa.Column('business_date', sa.Date(), nullable=False),
            sa.Column('orders_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('lines_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('net_total', sa.Numeric(precision=12, scale=2), nullable=True),
            sa.Column('invu_total', sa.Numeric(precision=12, scale=2), nullable=True),
            sa.Column('matches', sa.Boolean(), nullable=True),
            sa.Column('error', sa.Text(), nullable=True),
            sa.Column('synced_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('branch_id', 'business_date', name='uq_invu_sync_day'),
        )
        op.create_index(op.f('ix_invu_sync_days_id'), 'invu_sync_days', ['id'], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    for table in ('invu_sync_days', 'invu_sale_modifiers', 'invu_sale_lines', 'invu_sales', 'invu_menu_items'):
        if inspector.has_table(table):
            op.drop_table(table)
