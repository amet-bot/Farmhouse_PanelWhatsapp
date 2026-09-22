"""waste

Merma: la salida que le faltaba a Inventario. Dos tablas nuevas, sin tocar shipments ni
inventory_items — la existencia se calcula sumando las dos puntas, no se guarda.

Revision ID: 032_waste
Revises: 031_internal_attachments
Create Date: 2026-09-22 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '032_waste'
down_revision: Union[str, None] = '031_internal_attachments'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not inspector.has_table('waste_records'):
        op.create_table(
            'waste_records',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('branch_id', sa.Integer(), nullable=False),
            sa.Column('recorded_by_user_id', sa.Integer(), nullable=False),
            sa.Column('occurred_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('reason', sa.String(length=40), nullable=False),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
            sa.ForeignKeyConstraint(['recorded_by_user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_waste_records_id'), 'waste_records', ['id'], unique=False)
        op.create_index('ix_waste_branch_occurred', 'waste_records', ['branch_id', 'occurred_at'], unique=False)

    if not inspector.has_table('waste_items'):
        op.create_table(
            'waste_items',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('waste_record_id', sa.Integer(), nullable=False),
            sa.Column('inventory_item_id', sa.Integer(), nullable=False),
            sa.Column('quantity', sa.Numeric(precision=10, scale=3), nullable=False),
            sa.Column('unit_cost', sa.Numeric(precision=10, scale=2), nullable=True),
            sa.ForeignKeyConstraint(['waste_record_id'], ['waste_records.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_waste_items_id'), 'waste_items', ['id'], unique=False)
        op.create_index('ix_waste_item_inventory', 'waste_items', ['inventory_item_id'], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    for table in ('waste_items', 'waste_records'):
        if inspector.has_table(table):
            op.drop_table(table)
