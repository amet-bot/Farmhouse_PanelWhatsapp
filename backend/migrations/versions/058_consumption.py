"""consumption: registro de consumo de insumos por el equipo

Dos tablas nuevas (consumption_records / consumption_items), calcadas de la merma: cuánto se
usó de cada insumo en una sucursal, en su unidad, con el último costo conocido. Nada de lo
existente cambia.

Revision ID: 058_consumption
Revises: 057_ops_center
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "058_consumption"
down_revision: Union[str, None] = "057_ops_center"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("consumption_records"):
        op.create_table(
            "consumption_records",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branches.id"), nullable=False),
            sa.Column("recorded_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_consumption_records_id", "consumption_records", ["id"])
        op.create_index("ix_consumption_branch_occurred", "consumption_records", ["branch_id", "occurred_at"])
    if not insp.has_table("consumption_items"):
        op.create_table(
            "consumption_items",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("consumption_record_id", sa.Integer(), sa.ForeignKey("consumption_records.id", ondelete="CASCADE"), nullable=False),
            sa.Column("inventory_item_id", sa.Integer(), sa.ForeignKey("inventory_items.id"), nullable=False),
            sa.Column("quantity", sa.Numeric(10, 3), nullable=False),
            sa.Column("unit_cost", sa.Numeric(12, 4), nullable=True),
        )
        op.create_index("ix_consumption_items_id", "consumption_items", ["id"])
        op.create_index("ix_consumption_item_item", "consumption_items", ["inventory_item_id"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if insp.has_table("consumption_items"):
        op.drop_table("consumption_items")
    if insp.has_table("consumption_records"):
        op.drop_table("consumption_records")
