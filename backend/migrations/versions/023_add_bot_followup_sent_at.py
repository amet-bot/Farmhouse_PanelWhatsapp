"""add bot_followup_sent_at to conversations (bot mas persistente: reengancha al cliente callado)

Revision ID: 023_bot_followup_sent_at
Revises: 022_catering_handoff_node
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "023_bot_followup_sent_at"
down_revision: Union[str, None] = "022_catering_handoff_node"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "bot_followup_sent_at" not in _cols("conversations"):
        op.add_column(
            "conversations",
            sa.Column("bot_followup_sent_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    if "bot_followup_sent_at" in _cols("conversations"):
        op.drop_column("conversations", "bot_followup_sent_at")
