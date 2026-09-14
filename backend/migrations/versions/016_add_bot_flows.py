"""add_bot_flows

Revision ID: 016_add_bot_flows
Revises: 015_conversation_phone_id

Tabla para la nueva pestaña "Flujo visual" del panel central: guarda un diagrama editable
(nodos + conexiones) como JSON. Se siembra con UNA fila, `main_intake`, que VISUALIZA el
camino principal actual del bot (bienvenida -> tipo de pedido -> sucursal -> menú digital ->
confirmación -> método de pago -> pausa/handoff), usando el copy real de
services/auto_responses.py.

IMPORTANTE — esto es deliberadamente solo un diagrama de referencia + editor, no una réplica
1:1 del bot real:
- El bot real (routers/webhooks.py::_process_auto_flow_background) es MUCHO más complejo:
  sucursal dinámica entre 5 sucursales, intenciones universales que interrumpen desde
  cualquier punto (reiniciar/cancelar/cambiar sucursal/atrás), y un sub-flujo anidado de 4
  preguntas para pedidos corporativos con su propio retroceso. Nada de eso se modela aquí.
- El nodo de "pregunta" del editor limita a 3 opciones (límite real de botones de WhatsApp),
  así que los nodos de tipo de pedido y sucursal muestran solo 3 de las opciones reales
  (la lista real de WhatsApp permite hasta 10) — se anota explícitamente en el texto del nodo.
- Esta migración es solo de datos/estructura. El bot real todavía NO ejecuta este diagrama
  (no existe ningún flow_engine.py conectado a webhooks.py en este panel) — conectarlo es un
  paso deliberadamente aparte, no incluido aquí.
"""
import json
from datetime import datetime, timezone
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '016_add_bot_flows'
down_revision: Union[str, None] = '015_conversation_phone_id'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _main_intake_graph() -> dict:
    return {
        "nodes": [
            {"id": "n1", "type": "trigger", "x": 520, "y": 20, "w": 190,
             "name": "Cliente escribe",
             "text": "Se activa con el primer mensaje del cliente."},

            {"id": "n2", "type": "message", "x": 460, "y": 186, "w": 340,
             "name": "Bienvenida principal",
             "text": ("¡Hola! 👋 Soy el asistente de Farmhouse 🌿\n\n"
                       "¿Qué te gustaría hacer hoy? También puedes escribirme con tus propias palabras.")},

            {"id": "n3", "type": "question", "x": 420, "y": 420, "w": 420,
             "name": "Tipo de pedido (lista real: 6 opciones)",
             "text": ("¡Claro! ¿Cómo quieres recibir tu pedido?\n\n"
                       "Nota: el mensaje real es una LISTA de WhatsApp con 6 opciones "
                       "(Ver el menú y pedir, Delivery, Retiro en local, Evento o empresa, "
                       "Ver sucursales, Hablar con alguien) — el editor solo permite mostrar 3 "
                       "por nodo, aquí se muestran las 3 principales del camino de pedido."),
             "options": ["Delivery", "Retiro en local", "Evento o empresa"]},

            {"id": "n4", "type": "question", "x": 420, "y": 680, "w": 420,
             "name": "Selección de sucursal (5 reales)",
             "text": ("¿Desde cuál sucursal deseas pedir?\n\n"
                       "Nota: son 5 sucursales reales (Costa del Este, San Francisco, Clayton, "
                       "Obarrio y Vía Porras) cargadas dinámicamente desde la tabla de "
                       "sucursales — aquí se muestran solo 3 a modo de ejemplo."),
             "options": ["Costa del Este", "San Francisco", "Clayton"]},

            {"id": "n5", "type": "message", "x": 460, "y": 940, "w": 340,
             "name": "Menú Digital de la sucursal",
             "text": ("🍽️ Aquí tienes nuestro Menú Digital para armar tu pedido desde Farmhouse.\n\n"
                       "Elige tus Bowls, Ensaladas, Toasties o Smoothies favoritos y envíanos tu orden en 1 clic.")},

            {"id": "n6", "type": "message", "x": 460, "y": 1140, "w": 340,
             "name": "Confirmación de pedido recibido",
             "text": ("¡Gracias por tu pedido! Ya lo tenemos registrado 🌿\n\n"
                       "El equipo revisará tu pedido, confirmará el costo/dirección si aplica "
                       "y coordinará el pago contigo.")},

            {"id": "n7", "type": "question", "x": 420, "y": 1350, "w": 420,
             "name": "Método de pago",
             "text": "¿Cómo prefieres pagar?",
             "options": ["ACH / Transferencia", "Tarjeta", "Yappy"]},

            {"id": "n8", "type": "action", "x": 470, "y": 1580, "w": 300,
             "name": "Pausar y avisar al equipo",
             "kind": "Pausar automatización y notificar al equipo",
             "note": "Detiene al bot en esta conversación para que un agente humano de la sucursal continúe."},

            {"id": "n9", "type": "end", "x": 520, "y": 1750, "w": 200, "name": "Fin: con el equipo"},
        ],
        "conns": [
            {"from": "n1", "fromPort": 0, "to": "n2"},
            {"from": "n2", "fromPort": 0, "to": "n3"},
            {"from": "n3", "fromPort": 0, "to": "n4"},
            {"from": "n3", "fromPort": 1, "to": "n4"},
            {"from": "n3", "fromPort": 2, "to": "n4"},
            {"from": "n4", "fromPort": 0, "to": "n5"},
            {"from": "n4", "fromPort": 1, "to": "n5"},
            {"from": "n4", "fromPort": 2, "to": "n5"},
            {"from": "n5", "fromPort": 0, "to": "n6"},
            {"from": "n6", "fromPort": 0, "to": "n7"},
            {"from": "n7", "fromPort": 0, "to": "n8"},
            {"from": "n7", "fromPort": 1, "to": "n8"},
            {"from": "n7", "fromPort": 2, "to": "n8"},
            {"from": "n8", "fromPort": 0, "to": "n9"},
        ],
    }


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('bot_flows'):
        op.create_table(
            'bot_flows',
            sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('key', sa.String(length=50), nullable=False),
            sa.Column('name', sa.String(length=150), nullable=False),
            sa.Column('graph_json', sa.Text(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.Column('updated_by_id', sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(['updated_by_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('key'),
        )
        op.create_index(op.f('ix_bot_flows_key'), 'bot_flows', ['key'], unique=True)

    bot_flows = sa.table(
        'bot_flows',
        sa.column('key', sa.String),
        sa.column('name', sa.String),
        sa.column('graph_json', sa.Text),
        sa.column('updated_at', sa.DateTime),
    )
    existing = bind.execute(sa.text("SELECT id FROM bot_flows WHERE `key` = 'main_intake'")).first()
    if not existing:
        op.bulk_insert(bot_flows, [{
            'key': 'main_intake',
            'name': 'Camino principal (referencia visual)',
            'graph_json': json.dumps(_main_intake_graph(), ensure_ascii=False),
            'updated_at': datetime.now(timezone.utc),
        }])


def downgrade() -> None:
    op.drop_table('bot_flows')
