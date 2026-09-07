"""add delivery map, customer address and scheduling fields

Revision ID: 013_delivery_map
Revises: 012_add_corporate_intake_fields
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "013_delivery_map"
down_revision: Union[str, None] = "012_add_corporate_intake_fields"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _add(table: str, column: sa.Column) -> None:
    if column.name not in _cols(table):
        op.add_column(table, column)


def upgrade() -> None:
    _add("branches", sa.Column("address", sa.String(length=255), nullable=True))
    _add("branches", sa.Column("latitude", sa.Numeric(10, 7), nullable=True))
    _add("branches", sa.Column("longitude", sa.Numeric(10, 7), nullable=True))
    _add("branches", sa.Column("accepts_delivery", sa.Boolean(), nullable=False, server_default=sa.true()))

    _add("contacts", sa.Column("address", sa.String(length=500), nullable=True))
    _add("contacts", sa.Column("building_or_house", sa.String(length=150), nullable=True))
    _add("contacts", sa.Column("floor_or_unit", sa.String(length=100), nullable=True))
    _add("contacts", sa.Column("address_reference", sa.String(length=300), nullable=True))
    _add("contacts", sa.Column("latitude", sa.Numeric(10, 7), nullable=True))
    _add("contacts", sa.Column("longitude", sa.Numeric(10, 7), nullable=True))

    _add("orders", sa.Column("delivery_distance_km", sa.Numeric(8, 2), nullable=True))
    _add("orders", sa.Column("delivery_latitude", sa.Numeric(10, 7), nullable=True))
    _add("orders", sa.Column("delivery_longitude", sa.Numeric(10, 7), nullable=True))
    _add("orders", sa.Column("fulfillment_type", sa.String(length=20), nullable=False, server_default="asap"))
    _add("orders", sa.Column("scheduled_for", sa.DateTime(), nullable=True))


def downgrade() -> None:
    for table, names in (
        ("orders", ["scheduled_for", "fulfillment_type", "delivery_longitude", "delivery_latitude", "delivery_distance_km"]),
        ("contacts", ["longitude", "latitude", "address_reference", "floor_or_unit", "building_or_house", "address"]),
        ("branches", ["accepts_delivery", "longitude", "latitude", "address"]),
    ):
        for name in names:
            if name in _cols(table):
                op.drop_column(table, name)
