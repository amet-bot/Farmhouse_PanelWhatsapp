"""add_corporate_intake_fields

Revision ID: 012_add_corporate_intake_fields
Revises: 011_add_message_media_id
Create Date: 2026-09-06 00:00:00.000000

Agrega conversations.corporate_intake_step y conversations.corporate_intake_notes para
soportar las preguntas guiadas del flujo de Pedido Corporativo/Evento (opción 4) antes de
pasarle la conversación a la encargada de eventos.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '012_add_corporate_intake_fields'
down_revision: Union[str, None] = '011_add_message_media_id'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    if column.name not in _cols(table):
        op.add_column(table, column)


def upgrade() -> None:
    _add_column_if_missing('conversations', sa.Column('corporate_intake_step', sa.Integer(), nullable=True))
    _add_column_if_missing('conversations', sa.Column('corporate_intake_notes', sa.Text(), nullable=True))


def downgrade() -> None:
    if 'corporate_intake_notes' in _cols('conversations'):
        op.drop_column('conversations', 'corporate_intake_notes')
    if 'corporate_intake_step' in _cols('conversations'):
        op.drop_column('conversations', 'corporate_intake_step')
