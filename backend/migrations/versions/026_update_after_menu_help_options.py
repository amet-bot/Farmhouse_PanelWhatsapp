"""update after_menu_help_question options: horarios/ubicacion en vez de abrir el menu

Revision ID: 026_after_menu_help_options
Revises: 025_yappy_payment_success_node

"Abrir el menú" se quitó de la lista "¿algo más?" que sigue al link del Menú Digital: se manda
en el mismo turno que el botón del menú, así que repetirlo ahí se sentía redundante. En su lugar
van las dos preguntas más comunes según el negocio (horario y ubicación) — ver
services/auto_responses.py (AFTER_MENU_HELP_BUTTONS) y
routers/webhooks.py (_step_handle_branch_info_request).

Solo actualiza el arreglo `options` del nodo (mismo texto, mismo tipo "question"); si el nodo no
existe todavía (base de datos nueva, antes de la migración 017) no hace nada — el bot cae al
texto de Python de todas formas.
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "026_after_menu_help_options"
down_revision: Union[str, None] = "025_yappy_payment_success_node"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MAIN_FLOW_KEY = "main_intake"
NODE_ID = "after_menu_help_question"

_OLD_OPTIONS = ["Abrir el menú", "Cambiar sucursal", "Hablar con alguien"]
_NEW_OPTIONS = ["Ver horarios", "Ver ubicación", "Cambiar sucursal", "Hablar con alguien"]


def _table():
    return sa.table(
        "bot_flows",
        sa.column("id", sa.Integer),
        sa.column("key", sa.String),
        sa.column("graph_json", sa.Text),
    )


def _load(bind, bot_flows):
    return bind.execute(
        sa.select(bot_flows.c.id, bot_flows.c.graph_json).where(bot_flows.c.key == MAIN_FLOW_KEY)
    ).first()


def _set_options(bind, options: list) -> None:
    bot_flows = _table()
    row = _load(bind, bot_flows)
    if not row:
        return
    graph = json.loads(row.graph_json)
    node = next((n for n in graph.get("nodes", []) if n.get("id") == NODE_ID), None)
    if not node:
        return
    node["options"] = list(options)
    bind.execute(
        bot_flows.update().where(bot_flows.c.id == row.id).values(graph_json=json.dumps(graph, ensure_ascii=False))
    )


def upgrade() -> None:
    _set_options(op.get_bind(), _NEW_OPTIONS)


def downgrade() -> None:
    _set_options(op.get_bind(), _OLD_OPTIONS)
