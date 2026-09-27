"""waste_piece_mode

Merma por pieza entera o por parte:
  - inventory_items.piece_size: cuánto es una pieza entera del insumo, en gramos (o en ml si el
    insumo se mide en volumen). Con eso "se botó 1 baguette entero" se convierte solo a gramos.
  - waste_items.mode: "entera" | "parte" (NULL en las mermas cargadas antes de esto).
  - waste_items.pieces: cuántas piezas enteras, cuando mode = "entera".
Todas nullable: nada de lo ya cargado cambia.

Revision ID: 047_waste_piece_mode
Revises: 042_internal_chat_clear
Create Date: 2026-09-27 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '047_waste_piece_mode'
down_revision: Union[str, None] = '042_internal_chat_clear'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    ('inventory_items', 'piece_size', sa.Numeric(12, 3)),
    ('waste_items', 'mode', sa.String(length=10)),
    ('waste_items', 'pieces', sa.Numeric(10, 3)),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, name, type_ in COLUMNS:
        if not inspector.has_table(table):
            continue
        if name not in {c['name'] for c in inspector.get_columns(table)}:
            op.add_column(table, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, name, _ in reversed(COLUMNS):
        if not inspector.has_table(table):
            continue
        if name in {c['name'] for c in inspector.get_columns(table)}:
            op.drop_column(table, name)
