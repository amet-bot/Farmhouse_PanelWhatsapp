"""recetas cargadas (farmhouse-app): recetas, líneas, emparejamiento de ingredientes, platos y precios

Revision ID: 065_local_recipes
Revises: 064_task_photos
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "065_local_recipes"
down_revision: Union[str, None] = "064_task_photos"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("local_recipes"):
        op.create_table(
            "local_recipes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("kind", sa.String(20), nullable=False, server_default="plato"),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("name_norm", sa.String(200), nullable=False),
            sa.Column("category", sa.String(80), nullable=True),
            sa.Column("sale_price", sa.Numeric(10, 2), nullable=True),
            sa.Column("ref_cost", sa.Numeric(12, 4), nullable=True),
            sa.Column("ref_food_cost_pct", sa.Numeric(8, 2), nullable=True),
            sa.Column("yield_weight_g", sa.Numeric(12, 3), nullable=True),
            sa.Column("yield_portions", sa.Numeric(10, 2), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("source", sa.String(40), nullable=False, server_default="farmhouse_app"),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_local_recipe_kind_norm", "local_recipes", ["kind", "name_norm"], unique=True)
    if not insp.has_table("local_recipe_lines"):
        op.create_table(
            "local_recipe_lines",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("recipe_id", sa.Integer(), sa.ForeignKey("local_recipes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("ingredient_name", sa.String(200), nullable=False),
            sa.Column("ingredient_norm", sa.String(200), nullable=False),
            sa.Column("quantity", sa.Numeric(12, 4), nullable=False),
            sa.Column("unit", sa.String(20), nullable=True),
        )
        op.create_index("ix_local_recipe_lines_recipe_id", "local_recipe_lines", ["recipe_id"])
        op.create_index("ix_local_recipe_lines_ingredient_norm", "local_recipe_lines", ["ingredient_norm"])
    if not insp.has_table("ingredient_aliases"):
        op.create_table(
            "ingredient_aliases",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("name_norm", sa.String(200), nullable=False, unique=True),
            sa.Column("inventory_item_id", sa.Integer(), sa.ForeignKey("inventory_items.id", ondelete="SET NULL"), nullable=True),
            sa.Column("ignored", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("auto", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if not insp.has_table("recipe_dish_links"):
        op.create_table(
            "recipe_dish_links",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("dish_name", sa.String(200), nullable=False),
            sa.Column("dish_norm", sa.String(200), nullable=False, unique=True),
            sa.Column("recipe_id", sa.Integer(), sa.ForeignKey("local_recipes.id", ondelete="CASCADE"), nullable=True),
            sa.Column("auto", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if not insp.has_table("ingredient_prices"):
        op.create_table(
            "ingredient_prices",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("ingredient_name", sa.String(200), nullable=False),
            sa.Column("ingredient_norm", sa.String(200), nullable=False),
            sa.Column("category", sa.String(80), nullable=True),
            sa.Column("brand", sa.String(120), nullable=True),
            sa.Column("package_grams", sa.Numeric(12, 3), nullable=True),
            sa.Column("supplier", sa.String(150), nullable=True),
            sa.Column("price", sa.Numeric(12, 4), nullable=False),
            sa.Column("source", sa.String(40), nullable=False, server_default="farmhouse_app"),
            sa.Column("imported_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_ingredient_prices_norm", "ingredient_prices", ["ingredient_norm"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for t in ("ingredient_prices", "recipe_dish_links", "ingredient_aliases", "local_recipe_lines", "local_recipes"):
        if insp.has_table(t):
            op.drop_table(t)
