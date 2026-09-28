"""unit_cost_precision

Costo unitario con 4 decimales (antes 2) en cargamentos, merma, conteos, traslados y el libro
de movimientos. Los insumos de Invu se miden casi todos en gramos o mililitros y cuestan
fracciones de centavo por unidad ($0.0046 el gramo): con 2 decimales se guardaban como $0.00 y
la merma o la diferencia de un conteo salía valuada en cero. Solo se agranda la columna: ningún
valor guardado cambia.

Revision ID: 049_unit_cost_precision
Revises: 048_waste_measured
Create Date: 2026-09-28 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '049_unit_cost_precision'
down_revision: Union[str, None] = '048_waste_measured'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ('shipment_items', 'waste_items', 'stock_count_items', 'transfer_items', 'inventory_movements')


def _cambiar(nuevo, viejo) -> None:
    inspector = sa.inspect(op.get_bind())
    for table in TABLES:
        if not inspector.has_table(table):
            continue
        if 'unit_cost' not in {c['name'] for c in inspector.get_columns(table)}:
            continue
        with op.batch_alter_table(table) as batch:
            batch.alter_column('unit_cost', type_=nuevo, existing_type=viejo, existing_nullable=True)


def upgrade() -> None:
    _cambiar(sa.Numeric(12, 4), sa.Numeric(10, 2))


def downgrade() -> None:
    _cambiar(sa.Numeric(10, 2), sa.Numeric(12, 4))
