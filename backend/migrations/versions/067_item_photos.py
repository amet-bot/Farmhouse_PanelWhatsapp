"""item_photos

Foto de cada insumo (una por insumo), para la pantalla de merma rápida: se elige el insumo
viéndolo. Tabla nueva; nada de lo ya cargado cambia.

Revision ID: 067_item_photos
Revises: 066_item_density
Create Date: 2026-10-03 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = "067_item_photos"
down_revision: Union[str, None] = "066_item_density"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("item_photos"):
        return
    op.create_table(
        "item_photos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("inventory_item_id", sa.Integer(), sa.ForeignKey("inventory_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_type", sa.String(length=40), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary().with_variant(mysql.MEDIUMBLOB(), "mysql"), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_item_photos_id", "item_photos", ["id"])
    op.create_index("ix_item_photos_inventory_item_id", "item_photos", ["inventory_item_id"], unique=True)


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("item_photos"):
        op.drop_table("item_photos")
