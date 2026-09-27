"""internal_chat_clear

Agrega cleared_up_to_id a internal_participants: la marca de "vaciar mi chat", análoga a
last_read_at pero para qué mensajes deja de ver la persona en vez de hasta dónde leyó. Guarda
un id de internal_messages, no una fecha: created_at es DATETIME de segundo entero acá, y un
mensaje que llega en el mismo segundo del vaciado quedaría empatado con una marca de fecha y se
ocultaría para siempre con un ">" estricto. El id autoincremental no tiene ese empate posible.

Vaciar no borra ningún internal_message ni afecta a la otra persona del hilo — cada
participante tiene la suya.

Revision ID: 042_internal_chat_clear
Revises: 046_waste_processed (nació colgando de 041 en paralelo a 043-046; se encadena después
para que haya una sola cabeza y `alembic upgrade head` no falle al arrancar)
Create Date: 2026-09-26 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '042_internal_chat_clear'
down_revision: Union[str, None] = '046_waste_processed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table('internal_participants'):
        return
    existing = {c['name'] for c in inspector.get_columns('internal_participants')}
    if 'cleared_up_to_id' not in existing:
        op.add_column('internal_participants', sa.Column('cleared_up_to_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table('internal_participants'):
        return
    existing = {c['name'] for c in inspector.get_columns('internal_participants')}
    if 'cleared_up_to_id' in existing:
        op.drop_column('internal_participants', 'cleared_up_to_id')
