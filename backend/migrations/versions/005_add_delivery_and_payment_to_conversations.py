"""add_delivery_and_payment_to_conversations

Revision ID: 005_add_delivery_and_payment
Revises: 004_add_last_branch_prompt_at
Create Date: 2026-08-29 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '005_add_delivery_and_payment'
down_revision: Union[str, None] = '004_add_last_branch_prompt_at'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column('conversations', sa.Column('delivery_type', sa.String(length=20), nullable=True))
    op.add_column('conversations', sa.Column('payment_method', sa.String(length=20), nullable=True))

def downgrade() -> None:
    op.drop_column('conversations', 'payment_method')
    op.drop_column('conversations', 'delivery_type')
