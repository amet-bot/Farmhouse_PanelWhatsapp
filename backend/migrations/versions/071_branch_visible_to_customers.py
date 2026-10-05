"""sucursales ocultas a los clientes (p. ej. la oficina "Bloc")

Revision ID: 071_branch_visible_to_customers
Revises: 070_employee_intakes

Pedido del negocio (2026-10-05): la oficina "Bloc" existe como sucursal en el sistema (para asignarle
usuarios como Recursos Humanos) pero no vende, así que no debe aparecer a los clientes en el menú digital
ni en el bot de WhatsApp, ni recibir pedidos públicos. Por defecto todas las sucursales existentes quedan
visibles (no cambia nada para ellas). Ver models.branch.Branch.visible_to_customers.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "071_branch_visible_to_customers"
down_revision: Union[str, None] = "070_employee_intakes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind) -> bool:
    return any(c["name"] == "visible_to_customers" for c in sa.inspect(bind).get_columns("branches"))


def upgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        return
    op.add_column("branches", sa.Column("visible_to_customers", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind):
        op.drop_column("branches", "visible_to_customers")
