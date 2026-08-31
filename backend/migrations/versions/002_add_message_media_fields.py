"""add_message_media_fields

Revision ID: 002_add_message_media_fields
Revises: 001_initial_schema
Create Date: 2026-08-29 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '002_add_message_media_fields'
down_revision: Union[str, None] = '001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column('messages', sa.Column('media_url', sa.String(length=500), nullable=True))
    op.add_column('messages', sa.Column('media_type', sa.String(length=20), nullable=True))
    op.add_column('messages', sa.Column('media_mime_type', sa.String(length=100), nullable=True))

def downgrade() -> None:
    op.drop_column('messages', 'media_mime_type')
    op.drop_column('messages', 'media_type')
    op.drop_column('messages', 'media_url')
