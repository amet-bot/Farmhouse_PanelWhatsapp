"""initial_schema

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-08-27 12:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # 1. Tabla: branches
    op.create_table(
        'branches',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('code', sa.String(length=50), nullable=False),
        sa.Column('color', sa.String(length=20), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        sa.UniqueConstraint('code')
    )
    op.create_index(op.f('ix_branches_id'), 'branches', ['id'], unique=False)
    op.create_index(op.f('ix_branches_name'), 'branches', ['name'], unique=True)

    # 2. Tabla: users
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('email', sa.String(length=150), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=50), nullable=False, server_default='agent'),
        sa.Column('branch_id', sa.Integer(), nullable=True),
        sa.Column('avatar_url', sa.String(length=255), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)

    # 3. Tabla: devices
    op.create_table(
        'devices',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('device_id', sa.String(length=50), nullable=False),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('device_type', sa.String(length=50), nullable=False),
        sa.Column('branch_id', sa.Integer(), nullable=False),
        sa.Column('assigned_user_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='offline'),
        sa.Column('ip_address', sa.String(length=50), nullable=True),
        sa.Column('last_seen', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['assigned_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_id')
    )
    op.create_index(op.f('ix_devices_id'), 'devices', ['id'], unique=False)
    op.create_index(op.f('ix_devices_device_id'), 'devices', ['device_id'], unique=True)

    # 4. Tabla: contacts
    op.create_table(
        'contacts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('phone', sa.String(length=50), nullable=False),
        sa.Column('avatar_url', sa.String(length=255), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('last_interaction', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('phone')
    )
    op.create_index(op.f('ix_contacts_id'), 'contacts', ['id'], unique=False)
    op.create_index(op.f('ix_contacts_phone'), 'contacts', ['phone'], unique=True)

    # 5. Tabla: conversations
    op.create_table(
        'conversations',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('branch_id', sa.Integer(), nullable=False),
        sa.Column('assigned_user_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='new'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['customer_id'], ['contacts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['assigned_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_conversations_id'), 'conversations', ['id'], unique=False)

    # 6. Tabla: messages
    op.create_table(
        'messages',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('direction', sa.String(length=20), nullable=False),
        sa.Column('sender_type', sa.String(length=20), nullable=False),
        sa.Column('sender_id', sa.Integer(), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('is_internal', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('whatsapp_message_id', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_messages_id'), 'messages', ['id'], unique=False)
    op.create_index(op.f('ix_messages_whatsapp_message_id'), 'messages', ['whatsapp_message_id'], unique=False)

    # 7. Tabla: orders
    op.create_table(
        'orders',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('order_code', sa.String(length=50), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('branch_id', sa.Integer(), nullable=False),
        sa.Column('order_type', sa.String(length=50), nullable=False, server_default='delivery'),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='en_proceso'),
        sa.Column('subtotal', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('delivery_cost', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('items_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('order_code')
    )
    op.create_index(op.f('ix_orders_id'), 'orders', ['id'], unique=False)
    op.create_index(op.f('ix_orders_order_code'), 'orders', ['order_code'], unique=True)

def downgrade() -> None:
    op.drop_table('orders')
    op.drop_table('messages')
    op.drop_table('conversations')
    op.drop_table('contacts')
    op.drop_table('devices')
    op.drop_table('users')
    op.drop_table('branches')
