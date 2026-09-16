"""add awaiting_chat_order_description to conversations (Fase 4: pedir y pagar por chat)

Revision ID: 019_awaiting_chat_order
Revises: 018_conversation_last_opened_at
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "019_awaiting_chat_order"
down_revision: Union[str, None] = "018_conversation_last_opened_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "awaiting_chat_order_description" not in _cols("conversations"):
        op.add_column(
            "conversations",
            sa.Column("awaiting_chat_order_description", sa.Boolean(), nullable=True),
        )


def downgrade() -> None:
    if "awaiting_chat_order_description" in _cols("conversations"):
        op.drop_column("conversations", "awaiting_chat_order_description")
