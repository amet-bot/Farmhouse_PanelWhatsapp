"""bot_content_nodes_v2: tarjetas editables nuevas en el grafo main_intake

Tres textos nuevos del bot pasan a ser editables desde Flujo Visual:
- closed_now_message: "estamos cerrados, abrimos a las {abre}" antes del menú.
- handoff_wait_message: "seguimos contigo" cuando nadie respondió al cliente tras el handoff.
- faq_context: información libre (parqueo, wifi, alérgenos...) que usa el respaldo de
  preguntas frecuentes con IA (services/faq_bot.py).

Como en 024/025: tarjetas sueltas, sin conexiones; si el grafo no existe no hace nada y el bot
usa los textos de services/auto_responses.py.

Revision ID: 053_bot_content_nodes_v2
Revises: 052_bot_sessions_and_hours
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "053_bot_content_nodes_v2"
down_revision: Union[str, None] = "052_bot_sessions_and_hours"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MAIN_FLOW_KEY = "main_intake"

_NEW_NODES = [
    {
        "id": "closed_now_message", "type": "message", "x": 1710, "y": 5420, "w": 340,
        "name": "Sucursal cerrada ahora",
        "text": "Ahora mismo estamos cerrados 🌙 Abrimos a las {abre}. Igual puedes armar tu pedido desde el menú y programarlo para cuando abramos, y te lo tendremos listo a esa hora.",
    },
    {
        "id": "handoff_wait_message", "type": "message", "x": 1710, "y": 5650, "w": 340,
        "name": "Cliente esperando a una persona (10 min)",
        "text": "Seguimos contigo 🙏 En este momento el equipo está ocupado, pero ya les avisé y una persona te responde por aquí en cuanto se desocupe. Gracias por la paciencia.",
    },
    {
        "id": "faq_context", "type": "message", "x": 1710, "y": 5880, "w": 340,
        "name": "Datos para preguntas frecuentes (IA)",
        "text": "Escribe aquí, en frases cortas, lo que el bot puede responder por su cuenta. Ejemplos:\n- Parqueo: ...\n- Wifi: ...\n- Mascotas: ...\n- Alérgenos / opciones veganas: ...\n- Política de cancelación: ...\n- Reservas: ...",
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
