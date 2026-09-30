"""supply: minimos y pares por sucursal, ordenes de compra con lineas, solicitudes ligadas al catalogo

- item_branch_settings: mínimo, par, proveedor preferido y días de entrega por insumo y sucursal.
- expected_shipment_items: líneas (cantidad y costo) de un cargamento esperado = orden de compra.
- supply_requests: inventory_item_id y quantity opcionales (ligar la solicitud al catálogo).

Revision ID: 059_supply
Revises: 058_consumption
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "059_supply"
down_revision: Union[str, None] = "058_consumption"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("item_branch_settings"):
        op.create_table(
            "item_branch_settings",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("inventory_item_id", sa.Integer(), sa.ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False),
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False),
            sa.Column("min_quantity", sa.Numeric(10, 3), nullable=True),
            sa.Column("par_quantity", sa.Numeric(10, 3), nullable=True),
            sa.Column("supplier_id", sa.Integer(), sa.ForeignKey("suppliers.id", ondelete="SET NULL"), nullable=True),
            sa.Column("lead_days", sa.Integer(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.UniqueConstraint("inventory_item_id", "branch_id", name="uq_item_branch_setting"),
        )
        op.create_index("ix_item_branch_settings_id", "item_branch_settings", ["id"])
        op.create_index("ix_item_branch_setting_branch", "item_branch_settings", ["branch_id"])
    if not insp.has_table("expected_shipment_items"):
        op.create_table(
            "expected_shipment_items",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("expected_shipment_id", sa.Integer(), sa.ForeignKey("expected_shipments.id", ondelete="CASCADE"), nullable=False),
            sa.Column("inventory_item_id", sa.Integer(), sa.ForeignKey("inventory_items.id"), nullable=False),
            sa.Column("quantity", sa.Numeric(10, 3), nullable=False),
            sa.Column("unit_cost", sa.Numeric(12, 4), nullable=True),
        )
        op.create_index("ix_expected_shipment_items_id", "expected_shipment_items", ["id"])
        op.create_index("ix_expected_item_expected", "expected_shipment_items", ["expected_shipment_id"])
    have = {c["name"] for c in insp.get_columns("supply_requests")}
    if "inventory_item_id" not in have:
        op.add_column("supply_requests", sa.Column("inventory_item_id", sa.Integer(), sa.ForeignKey("inventory_items.id", ondelete="SET NULL"), nullable=True))
    if "quantity" not in have:
        op.add_column("supply_requests", sa.Column("quantity", sa.Numeric(10, 3), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    have = {c["name"] for c in insp.get_columns("supply_requests")}
    for col in ("quantity", "inventory_item_id"):
        if col in have:
            op.drop_column("supply_requests", col)
    if insp.has_table("expected_shipment_items"):
        op.drop_table("expected_shipment_items")
    if insp.has_table("item_branch_settings"):
        op.drop_table("item_branch_settings")
