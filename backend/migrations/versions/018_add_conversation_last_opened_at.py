"""add last_opened_at to conversations (recordatorio de respuesta pendiente)

Revision ID: 018_conversation_last_opened_at
Revises: 017_expand_main_intake
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "018_conversation_last_opened_at"
down_revision: Union[str, None] = "017_expand_main_intake"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "last_opened_at" not in _cols("conversations"):
        op.add_column(
            "conversations",
            sa.Column("last_opened_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    if "last_opened_at" in _cols("conversations"):
        op.drop_column("conversations", "last_opened_at")
