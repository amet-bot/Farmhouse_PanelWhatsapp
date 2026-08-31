"""add_last_branch_prompt_at_to_conversations

Revision ID: 004_add_last_branch_prompt_at
Revises: 003_make_branch_id_nullable
Create Date: 2026-08-29 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '004_add_last_branch_prompt_at'
down_revision: Union[str, None] = '003_make_branch_id_nullable'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column('conversations', sa.Column('last_branch_prompt_at', sa.DateTime(), nullable=True))

def downgrade() -> None:
    op.drop_column('conversations', 'last_branch_prompt_at')
