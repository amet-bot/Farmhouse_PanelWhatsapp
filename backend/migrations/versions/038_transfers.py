"""transfers

Farmhouse Link, Fase 4 (segundo bloque): traslados de insumos entre sucursales, con su propio
estado (requested -> approved -> dispatched -> received / rejected / cancelled). No mueve
inventario al crearse; despachar y recibir generan sus propios movimientos en el libro creado en
la migración anterior (transfer_out / transfer_in).

Revision ID: 038_transfers
Revises: 037_inventory_movements
Create Date: 2026-09-24 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '038_transfers'
down_revision: Union[str, None] = '037_inventory_movements'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not inspector.has_table('transfers'):
        op.create_table(
            'transfers',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('from_branch_id', sa.Integer(), nullable=False),
            sa.Column('to_branch_id', sa.Integer(), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='requested'),
            sa.Column('requested_by_user_id', sa.Integer(), nullable=False),
            sa.Column('approved_by_user_id', sa.Integer(), nullable=True),
            sa.Column('dispatched_by_user_id', sa.Integer(), nullable=True),
            sa.Column('received_by_user_id', sa.Integer(), nullable=True),
            sa.Column('requested_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('approved_at', sa.DateTime(), nullable=True),
            sa.Column('dispatched_at', sa.DateTime(), nullable=True),
            sa.Column('received_at', sa.DateTime(), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['from_branch_id'], ['branches.id']),
            sa.ForeignKeyConstraint(['to_branch_id'], ['branches.id']),
            sa.ForeignKeyConstraint(['requested_by_user_id'], ['users.id']),
            sa.ForeignKeyConstraint(['approved_by_user_id'], ['users.id']),
            sa.ForeignKeyConstraint(['dispatched_by_user_id'], ['users.id']),
            sa.ForeignKeyConstraint(['received_by_user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_transfers_id'), 'transfers', ['id'], unique=False)
        op.create_index('ix_transfer_from_branch', 'transfers', ['from_branch_id'], unique=False)
        op.create_index('ix_transfer_to_branch', 'transfers', ['to_branch_id'], unique=False)

    if not inspector.has_table('transfer_items'):
        op.create_table(
            'transfer_items',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('transfer_id', sa.Integer(), nullable=False),
            sa.Column('inventory_item_id', sa.Integer(), nullable=False),
            sa.Column('quantity', sa.Numeric(precision=10, scale=3), nullable=False),
            sa.Column('unit_cost', sa.Numeric(precision=10, scale=2), nullable=True),
            sa.ForeignKeyConstraint(['transfer_id'], ['transfers.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_transfer_items_id'), 'transfer_items', ['id'], unique=False)
        op.create_index('ix_transfer_item_inventory', 'transfer_items', ['inventory_item_id'], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if inspector.has_table('transfer_items'):
        op.drop_table('transfer_items')
    if inspector.has_table('transfers'):
        op.drop_table('transfers')
