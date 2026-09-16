"""reorganize main_intake node positions (diagrama de flujo en forma de árbol)

Revision ID: 021_reorganize_layout
Revises: 020_chat_order_content_nodes

El layout de la migración 017 (dos columnas fijas, sin relación con las conexiones reales)
hacía que varias conexiones tuvieran que viajar cientos/miles de píxeles entre nodos, cruzando
por encima de tarjetas sin relación — el diagrama se veía "enredado". Peor todavía: las ~22
tarjetas sueltas (cancelar, reiniciar, "no te entendí"…) caían todas en la misma primera fila
que el inicio del flujo, estirando el diagrama a más de 6.000px de ancho.

Esta migración reemplaza SOLO x/y de cada nodo (nunca su texto, tipo, ni sus conexiones) por las
posiciones que produce el botón "Vertical" ya reescrito (ver flow_editor.js): un árbol prolijo
con cada paso centrado sobre sus ramas, y las tarjetas sueltas agrupadas aparte, debajo del
árbol principal. Son exactamente los valores que calcula ese botón para el grafo actual, tomados
de una corrida real en el editor, no recalculados a mano.
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "021_reorganize_layout"
down_revision: Union[str, None] = "020_chat_order_content_nodes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MAIN_FLOW_KEY = "main_intake"

_POSITIONS = {
    # Árbol principal: arranca en el disparador y baja abriéndose por ramas.
    "trigger": (900, 40), "main_welcome": (900, 229), "order_type_question": (900, 445),
    "branch_selection_delivery_body": (420, 854), "branch_selection_pickup_body": (804, 854),
    "branch_delivery_opening": (420, 1122), "menu_link_delivery_body": (420, 1436),
    "branch_pickup_opening": (804, 1122), "menu_link_pickup_body": (804, 1436),
    "payment_ach": (420, 1739), "payment_card": (420, 2110),
    "payment_yappy": (420, 2425), "human_handoff_message": (420, 2658), "end": (420, 2874),
    "corporate_intro": (1380, 854), "corporate_event_type_question": (1380, 1122),
    "corporate_headcount_question": (1380, 1436), "corporate_date_question": (1188, 1739),
    "corporate_location_question": (1188, 2110), "corporate_location_after_combined": (1572, 1739),
    "corporate_closing": (1572, 2425), "corporate_pause_action": (1572, 2658),
    # Anexo: grupos que no cuelgan del disparador, agrupados debajo del árbol.
    "branch_selection_menu_direct_body": (420, 3113), "menu_link_generic_body": (420, 3278),
    "branch_selection_visit_body": (1042, 3113), "branch_visit_opening": (1042, 3295),
    "manager_help_question": (1042, 3460), "manager_assigned_message": (850, 3774),
    "manager_declined_message": (1234, 3774),
    "corporate_invalid_option_retry": (420, 4020), "corporate_headcount_retry": (850, 4020),
    "corporate_date_retry": (1280, 4020), "restart_message": (420, 4213),
    "cancel_message": (850, 4213), "change_order_type_message": (1280, 4213),
    "change_branch_message": (420, 4407), "unknown_main_message": (850, 4407),
    "unknown_order_message": (1280, 4407), "unknown_branch_message": (420, 4618),
    "after_menu_help_question": (850, 4618), "visit_recovery_message": (1280, 4618),
    "attachment_received_message": (420, 4960), "chat_order_intro_question": (850, 4960),
    "chat_order_payment_question": (1280, 4960),
}


def _load_row(bind, bot_flows):
    return bind.execute(sa.select(bot_flows.c.id, bot_flows.c.graph_json).where(bot_flows.c.key == MAIN_FLOW_KEY)).first()


def upgrade() -> None:
    bind = op.get_bind()
    bot_flows = sa.table("bot_flows", sa.column("id", sa.Integer), sa.column("key", sa.String), sa.column("graph_json", sa.Text))
    row = _load_row(bind, bot_flows)
    if not row:
        return

    graph = json.loads(row.graph_json)
    for node in graph.get("nodes", []):
        pos = _POSITIONS.get(node.get("id"))
        if pos:
            node["x"], node["y"] = pos

    bind.execute(bot_flows.update().where(bot_flows.c.id == row.id).values(graph_json=json.dumps(graph, ensure_ascii=False)))


def downgrade() -> None:
    # Las posiciones son puramente visuales (no afectan el comportamiento del bot); no vale la
    # pena guardar el layout de dos columnas anterior solo para poder "deshacer" un reordenado.
    pass
