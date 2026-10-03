"""item_density

inventory_items.grams_per_ml: cuántos gramos pesa 1 ml del insumo. Con eso una receta que pide
"50 g de agua de pipa" se puede descontar de un insumo que se mide en mililitros. Nullable:
nada de lo ya cargado cambia.

Revision ID: 066_item_density
Revises: 065_local_recipes
Create Date: 2026-10-03 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "066_item_density"
down_revision: Union[str, None] = "065_local_recipes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("inventory_items") and "grams_per_ml" not in {c["name"] for c in inspector.get_columns("inventory_items")}:
        op.add_column("inventory_items", sa.Column("grams_per_ml", sa.Numeric(8, 4), nullable=True))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("inventory_items") and "grams_per_ml" in {c["name"] for c in inspector.get_columns("inventory_items")}:
        op.drop_column("inventory_items", "grams_per_ml")
