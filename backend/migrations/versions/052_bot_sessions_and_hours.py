"""bot_sessions_and_hours

Mejoras del bot: seguimiento de handoffs sin respuesta (bot_handoff_at / handoff_escalated_at),
la descripción del pedido por chat en la conversación, y el horario de cada sucursal para que
el bot avise cuando está cerrada. Solo columnas nuevas y nulas: nada de lo existente cambia.

Revision ID: 052_bot_sessions_and_hours
Revises: 051_native_push_tokens
Create Date: 2026-09-30 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '052_bot_sessions_and_hours'
down_revision: Union[str, None] = '051_native_push_tokens'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = {
    'conversations': [
        sa.Column('bot_handoff_at', sa.DateTime(), nullable=True),
        sa.Column('handoff_escalated_at', sa.DateTime(), nullable=True),
        sa.Column('chat_order_description', sa.Text(), nullable=True),
    ],
    'branches': [
        sa.Column('opens_at', sa.String(5), nullable=True),
        sa.Column('closes_at', sa.String(5), nullable=True),
    ],
}


def _existing(table: str) -> set:
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table, columns in _COLUMNS.items():
        have = _existing(table)
        for col in columns:
            if col.name not in have:
                op.add_column(table, col)


def downgrade() -> None:
    for table, columns in _COLUMNS.items():
        have = _existing(table)
        for col in columns:
            if col.name in have:
                op.drop_column(table, col.name)
