"""delivery_details_and_hours

- Conversación: paso y datos de entrega que el bot pregunta tras la ubicación (PH / casa /
  local y la referencia), para el resumen del handoff y el contacto.
- Horarios reales: todas las sucursales abren a las 8:00 AM, salvo Vía Porras y Obarrio que
  abren a las 6:00 AM; cierran 9:30 PM. Solo se escribe donde no había horario cargado.
- Tarjetas editables nuevas en Flujo Visual para las preguntas de entrega.

Revision ID: 055_delivery_details_and_hours
Revises: 054_delivery_location_nodes
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "055_delivery_details_and_hours"
down_revision: Union[str, None] = "054_delivery_location_nodes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MAIN_FLOW_KEY = "main_intake"

_COLUMNS = [
    sa.Column("delivery_intake_step", sa.Integer(), nullable=True),
    sa.Column("delivery_place_type", sa.String(20), nullable=True),
    sa.Column("delivery_reference", sa.String(300), nullable=True),
]

_NEW_NODES = [
    {
        "id": "location_described", "type": "message", "x": 2510, "y": 5420, "w": 340,
        "name": "Ubicación en palabras",
        "text": "Te ubico en *{lugar}* 🗺️",
    },
    {
        "id": "delivery_place_question", "type": "question", "x": 2510, "y": 5650, "w": 340,
        "name": "¿PH, casa o local?",
        "text": "Para que el motorizado llegue sin vueltas, ¿a qué tipo de lugar te llevamos el pedido?",
        "options": ["PH / edificio", "Casa", "Local / oficina"],
    },
    {
        "id": "delivery_reference_question_ph", "type": "message", "x": 2510, "y": 5880, "w": 340,
        "name": "Referencia: PH",
        "text": "¿Cómo se llama el PH y cuál es el apartamento? Por ejemplo: PH Torre Mar, apto 5B.",
    },
    {
        "id": "delivery_reference_question_casa", "type": "message", "x": 2510, "y": 6110, "w": 340,
        "name": "Referencia: casa",
        "text": "¿Número de casa y alguna referencia? Por ejemplo: casa 12, portón negro, frente al parque.",
    },
    {
        "id": "delivery_reference_question_local", "type": "message", "x": 2510, "y": 6340, "w": 340,
        "name": "Referencia: local u oficina",
        "text": "¿Cómo se llama el local u oficina y en qué piso está? Por ejemplo: Oficinas Delta, piso 3.",
    },
    {
        "id": "delivery_details_saved", "type": "message", "x": 2510, "y": 6570, "w": 340,
        "name": "Entrega anotada",
        "text": "¡Anotado! 📝 {tipo}: {referencia}. Ahora sí, arma tu pedido:",
    },
]


def _table():
    return sa.table("bot_flows", sa.column("id", sa.Integer), sa.column("key", sa.String), sa.column("graph_json", sa.Text))


def upgrade() -> None:
    bind = op.get_bind()
    have = {c["name"] for c in sa.inspect(bind).get_columns("conversations")}
    for col in _COLUMNS:
        if col.name not in have:
            op.add_column("conversations", col)

    branches = sa.table("branches", sa.column("code", sa.String), sa.column("opens_at", sa.String), sa.column("closes_at", sa.String))
    bind.execute(branches.update().where(branches.c.opens_at.is_(None), branches.c.code.in_(["VP", "OBR"])).values(opens_at="06:00"))
    bind.execute(branches.update().where(branches.c.opens_at.is_(None), branches.c.code != "CAT").values(opens_at="08:00"))
    bind.execute(branches.update().where(branches.c.closes_at.is_(None), branches.c.code != "CAT").values(closes_at="21:30"))

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
    have = {c["name"] for c in sa.inspect(bind).get_columns("conversations")}
    for col in _COLUMNS:
        if col.name in have:
            op.drop_column("conversations", col.name)
    bot_flows = _table()
    row = bind.execute(sa.select(bot_flows.c.id, bot_flows.c.graph_json).where(bot_flows.c.key == MAIN_FLOW_KEY)).first()
    if not row:
        return
    graph = json.loads(row.graph_json)
    drop = {n["id"] for n in _NEW_NODES}
    graph["nodes"] = [n for n in graph.get("nodes", []) if n.get("id") not in drop]
    bind.execute(bot_flows.update().where(bot_flows.c.id == row.id).values(graph_json=json.dumps(graph, ensure_ascii=False)))
