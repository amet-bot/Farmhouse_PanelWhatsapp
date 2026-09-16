"""add chat_order_intro_question / chat_order_payment_question nodes to main_intake graph

Revision ID: 020_chat_order_content_nodes
Revises: 019_awaiting_chat_order

Fase 4 (reducir texto libre / "pedir y pagar por chat"): agrega al grafo `main_intake` los 2
nodos nuevos que introduce esta fase, con el texto copiado verbatim de
services/auto_responses.py, para que sean editables desde el panel como el resto. El bot ya
funciona correctamente sin esta migración (services/flow_content.py cae al texto de
auto_responses.py si el nodo no existe en el grafo) — esto es solo para que un admin pueda
editarlos desde Flujo Visual. Se ubican en una columna nueva para no encimarse con nodos
existentes; si la siembra queda visualmente apretada, el botón "Vertical"/"Horizontal" del
editor reacomoda todo (igual que se documentó en la migración 017).
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "020_chat_order_content_nodes"
down_revision: Union[str, None] = "019_awaiting_chat_order"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MAIN_FLOW_KEY = "main_intake"

_NEW_NODES = [
    {
        "id": "chat_order_intro_question", "type": "message", "x": 1460, "y": 20, "w": 340,
        "name": "Pedir por chat (intro)",
        "text": "¡Perfecto! Cuéntame qué te gustaría pedir (platillos y cantidades) y lo dejamos listo para el pago 😊",
    },
    {
        "id": "chat_order_payment_question", "type": "message", "x": 1460, "y": 175, "w": 340,
        "name": "Pedir por chat (pago)",
        "text": "¡Anotado! ¿Cómo prefieres pagar?",
    },
]


def upgrade() -> None:
    bind = op.get_bind()
    bot_flows = sa.table(
        "bot_flows",
        sa.column("id", sa.Integer),
        sa.column("key", sa.String),
        sa.column("graph_json", sa.Text),
    )
    row = bind.execute(sa.select(bot_flows.c.id, bot_flows.c.graph_json).where(bot_flows.c.key == MAIN_FLOW_KEY)).first()
    if not row:
        return

    graph = json.loads(row.graph_json)
    nodes = graph.setdefault("nodes", [])
    existing_ids = {n.get("id") for n in nodes}
    for node in _NEW_NODES:
        if node["id"] not in existing_ids:
            nodes.append(dict(node))

    bind.execute(
        bot_flows.update().where(bot_flows.c.id == row.id).values(graph_json=json.dumps(graph, ensure_ascii=False))
    )


def downgrade() -> None:
    bind = op.get_bind()
    bot_flows = sa.table(
        "bot_flows",
        sa.column("id", sa.Integer),
        sa.column("key", sa.String),
        sa.column("graph_json", sa.Text),
    )
    row = bind.execute(sa.select(bot_flows.c.id, bot_flows.c.graph_json).where(bot_flows.c.key == MAIN_FLOW_KEY)).first()
    if not row:
        return

    graph = json.loads(row.graph_json)
    new_ids = {n["id"] for n in _NEW_NODES}
    graph["nodes"] = [n for n in graph.get("nodes", []) if n.get("id") not in new_ids]

    bind.execute(
        bot_flows.update().where(bot_flows.c.id == row.id).values(graph_json=json.dumps(graph, ensure_ascii=False))
    )
