"""add_inventory_cargamentos

Revision ID: 028_inventory_cargamentos
Revises: 027_entry_gate_node
Create Date: 2026-09-21 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '028_inventory_cargamentos'
down_revision: Union[str, None] = '027_entry_gate_node'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table('inventory_items'):
        op.create_table(
            'inventory_items',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('name', sa.String(length=150), nullable=False),
            sa.Column('unit', sa.String(length=30), nullable=False),
            sa.Column('category', sa.String(length=50), nullable=True),
            sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name'),
        )

    if not inspector.has_table('shipments'):
        op.create_table(
            'shipments',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('branch_id', sa.Integer(), nullable=False),
            sa.Column('received_by_user_id', sa.Integer(), nullable=False),
            sa.Column('received_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('supplier', sa.String(length=150), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
            sa.ForeignKeyConstraint(['received_by_user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )

    if not inspector.has_table('shipment_items'):
        op.create_table(
            'shipment_items',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('shipment_id', sa.Integer(), nullable=False),
            sa.Column('inventory_item_id', sa.Integer(), nullable=False),
            sa.Column('quantity', sa.Numeric(10, 3), nullable=False),
            sa.Column('unit_cost', sa.Numeric(10, 2), nullable=True),
            sa.ForeignKeyConstraint(['shipment_id'], ['shipments.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id']),
            sa.PrimaryKeyConstraint('id'),
        )

    existing_idx = {i['name'] for i in sa.inspect(op.get_bind()).get_indexes('inventory_items')}
    if op.f('ix_inventory_items_id') not in existing_idx:
        op.create_index(op.f('ix_inventory_items_id'), 'inventory_items', ['id'], unique=False)
    if op.f('ix_inventory_items_name') not in existing_idx:
        op.create_index(op.f('ix_inventory_items_name'), 'inventory_items', ['name'], unique=True)

    existing_idx = {i['name'] for i in sa.inspect(op.get_bind()).get_indexes('shipments')}
    if op.f('ix_shipments_id') not in existing_idx:
        op.create_index(op.f('ix_shipments_id'), 'shipments', ['id'], unique=False)

    existing_idx = {i['name'] for i in sa.inspect(op.get_bind()).get_indexes('shipment_items')}
    if op.f('ix_shipment_items_id') not in existing_idx:
        op.create_index(op.f('ix_shipment_items_id'), 'shipment_items', ['id'], unique=False)


def downgrade() -> None:
    op.drop_table('shipment_items')
    op.drop_table('shipments')
    op.drop_table('inventory_items')
