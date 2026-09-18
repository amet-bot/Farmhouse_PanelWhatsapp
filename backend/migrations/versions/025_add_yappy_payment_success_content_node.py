"""add yappy_payment_success_message content node to main_intake graph

Revision ID: 025_yappy_payment_success_node
Revises: 024_bot_followup_content_node

El cliente recibe este mensaje cuando Yappy confirma de verdad (por el IPN real, con firma
verificada) que su pago se completó (ver routers/payments._notify_customer_payment_success).
Se agrega al grafo `main_intake` para que sea editable desde Flujo Visual como el resto del
contenido. El bot ya funciona correctamente sin esta migración (flow_content.get_node_text cae
al texto de auto_responses.py si el nodo no existe).

No se conecta a ningún nodo en particular: se dispara desde un evento externo (la notificación
de Yappy), no desde un paso anterior del flujo — queda como tarjeta suelta, igual que
"bot_followup_message" y "corporate_catering_handoff".
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "025_yappy_payment_success_node"
down_revision: Union[str, None] = "024_bot_followup_content_node"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MAIN_FLOW_KEY = "main_intake"
NODE_ID = "yappy_payment_success_message"

_NEW_NODE = {
    "id": NODE_ID, "type": "message", "x": 1710, "y": 5187, "w": 340,
    "name": "Pago con Yappy confirmado",
    "text": "¡Pago recibido con éxito! ✅ Tu pedido *{pedido}* ya está confirmado y en preparación. ¡Gracias por tu compra! 🌿",
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
