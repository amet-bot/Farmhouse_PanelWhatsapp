"""add entry_gate node to main_intake graph

Revision ID: 027_entry_gate_node
Revises: 026_after_menu_help_options

El bot ya no abre con el menú de 6 opciones: el primer mensaje de una conversación nueva es un
portón de dos botones ("Usar el asistente" / "Hablar con alguien"), para que quien solo quiere
un humano llegue en un toque. Ver ENTRY_GATE_BUTTONS en services/auto_responses.py y
_send_entry_gate / _step_prompt_entry_when_context_missing en routers/webhooks.py.

Esta migración agrega ese paso al grafo `main_intake` para que su texto y sus dos etiquetas se
puedan editar desde "Flujo visual" como el resto. El bot funciona igual sin ella, porque
services/flow_content.py cae al texto de Python cuando el nodo no existe.

En el diagrama el portón queda entre el inicio y la bienvenida (inicio -> portón; rama 1 ->
bienvenida, rama 2 -> mensaje de atención humana). Se coloca a la derecha del inicio en vez de
reacomodar las ~45 tarjetas del grafo completo: el botón "Vertical" del editor reordena todo si
alguien lo prefiere en columna.
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "027_entry_gate_node"
down_revision: Union[str, None] = "026_after_menu_help_options"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MAIN_FLOW_KEY = "main_intake"
NODE_ID = "entry_gate"

# Las opciones van en el mismo orden que ENTRY_GATE_BUTTONS: get_node_options solo sustituye el
# título en cada posición, los ids ("entry_gate_bot" / "main_human") siguen viviendo en Python.
_NEW_NODE = {
    "id": NODE_ID, "type": "question", "x": 1188, "y": 40, "w": 340,
    "name": "Portón: asistente o persona",
    "text": (
        "{saludo} Soy el asistente de Farmhouse 🌿\n\n"
        "¿Quieres que te ayude yo con tu pedido, o prefieres hablar de una vez con alguien del equipo?"
    ),
    "options": ["Usar el asistente", "Hablar con alguien"],
}

_NEW_CONNS = [
    {"from": "trigger", "fromPort": 0, "to": NODE_ID},
    {"from": NODE_ID, "fromPort": 0, "to": "main_welcome"},
    {"from": NODE_ID, "fromPort": 1, "to": "human_handoff_message"},
]


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


def _save(bind, bot_flows, row_id, graph):
    bind.execute(
        bot_flows.update().where(bot_flows.c.id == row_id).values(graph_json=json.dumps(graph, ensure_ascii=False))
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

    # El inicio apuntaba directo a la bienvenida; ahora pasa por el portón. Se reemplaza esa
    # conexión (y cualquiera ya existente de las nuevas) en vez de acumular duplicados.
    reemplazadas = {(c["from"], c["fromPort"]) for c in _NEW_CONNS}
    conns = [
        c for c in graph.get("conns", [])
        if (c.get("from"), c.get("fromPort", 0)) not in reemplazadas
    ]
    conns.extend(dict(c) for c in _NEW_CONNS)
    graph["conns"] = conns

    _save(bind, bot_flows, row.id, graph)


def downgrade() -> None:
    bind = op.get_bind()
    bot_flows = _table()
    row = _load(bind, bot_flows)
    if not row:
        return

    graph = json.loads(row.graph_json)
    graph["nodes"] = [n for n in graph.get("nodes", []) if n.get("id") != NODE_ID]
    conns = [
        c for c in graph.get("conns", [])
        if c.get("from") != NODE_ID and c.get("to") != NODE_ID
    ]
    # Devuelve el inicio directo a la bienvenida, que es a donde apuntaba antes.
    if any(n.get("id") == "main_welcome" for n in graph.get("nodes", [])):
        conns.append({"from": "trigger", "fromPort": 0, "to": "main_welcome"})
    graph["conns"] = conns

    _save(bind, bot_flows, row.id, graph)
