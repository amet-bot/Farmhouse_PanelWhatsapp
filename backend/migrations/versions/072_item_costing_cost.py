"""item_costing_cost

Costo real por insumo (del Excel de costeo de recetas) en columnas propias. Va aparte de
`reference_cost` porque la sincronización con Invu lo reescribe en cada pasada. Solo agrega
columnas: no carga datos (eso lo hace scripts/load_costeo.py, a pedido).

Revision ID: 072_item_costing_cost
Revises: 071_branch_visible_to_customers
Create Date: 2026-10-05 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "072_item_costing_cost"
down_revision: Union[str, None] = "071_branch_visible_to_customers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("inventory_items")}
    if "costing_cost" not in cols:
        op.add_column("inventory_items", sa.Column("costing_cost", sa.Numeric(12, 4), nullable=True))
    if "costing_source" not in cols:
        op.add_column("inventory_items", sa.Column("costing_source", sa.String(80), nullable=True))
    if "costing_updated_at" not in cols:
        op.add_column("inventory_items", sa.Column("costing_updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("inventory_items", "costing_updated_at")
    op.drop_column("inventory_items", "costing_source")
    op.drop_column("inventory_items", "costing_cost")
