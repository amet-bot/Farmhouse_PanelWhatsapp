"""delivery_location_nodes: tarjetas editables del delivery por ubicación

Al elegir delivery el bot pide la ubicación con el botón nativo de WhatsApp y recomienda la
sucursal más cercana (ver _step_handle_shared_location en routers/webhooks.py). Sus tres
textos pasan a ser editables desde Flujo Visual. Tarjetas sueltas, como en 053.

Revision ID: 054_delivery_location_nodes
Revises: 053_bot_content_nodes_v2
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "054_delivery_location_nodes"
down_revision: Union[str, None] = "053_bot_content_nodes_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MAIN_FLOW_KEY = "main_intake"

_NEW_NODES = [
    {
        "id": "delivery_location_request", "type": "message", "x": 2110, "y": 5420, "w": 340,
        "name": "Delivery: pedir ubicación",
        "text": "Delivery, entendido 🛵 Compárteme tu ubicación con el botón de abajo y te recomiendo la sucursal más cercana. Si prefieres, escríbeme el nombre de la sucursal.",
    },
    {
        "id": "nearest_branch_message", "type": "message", "x": 2110, "y": 5650, "w": 340,
        "name": "Sucursal más cercana",
        "text": "Tu sucursal más cercana es *{sucursal}*, a {km} km 📍 Desde ahí te llevamos el pedido.",
    },
    {
        "id": "delivery_out_of_range_message", "type": "message", "x": 2110, "y": 5880, "w": 340,
        "name": "Fuera del alcance de delivery",
        "text": "Estás a {km} km de nuestra sucursal más cercana (*{sucursal}*) y por ahora el delivery llega hasta {max_km} km 😔 Si quieres, puedes pedir para retirar en la sucursal que te quede mejor:",
    },
]


def _table():
    return sa.table("bot_flows", sa.column("id", sa.Integer), sa.column("key", sa.String), sa.column("graph_json", sa.Text))


def upgrade() -> None:
    bind = op.get_bind()
    bot_flows = _table()
    row = bind.execute(sa.select(bot_flows.c.id, bot_flows.c.graph_json).where(bot_flows.c.key == MAIN_FLOW_KEY)).first()
    if not row:
        return
    graph = json.loads(row.graph_json)
    nodes = graph.setdefault("nodes", [])
    have = {n.get("id") for n in nodes}
    for node in _NEW_NODES:
        if node["id"] not in have:
            nodes.append(dict(node))
    bind.execute(bot_flows.update().where(bot_flows.c.id == row.id).values(graph_json=json.dumps(graph, ensure_ascii=False)))


def downgrade() -> None:
    bind = op.get_bind()
    bot_flows = _table()
    row = bind.execute(sa.select(bot_flows.c.id, bot_flows.c.graph_json).where(bot_flows.c.key == MAIN_FLOW_KEY)).first()
    if not row:
        return
    graph = json.loads(row.graph_json)
    drop = {n["id"] for n in _NEW_NODES}
    graph["nodes"] = [n for n in graph.get("nodes", []) if n.get("id") not in drop]
    bind.execute(bot_flows.update().where(bot_flows.c.id == row.id).values(graph_json=json.dumps(graph, ensure_ascii=False)))
