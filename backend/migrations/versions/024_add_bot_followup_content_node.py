"""add bot_followup_message content node to main_intake graph (bot mas persistente)

Revision ID: 024_bot_followup_content_node
Revises: 023_bot_followup_sent_at

Agrega al grafo `main_intake` el nodo editable del mensaje de seguimiento ("¿sigues ahí?") que
manda services/bot_followup.py cuando el cliente se queda callado 5+ minutos, para que sea
editable desde Flujo Visual como el resto del contenido. El bot ya funciona correctamente sin
esta migración (flow_content.get_node_text cae al texto de auto_responses.py si el nodo no
existe en el grafo) — esto es solo para que un admin pueda editarlo desde el panel.

No se conecta a ningún nodo en particular: el seguimiento puede dispararse desde casi cualquier
punto del flujo (después de cualquier mensaje del bot), así que no tiene un único "padre" lógico
como los nodos normales — queda como una tarjeta suelta, igual que "Archivo recibido" o
"Recuperación: modo visita".
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "024_bot_followup_content_node"
down_revision: Union[str, None] = "023_bot_followup_sent_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MAIN_FLOW_KEY = "main_intake"
NODE_ID = "bot_followup_message"

_NEW_NODE = {
    "id": NODE_ID, "type": "message", "x": 1710, "y": 4960, "w": 340,
    "name": "Seguimiento si el cliente calla",
    "text": "¿Sigues ahí? 😊 Cuando quieras seguimos justo donde lo dejamos — cualquier cosa, escríbeme.",
}


def upgrade() -> None:
    bind = op.get_bind()
    bot_flows = sa.table("bot_flows", sa.column("id", sa.Integer), sa.column("key", sa.String), sa.column("graph_json", sa.Text))
    row = bind.execute(sa.select(bot_flows.c.id, bot_flows.c.graph_json).where(bot_flows.c.key == MAIN_FLOW_KEY)).first()
    if not row:
        return

    graph = json.loads(row.graph_json)
    nodes = graph.setdefault("nodes", [])
    if NODE_ID not in {n.get("id") for n in nodes}:
        nodes.append(dict(_NEW_NODE))

    bind.execute(bot_flows.update().where(bot_flows.c.id == row.id).values(graph_json=json.dumps(graph, ensure_ascii=False)))


def downgrade() -> None:
    bind = op.get_bind()
    bot_flows = sa.table("bot_flows", sa.column("id", sa.Integer), sa.column("key", sa.String), sa.column("graph_json", sa.Text))
    row = bind.execute(sa.select(bot_flows.c.id, bot_flows.c.graph_json).where(bot_flows.c.key == MAIN_FLOW_KEY)).first()
    if not row:
        return

    graph = json.loads(row.graph_json)
    graph["nodes"] = [n for n in graph.get("nodes", []) if n.get("id") != NODE_ID]
    bind.execute(bot_flows.update().where(bot_flows.c.id == row.id).values(graph_json=json.dumps(graph, ensure_ascii=False)))
