"""nearest_branch_and_fee_nodes: tarjetas editables de "la más cercana a mí" y la tarifa

- pickup_location_request: pedir la ubicación en retiro/visita para decir cuál queda más cerca.
- nearest_branch_pickup_message: "te queda más cerca X, elige".
- delivery_fee_message: tarifa de delivery por distancia dicha apenas se sabe la sucursal.

Tarjetas sueltas, como en 053-055.

Revision ID: 056_nearest_branch_and_fee_nodes
Revises: 055_delivery_details_and_hours
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "056_nearest_branch_and_fee_nodes"
down_revision: Union[str, None] = "055_delivery_details_and_hours"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MAIN_FLOW_KEY = "main_intake"

_NEW_NODES = [
    {
        "id": "pickup_location_request", "type": "message", "x": 2910, "y": 5420, "w": 340,
        "name": "Retiro: pedir ubicación",
        "text": "Compárteme tu ubicación con el botón de abajo y te digo cuál sucursal te queda más cerca 📍 Si prefieres, escríbeme el nombre de la sucursal.",
    },
    {
        "id": "nearest_branch_pickup_message", "type": "message", "x": 2910, "y": 5650, "w": 340,
        "name": "Retiro: la más cercana",
        "text": "Te queda más cerca *{sucursal}*, a {km} km 📍 Elige dónde prefieres:",
    },
    {
        "id": "delivery_fee_message", "type": "message", "x": 2910, "y": 5880, "w": 340,
        "name": "Tarifa de delivery",
        "text": "El delivery hasta tu ubicación cuesta *${tarifa}* 🛵",
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
    ids = {n.get("id") for n in nodes}
    for node in _NEW_NODES:
        if node["id"] not in ids:
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
