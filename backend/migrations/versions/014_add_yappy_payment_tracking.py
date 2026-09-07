"""add Yappy payment tracking

Revision ID: 014_yappy_payments
Revises: 013_delivery_map
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "014_yappy_payments"
down_revision: Union[str, None] = "013_delivery_map"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    columns = _cols("orders")
    if "payment_status" not in columns:
        op.add_column(
            "orders",
            sa.Column(
                "payment_status",
                sa.String(length=30),
                nullable=False,
                server_default="pending",
            ),
        )
    if "payment_reference" not in columns:
        op.add_column(
            "orders",
            sa.Column("payment_reference", sa.String(length=100), nullable=True),
        )
    if "payment_confirmation_number" not in columns:
        op.add_column(
            "orders",
            sa.Column(
                "payment_confirmation_number", sa.String(length=100), nullable=True
            ),
        )


def downgrade() -> None:
    for name in ("payment_confirmation_number", "payment_reference", "payment_status"):
        if name in _cols("orders"):
            op.drop_column("orders", name)
