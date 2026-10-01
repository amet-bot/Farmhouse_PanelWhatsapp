"""tareas recurrentes: la regla que crea Tasks solas a hora(s) fija(s) o un día del mes

Revision ID: 063_recurring_tasks
Revises: 062_shorten_bot_messages

Pedido del negocio (2026-10-01): tareas que se repiten siempre igual sin tener que crearlas a
mano cada vez — limpieza de apertura a las 7am en cada local, listas de pares de producción a
las 8am/3pm/8pm, inventario el día 30 de cada mes. Ver models.ops.RecurringTaskTemplate y
services/recurring_tasks.py (el loop que las dispara, mismo patrón que services/supply_alerts.py).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "063_recurring_tasks"
down_revision: Union[str, None] = "062_shorten_bot_messages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if "recurring_task_templates" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "recurring_task_templates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branches.id"), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("frequency", sa.String(20), nullable=False, server_default="daily"),
        sa.Column("times_json", sa.Text(), nullable=False),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_recurring_task_branch_active", "recurring_task_templates", ["branch_id", "active"])


def downgrade() -> None:
    bind = op.get_bind()
    if "recurring_task_templates" in sa.inspect(bind).get_table_names():
        op.drop_index("ix_recurring_task_branch_active", table_name="recurring_task_templates")
        op.drop_table("recurring_task_templates")
