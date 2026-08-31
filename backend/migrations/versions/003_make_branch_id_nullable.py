"""make_conversation_branch_id_nullable

Revision ID: 003_make_branch_id_nullable
Revises: 002_add_message_media_fields
Create Date: 2026-08-29 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '003_make_branch_id_nullable'
down_revision: Union[str, None] = '002_add_message_media_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.alter_column('conversations', 'branch_id', existing_type=sa.Integer(), nullable=True)

def downgrade() -> None:
    op.alter_column('conversations', 'branch_id', existing_type=sa.Integer(), nullable=False)
