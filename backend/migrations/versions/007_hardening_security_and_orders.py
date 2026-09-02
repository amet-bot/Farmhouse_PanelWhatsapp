"""hardening_security_and_orders

Revision ID: 007_hardening_security_and_orders
Revises: 006_add_username_to_users
Create Date: 2026-08-31 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '007_hardening_security_and_orders'
down_revision: Union[str, None] = '006_add_username_to_users'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _idx_names(table: str) -> set:
    return {i['name'] for i in sa.inspect(op.get_bind()).get_indexes(table)}


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    # Defensivo: esta base de datos puede haber sido inicializada alguna vez desde
    # database/schema.sql (que ya incluye el esquema final) con el alembic_version
    # desalineado, así que una columna "nueva" puede ya existir de antemano.
    if column.name not in _cols(table):
        op.add_column(table, column)


def upgrade() -> None:
    # 1. Tabla messages: status, error_detail, deleted_at, deleted_by
    _add_column_if_missing('messages', sa.Column('status', sa.String(length=20), nullable=False, server_default='sent'))
    _add_column_if_missing('messages', sa.Column('error_detail', sa.String(length=500), nullable=True))
    _add_column_if_missing('messages', sa.Column('deleted_at', sa.DateTime(), nullable=True))
    _add_column_if_missing('messages', sa.Column('deleted_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True))

    # 2. Unicidad de whatsapp_message_id en messages (Punto 4)
    # Si existe un índice previo no-único, dropearlo y crear el único
    existing_idx = _idx_names('messages')
    idx_name = op.f('ix_messages_whatsapp_message_id')
    if 'ix_messages_whatsapp_message_id' in existing_idx and idx_name not in existing_idx:
        op.drop_index('ix_messages_whatsapp_message_id', table_name='messages')
        existing_idx = _idx_names('messages')
    if idx_name not in existing_idx:
        op.create_index(idx_name, 'messages', ['whatsapp_message_id'], unique=True)

    # 3. Tabla conversations: deleted_at, deleted_by, automation_paused
    _add_column_if_missing('conversations', sa.Column('deleted_at', sa.DateTime(), nullable=True))
    _add_column_if_missing('conversations', sa.Column('deleted_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True))
    _add_column_if_missing('conversations', sa.Column('automation_paused', sa.Boolean(), nullable=False, server_default='0'))

    # 4. Tabla contacts: deleted_at
    _add_column_if_missing('contacts', sa.Column('deleted_at', sa.DateTime(), nullable=True))

    # 5. Tabla orders: tax, total, created_by, deleted_at y conversión a DECIMAL (Punto 8)
    _add_column_if_missing('orders', sa.Column('tax', sa.Numeric(precision=10, scale=2), nullable=False, server_default='0.00'))
    _add_column_if_missing('orders', sa.Column('total', sa.Numeric(precision=10, scale=2), nullable=False, server_default='0.00'))
    _add_column_if_missing('orders', sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True))
    _add_column_if_missing('orders', sa.Column('deleted_at', sa.DateTime(), nullable=True))

    # Alterar subtotal y delivery_cost a DECIMAL(10,2)
    op.alter_column('orders', 'subtotal', existing_type=sa.Float(), type_=sa.Numeric(precision=10, scale=2), nullable=False, server_default='0.00')
    op.alter_column('orders', 'delivery_cost', existing_type=sa.Float(), type_=sa.Numeric(precision=10, scale=2), nullable=False, server_default='0.00')

def downgrade() -> None:
    # Revertir orders
    op.alter_column('orders', 'delivery_cost', existing_type=sa.Numeric(precision=10, scale=2), type_=sa.Float(), nullable=False)
    op.alter_column('orders', 'subtotal', existing_type=sa.Numeric(precision=10, scale=2), type_=sa.Float(), nullable=False)
    op.drop_column('orders', 'deleted_at')
    op.drop_column('orders', 'created_by')
    op.drop_column('orders', 'total')
    op.drop_column('orders', 'tax')

    # Revertir contacts
    op.drop_column('contacts', 'deleted_at')

    # Revertir conversations
    op.drop_column('conversations', 'automation_paused')
    op.drop_column('conversations', 'deleted_by')
    op.drop_column('conversations', 'deleted_at')

    # Revertir messages
    op.drop_index(op.f('ix_messages_whatsapp_message_id'), table_name='messages')
    op.create_index('ix_messages_whatsapp_message_id', 'messages', ['whatsapp_message_id'], unique=False)
    op.drop_column('messages', 'deleted_by')
    op.drop_column('messages', 'deleted_at')
    op.drop_column('messages', 'error_detail')
    op.drop_column('messages', 'status')
