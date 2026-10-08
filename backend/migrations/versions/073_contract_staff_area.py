"""contract_staff_area

Área del colaborador en su contrato: "Sucursal" o "Administrativo". Decide qué plantilla de Word
se exporta (la de sucursal es de contrato Definido; la de administración, de Indefinido). Los
contratos que ya existen quedan como "Sucursal".

Revision ID: 073_contract_staff_area
Revises: 072_item_costing_cost
Create Date: 2026-10-08 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "073_contract_staff_area"
down_revision: Union[str, None] = "072_item_costing_cost"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("employee_contracts")}
    if "staff_area" not in cols:
        op.add_column("employee_contracts", sa.Column("staff_area", sa.String(20), nullable=False, server_default="Sucursal"))


def downgrade() -> None:
    op.drop_column("employee_contracts", "staff_area")
