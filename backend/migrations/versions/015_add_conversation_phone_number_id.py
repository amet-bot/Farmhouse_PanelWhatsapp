"""store the receiving WhatsApp Phone Number ID per conversation

Revision ID: 015_conversation_phone_id
Revises: 014_yappy_payments
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "015_conversation_phone_id"
down_revision: Union[str, None] = "014_yappy_payments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "whatsapp_phone_number_id" not in _cols("conversations"):
        op.add_column(
            "conversations",
            sa.Column("whatsapp_phone_number_id", sa.String(length=50), nullable=True),
        )
        op.create_index(
            "ix_conversations_whatsapp_phone_number_id",
            "conversations",
            ["whatsapp_phone_number_id"],
            unique=False,
        )


def downgrade() -> None:
    if "whatsapp_phone_number_id" in _cols("conversations"):
        op.drop_index("ix_conversations_whatsapp_phone_number_id", table_name="conversations")
        op.drop_column("conversations", "whatsapp_phone_number_id")
