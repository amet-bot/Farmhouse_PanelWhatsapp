"""internal_chat

Comunicación Interna: hilos, participantes y mensajes entre agentes. Tablas propias, sin tocar
conversations/messages, que son del Centro WhatsApp y están atados a Contact.

Revision ID: 030_internal_chat
Revises: 029_add_suppliers
Create Date: 2026-09-21 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '030_internal_chat'
down_revision: Union[str, None] = '029_add_suppliers'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not inspector.has_table('internal_threads'):
        op.create_table(
            'internal_threads',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('kind', sa.String(length=20), nullable=False, server_default='direct'),
            sa.Column('branch_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('last_message_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_internal_threads_id'), 'internal_threads', ['id'], unique=False)
        op.create_index(op.f('ix_internal_threads_last_message_at'), 'internal_threads', ['last_message_at'], unique=False)

    if not inspector.has_table('internal_participants'):
        op.create_table(
            'internal_participants',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('thread_id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('last_read_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['thread_id'], ['internal_threads.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('thread_id', 'user_id', name='uq_internal_participant'),
        )
        op.create_index(op.f('ix_internal_participants_id'), 'internal_participants', ['id'], unique=False)
        op.create_index('ix_internal_participant_user', 'internal_participants', ['user_id'], unique=False)

    if not inspector.has_table('internal_messages'):
        op.create_table(
            'internal_messages',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('thread_id', sa.Integer(), nullable=False),
            sa.Column('sender_user_id', sa.Integer(), nullable=False),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.ForeignKeyConstraint(['thread_id'], ['internal_threads.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['sender_user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_internal_messages_id'), 'internal_messages', ['id'], unique=False)
        op.create_index('ix_internal_message_thread_created', 'internal_messages', ['thread_id', 'created_at'], unique=False)


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    for table in ('internal_messages', 'internal_participants', 'internal_threads'):
        if inspector.has_table(table):
            op.drop_table(table)
