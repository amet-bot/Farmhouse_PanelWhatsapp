"""add corporate_catering_handoff node to main_intake graph

Revision ID: 022_catering_handoff_node
Revises: 021_reorganize_layout

El bot ya no hace las 4 preguntas del pedido corporativo: al elegir "Evento o empresa" entrega
directamente el número del equipo de catering (ver CORPORATE_INTAKE_ENABLED en
services/auto_responses.py). Esta migración agrega al grafo `main_intake` el nodo con ese texto
para que sea editable desde Flujo Visual como el resto — el bot funciona igual sin ella, porque
services/flow_content.py cae al texto de auto_responses.py cuando el nodo no existe.

Se conecta después de la pregunta "Tipo de pedido" (rama 3, "Evento / empresa") para que en el
diagrama se vea de dónde sale. Se ubica junto a los nodos del tramo corporativo; el botón
"Vertical" del editor reacomoda todo si hiciera falta.
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "022_catering_handoff_node"
down_revision: Union[str, None] = "021_reorganize_layout"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MAIN_FLOW_KEY = "main_intake"
NODE_ID = "corporate_catering_handoff"

_NEW_NODE = {
    "id": NODE_ID, "type": "message", "x": 1380, "y": 854, "w": 340,
    "name": "Evento/empresa: número de catering",
    "text": (
        "¡Perfecto! 🎉 Los pedidos para eventos y empresas los coordina directamente nuestro equipo "
        "de catering, que es quien trabaja con Sol para armarte la propuesta.\n\n"
        "Escríbeles por aquí y te atienden de una vez 👇\n📞 +507 6364-4572\nhttps://wa.me/50763644572\n\n"
        "¡Gracias por pensar en Farmhouse para tu evento! 😊"
    ),
}

# Rama 3 de "Tipo de pedido" = "Evento / empresa" (las opciones se cuentan desde 0).
_NEW_CONN = {"from": "order_type_question", "fromPort": 2, "to": NODE_ID}

# Al cambiar esa rama, el tramo de las 4 preguntas deja de colgar del árbol principal y el nodo
# nuevo ocupa su lugar. Estas son las posiciones que produce el botón "Vertical" del editor con
# el grafo ya rehecho (tomadas de una corrida real, igual que en la migración 021): el tramo
# apagado queda agrupado abajo con el resto de lo que no cuelga del inicio, y ninguna tarjeta se
# encima con otra.
_POSITIONS = {
    "trigger": (804, 40), "main_welcome": (804, 229), "order_type_question": (804, 445),
    "branch_selection_delivery_body": (420, 854), "branch_selection_pickup_body": (804, 854),
    "corporate_catering_handoff": (1188, 854),
    "branch_delivery_opening": (420, 1156), "menu_link_delivery_body": (420, 1338),
    "branch_pickup_opening": (804, 1156), "menu_link_pickup_body": (804, 1338),
    "payment_ach": (420, 1641), "payment_card": (420, 2012), "payment_yappy": (420, 2298),
    "human_handoff_message": (420, 2531), "end": (420, 2747),
    # Debajo del árbol: lo que no cuelga del inicio.
    "branch_selection_menu_direct_body": (420, 2987), "menu_link_generic_body": (420, 3151),
    "branch_selection_visit_body": (1042, 2987), "branch_visit_opening": (1042, 3168),
    "manager_help_question": (1042, 3333), "manager_assigned_message": (850, 3647),
    "manager_declined_message": (1234, 3647),
    # Tramo apagado de las 4 preguntas corporativas.
    "corporate_intro": (612, 3893), "corporate_event_type_question": (612, 4161),
    "corporate_headcount_question": (612, 4475), "corporate_date_question": (420, 4691),
    "corporate_location_question": (420, 4873), "corporate_location_after_combined": (804, 4691),
    "corporate_closing": (804, 5187), "corporate_pause_action": (804, 5404),
    "corporate_invalid_option_retry": (1234, 3893), "corporate_headcount_retry": (420, 5616),
    "corporate_date_retry": (850, 5616),
    "restart_message": (1280, 5616), "cancel_message": (420, 5809),
    "change_order_type_message": (850, 5809), "change_branch_message": (1280, 5809),
    "unknown_main_message": (420, 6003), "unknown_order_message": (850, 6003),
    "unknown_branch_message": (1280, 6003), "after_menu_help_question": (420, 6214),
    "visit_recovery_message": (850, 6214), "attachment_received_message": (1280, 6214),
    "chat_order_intro_question": (420, 6556), "chat_order_payment_question": (850, 6556),
}


def _load(bind, bot_flows):
    return bind.execute(
        sa.select(bot_flows.c.id, bot_flows.c.graph_json).where(bot_flows.c.key == MAIN_FLOW_KEY)
    ).first()


def _table():
    return sa.table(
        "bot_flows",
        sa.column("id", sa.Integer),
        sa.column("key", sa.String),
        sa.column("graph_json", sa.Text),
    )


def upgrade() -> None:
    bind = op.get_bind()
    bot_flows = _table()
    row = _load(bind, bot_flows)
    if not row:
        return

    graph = json.loads(row.graph_json)
    nodes = graph.setdefault("nodes", [])
    if NODE_ID not in {n.get("id") for n in nodes}:
        nodes.append(dict(_NEW_NODE))

    conns = graph.setdefault("conns", [])
    # La rama 3 apuntaba al nodo de la intro corporativa, que ya no se usa: se reemplaza para que
    # el diagrama muestre el camino que el bot recorre hoy.
    conns = [
        c for c in conns
        if not (c.get("from") == _NEW_CONN["from"] and c.get("fromPort") == _NEW_CONN["fromPort"])
    ]
    conns.append(dict(_NEW_CONN))
    graph["conns"] = conns

    for node in nodes:
        pos = _POSITIONS.get(node.get("id"))
        if pos:
            node["x"], node["y"] = pos

    bind.execute(
        bot_flows.update().where(bot_flows.c.id == row.id).values(graph_json=json.dumps(graph, ensure_ascii=False))
    )


def downgrade() -> None:
    bind = op.get_bind()
    bot_flows = _table()
    row = _load(bind, bot_flows)
    if not row:
        return

    graph = json.loads(row.graph_json)
    graph["nodes"] = [n for n in graph.get("nodes", []) if n.get("id") != NODE_ID]
    conns = [c for c in graph.get("conns", []) if c.get("to") != NODE_ID and c.get("from") != NODE_ID]
    # Devuelve la rama "Evento / empresa" a la intro de las 4 preguntas, que es a donde apuntaba
    # antes. Las posiciones no se restauran: son puramente visuales y el botón "Vertical" del
    # editor las vuelve a acomodar solo.
    if any(n.get("id") == "corporate_intro" for n in graph.get("nodes", [])):
        conns.append({"from": "order_type_question", "fromPort": 2, "to": "corporate_intro"})
    graph["conns"] = conns

    bind.execute(
        bot_flows.update().where(bot_flows.c.id == row.id).values(graph_json=json.dumps(graph, ensure_ascii=False))
    )
