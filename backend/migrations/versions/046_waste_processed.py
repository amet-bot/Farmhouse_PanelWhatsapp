"""waste_processed

Recorte o limpieza (merma de proceso): cuánto se limpió en total, para calcular el rendimiento
("de 5 kg de pollo quedaron 0.270 kg de recorte" → rinde 94.6 %). Dos columnas nullable.

Revision ID: 046_waste_processed
Revises: 045_invu_recipe_lines
Create Date: 2026-09-26 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '046_waste_processed'
down_revision: Union[str, None] = '045_invu_recipe_lines'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    ('processed_value', sa.Numeric(10, 3)),
    ('processed_unit', sa.String(length=5)),
)


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table('waste_records'):
        return
    existing = {c['name'] for c in inspector.get_columns('waste_records')}
    for name, type_ in COLUMNS:
        if name not in existing:
            op.add_column('waste_records', sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if not inspector.has_table('waste_records'):
        return
    existing = {c['name'] for c in inspector.get_columns('waste_records')}
    for name, _ in reversed(COLUMNS):
        if name in existing:
            op.drop_column('waste_records', name)
