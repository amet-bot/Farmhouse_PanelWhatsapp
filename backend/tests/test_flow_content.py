"""
Fase 2 de "Flujo visual": el diagrama `main_intake` controla de verdad el CONTENIDO que el
bot le manda a un cliente real (services/flow_content.py), aunque el CONTROL del flujo
(interrupciones, sucursales, el intake corporativo) se quede en routers/webhooks.py tal como
estaba. Estas pruebas verifican el contrato de respaldo: sin nada editado el comportamiento es
idéntico al de siempre, y una edición real sí cambia el mensaje saliente.
"""
import json

import pytest
from config import settings
from conftest import TestingSessionLocal
from models.bot_flow import BotFlow
from models.contact import Contact
from models.conversation import Conversation
from models.message import Message
from services.auto_responses import MAIN_WELCOME_BODY
from services.flow_content import get_node_options, get_node_text


@pytest.fixture(autouse=True)
def setup_webhook_env(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_MODE", "mock")
    monkeypatch.setattr("routers.webhooks.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("routers.webhooks.notify_branch_new_message", lambda *a, **k: None)


def _post_text(client, phone, wamid, text):
    return client.post("/api/webhooks/whatsapp", json={
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA_ID", "changes": [{
            "value": {"messaging_product": "whatsapp", "messages": [
                {"from": phone, "id": wamid, "timestamp": "1725500000", "type": "text", "text": {"body": text}},
            ]},
            "field": "messages",
        }]}],
    })


def _post_button(client, phone, wamid, button_id, title):
    return client.post("/api/webhooks/whatsapp", json={
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA_ID", "changes": [{
            "value": {"messaging_product": "whatsapp", "messages": [
                {"from": phone, "id": wamid, "timestamp": "1725500000", "type": "interactive",
                 "interactive": {"button_reply": {"id": button_id, "title": title}}},
            ]},
            "field": "messages",
        }]}],
    })


def test_sin_grafo_sembrado_get_node_text_devuelve_el_respaldo(db_session):
    # No existe ninguna fila 'main_intake' en bot_flows (los tests usan create_all(), no las
    # migraciones/seeds de Alembic) -- este es exactamente el escenario de los 22 tests de
    # conversación existentes, que nunca deben verse afectados por este módulo.
    assert get_node_text(db_session, "main_welcome", "respaldo") == "respaldo"
    assert get_node_options(db_session, "order_type_question", ["A", "B"]) == ["A", "B"]


def test_editar_el_nodo_de_bienvenida_cambia_el_mensaje_real(client, clayton_branch, db_session):
    flow = BotFlow(
        key="main_intake",
        name="Camino principal",
        graph_json=json.dumps({
            "nodes": [
                {"id": "trigger", "type": "trigger", "x": 0, "y": 0, "w": 170, "name": "Cliente escribe"},
                {"id": "main_welcome", "type": "message", "x": 0, "y": 120, "w": 320, "name": "Bienvenida",
                 "text": "¡Bienvenido a Farmhouse Test! Este texto viene editado desde el panel."},
            ],
            "conns": [{"from": "trigger", "fromPort": 0, "to": "main_welcome"}],
        }, ensure_ascii=False),
    )
    db_session.add(flow)
    db_session.commit()

    # El primer mensaje ya no es la bienvenida sino el portón asistente/persona; la bienvenida
    # editada llega al tocar "Usar el asistente".
    assert _post_text(client, "50769995555", "wamid.FLOWCONTENT01", "Hola").status_code == 200
    assert _post_button(
        client, "50769995555", "wamid.FLOWCONTENT02", "entry_gate_bot", "Usar el asistente"
    ).status_code == 200

    contact = db_session.query(Contact).filter(Contact.phone.contains("69995555")).first()
    conv = db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()
    msgs = db_session.query(Message).filter(Message.conversation_id == conv.id, Message.direction == "outgoing").all()
    joined = "\n".join(m.content for m in msgs)
    assert "Este texto viene editado desde el panel" in joined
    assert MAIN_WELCOME_BODY not in joined


def test_grafo_con_opciones_de_mas_o_de_menos_se_ignora_y_usa_el_respaldo(db_session):
    flow = BotFlow(
        key="main_intake",
        name="Camino principal",
        graph_json=json.dumps({
            "nodes": [
                {"id": "trigger", "type": "trigger", "x": 0, "y": 0, "w": 170, "name": "Cliente escribe"},
                {"id": "order_type_question", "type": "question", "x": 0, "y": 120, "w": 320,
                 "name": "Tipo de pedido", "text": "x",
                 "options": ["Solo una opcion"]},
            ],
            "conns": [],
        }, ensure_ascii=False),
    )
    db_session.add(flow)
    db_session.commit()

    fallback = ["Delivery", "Retiro en local", "Evento / empresa"]
    assert get_node_options(db_session, "order_type_question", fallback) == fallback


def test_grafo_invalido_no_rompe_nada(db_session):
    flow = BotFlow(key="main_intake", name="Camino principal", graph_json="{no es json valido")
    db_session.add(flow)
    db_session.commit()

    assert get_node_text(db_session, "main_welcome", "respaldo") == "respaldo"
    assert get_node_options(db_session, "order_type_question", ["A", "B"]) == ["A", "B"]
