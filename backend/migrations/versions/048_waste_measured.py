"""waste_measured

Peso real vs. peso promedio en la merma de "pieza entera":
  - waste_items.measured_amount: lo que marcó la balanza para esa línea (g, o ml si el insumo es
    de volumen), cuando se pesó. Si falta en una "pieza entera", el peso salió del promedio de la
    pieza y es una estimación.
  - waste_records.weight_estimated: el bloque "Peso" del registro se calculó con algún promedio.
Ambas nullable: lo ya cargado no cambia.

Revision ID: 048_waste_measured
Revises: 047_waste_piece_mode
Create Date: 2026-09-27 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '048_waste_measured'
down_revision: Union[str, None] = '047_waste_piece_mode'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    ('waste_items', 'measured_amount', sa.Numeric(12, 3)),
    ('waste_records', 'weight_estimated', sa.Boolean()),
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
