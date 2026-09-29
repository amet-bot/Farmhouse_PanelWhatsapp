"""native_push_tokens

Tokens de Firebase (FCM) de los celulares con la app de Android: las notificaciones nativas
("a la vista"), además de las del navegador (push_subscriptions). Tabla nueva: nada cambia.

Revision ID: 051_native_push_tokens
Revises: 050_shipment_receiving
Create Date: 2026-09-29 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '051_native_push_tokens'
down_revision: Union[str, None] = '050_shipment_receiving'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table('native_push_tokens'):
        return
    op.create_table(
        'native_push_tokens',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token', sa.String(255), nullable=False, unique=True),
        sa.Column('platform', sa.String(20), nullable=False, server_default='android'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_native_push_tokens_id', 'native_push_tokens', ['id'])
    op.create_index('ix_native_push_tokens_user_id', 'native_push_tokens', ['user_id'])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table('native_push_tokens'):
        op.drop_table('native_push_tokens')
