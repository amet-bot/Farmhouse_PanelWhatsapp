"""merge delivery_place_question + reference en una sola pregunta de dirección

Revision ID: 061_merge_delivery_address_question
Revises: 060_closing_sheet

Simplificación del bot (conversación del 2026-10-01): el sub-flujo de entrega tras la ubicación
hacía dos preguntas seguidas ("¿PH, casa o local?" con lista tocable, y luego "¿cuál es la
referencia?" aparte). Se fusionan en una sola pregunta abierta de dirección completa — ver
services/auto_responses.py (DELIVERY_ADDRESS_QUESTION) y _handle_delivery_intake_step en
routers/webhooks.py.

Actualiza el nodo existente "delivery_place_question" del grafo editable "Flujo visual"
(mismo id, para no perder ninguna personalización de posición/nombre ya guardada): pasa de
"question" con 3 opciones a "message" de texto libre, con el texto nuevo. Mismo patrón que la
migración 026 (actualiza contenido de un nodo existente para un cambio de comportamiento
deliberado, no uno nuevo). También recorta la frase final de "delivery_details_saved" ("Ahora
sí, arma tu pedido:"), que ahora queda pegada a la info de la sucursal en la misma burbuja en
vez de ser su propio mensaje.

Si el grafo o el nodo no existen todavía (base nueva, o antes de la migración 017/055), no hace
nada — el bot cae al texto de Python de todas formas.
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "061_merge_delivery_address_question"
down_revision: Union[str, None] = "060_closing_sheet"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MAIN_FLOW_KEY = "main_intake"

_NEW_PLACE_QUESTION_TEXT = (
    "Para que el motorizado llegue sin vueltas, cuéntame tu dirección completa (si es PH/edificio, "
    "casa o local, y alguna referencia). Por ejemplo: \"PH Torre Mar, apto 5B\" o \"casa 12, portón negro, frente al parque\"."
)
_OLD_PLACE_QUESTION_TEXT = "Para que el motorizado llegue sin vueltas, ¿a qué tipo de lugar te llevamos el pedido?"
_OLD_PLACE_OPTIONS = ["PH / edificio", "Casa", "Local / oficina"]

_NEW_SAVED_TEXT = "¡Anotado! 📝 {tipo}: {referencia}."
_OLD_SAVED_TEXT = "¡Anotado! 📝 {tipo}: {referencia}. Ahora sí, arma tu pedido:"


def _table():
    return sa.table("bot_flows", sa.column("id", sa.Integer), sa.column("key", sa.String), sa.column("graph_json", sa.Text))


def _load(bind, bot_flows):
    return bind.execute(sa.select(bot_flows.c.id, bot_flows.c.graph_json).where(bot_flows.c.key == MAIN_FLOW_KEY)).first()


def _apply(bind, *, place_type: str, place_text: str, place_drop_options: bool, saved_text: str) -> None:
    bot_flows = _table()
    row = _load(bind, bot_flows)
    if not row:
        return
    graph = json.loads(row.graph_json)
    nodes = graph.get("nodes", [])

    place_node = next((n for n in nodes if n.get("id") == "delivery_place_question"), None)
    if place_node is not None:
        place_node["type"] = place_type
        place_node["text"] = place_text
        if place_drop_options:
            place_node.pop("options", None)
        else:
            place_node["options"] = list(_OLD_PLACE_OPTIONS)

    saved_node = next((n for n in nodes if n.get("id") == "delivery_details_saved"), None)
    if saved_node is not None:
        saved_node["text"] = saved_text

    bind.execute(bot_flows.update().where(bot_flows.c.id == row.id).values(graph_json=json.dumps(graph, ensure_ascii=False)))


def upgrade() -> None:
    _apply(
        op.get_bind(),
        place_type="message", place_text=_NEW_PLACE_QUESTION_TEXT, place_drop_options=True,
        saved_text=_NEW_SAVED_TEXT,
    )


def downgrade() -> None:
    _apply(
        op.get_bind(),
        place_type="question", place_text=_OLD_PLACE_QUESTION_TEXT, place_drop_options=False,
        saved_text=_OLD_SAVED_TEXT,
    )
