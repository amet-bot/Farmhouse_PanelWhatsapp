"""ops_center: incidencias asignables y solicitudes con aprobación

Centro de operación multisucursal: las incidencias se pueden asignar a alguien y las
solicitudes de insumos pasan por aprobación (open -> approved -> fulfilled). Solo columnas
nuevas y nulas; los estados existentes no cambian.

Revision ID: 057_ops_center
Revises: 056_nearest_branch_and_fee_nodes
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "057_ops_center"
down_revision: Union[str, None] = "056_nearest_branch_and_fee_nodes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = {
    "incidents": [
        sa.Column("assigned_to_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    ],
    "supply_requests": [
        sa.Column("approved_by_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
    ],
}


def _existing(table: str) -> set:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table, columns in _COLUMNS.items():
        have = _existing(table)
        for col in columns:
            if col.name not in have:
                op.add_column(table, col)


def downgrade() -> None:
    for table, columns in _COLUMNS.items():
        have = _existing(table)
        for col in columns:
            if col.name in have:
                op.drop_column(table, col.name)
