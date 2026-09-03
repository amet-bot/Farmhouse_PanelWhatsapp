"""add_message_media_id

Revision ID: 011_add_message_media_id
Revises: 010_add_active_cart_fields
Create Date: 2026-09-03 00:00:00.000000

Agrega messages.media_id: el identificador de objeto multimedia que devuelve WhatsApp
Cloud API (message.image.id, etc.), necesario para poder reintentar la descarga desde
Meta cuando falla la primera vez (POST /messages/{id}/retry-media).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '011_add_message_media_id'
down_revision: Union[str, None] = '010_add_active_cart_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if 'media_id' not in _cols('messages'):
        op.add_column('messages', sa.Column('media_id', sa.String(length=100), nullable=True))


def downgrade() -> None:
    if 'media_id' in _cols('messages'):
        op.drop_column('messages', 'media_id')
