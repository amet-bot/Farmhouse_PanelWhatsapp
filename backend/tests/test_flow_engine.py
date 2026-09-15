"""
El motor de flujo (services/flow_engine.py) es el único tramo del bot donde las CONEXIONES
del grafo `main_intake` deciden de verdad qué paso sigue (el sub-flujo de 4 preguntas de
Pedido Corporativo/Evento) — ver docstring de models/bot_flow.py. Estas pruebas demuestran que
reconectar el diagrama SÍ cambia el comportamiento real del bot, y que un diagrama roto o a
medio editar cae de vuelta al comportamiento de siempre sin romper la conversación.
"""
import json

import pytest

from config import settings
from conftest import TestingSessionLocal
from models.bot_flow import BotFlow
from models.branch import Branch
from models.contact import Contact
from models.conversation import Conversation


@pytest.fixture(autouse=True)
def setup_webhook_env(monkeypatch):
    """Mismo patrón que el resto de tests que ejercitan el webhook (ver test_bot_new_flow.py):
    sin esto, el procesador en segundo plano (routers.webhooks::_process_auto_flow_background)
    usa el SessionLocal real (la base de datos de desarrollo local) en vez de la de pruebas."""
    monkeypatch.setattr(settings, "WHATSAPP_MODE", "mock")
    monkeypatch.setattr("routers.webhooks.SessionLocal", TestingSessionLocal)


def _seed_graph(db_session, conns_override):
    """Grafo mínimo con los 5 nodos del sub-flujo corporativo, usando las conexiones dadas."""
    graph = {
        "nodes": [
            {"id": "trigger", "type": "trigger", "name": "Cliente escribe"},
            {"id": "corporate_event_type_question", "type": "question", "name": "Tipo de evento",
             "text": "¿Qué tipo de evento tienes en mente?",
             "options": ["Reunión corporativa", "Evento especial", "Otro"]},
            {"id": "corporate_headcount_question", "type": "message", "name": "Personas",
             "text": "¿Para cuántas personas sería?"},
            {"id": "corporate_date_question", "type": "message", "name": "Fecha",
             "text": "¿Qué fecha y hora tienes en mente?"},
            {"id": "corporate_location_question", "type": "question", "name": "Lugar",
             "text": "¿Dónde te gustaría recibir el pedido?",
             "options": ["Retiro en sucursal", "Entrega en mi lugar", "Aún no lo sé"]},
            {"id": "corporate_location_after_combined", "type": "message", "name": "Lugar (combinado)",
             "text": "¡Genial! ¿Dónde te gustaría recibir el pedido?"},
        ],
        "conns": conns_override,
    }
    flow = BotFlow(key="main_intake", name="Prueba", graph_json=json.dumps(graph, ensure_ascii=False))
    db_session.add(flow)
    db_session.commit()


def _post_text(client, phone, wamid, body):
    return client.post("/api/webhooks/whatsapp", json={
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "value": {"messaging_product": "whatsapp", "messages": [
                    {"from": phone, "id": wamid, "timestamp": "1725500000", "text": {"body": body}, "type": "text"}
                ]},
                "field": "messages"
            }]
        }]
    })


def _post_button(client, phone, wamid, button_id, title):
    return client.post("/api/webhooks/whatsapp", json={
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "value": {"messaging_product": "whatsapp", "messages": [
                    {"from": phone, "id": wamid, "timestamp": "1725500000",
                     "interactive": {"button_reply": {"id": button_id, "title": title}}, "type": "interactive"}
                ]},
                "field": "messages"
            }]
        }]
    })


def test_rewiring_the_graph_skips_a_corporate_step(client, clayton_branch, db_session):
    """Si el admin conecta 'Tipo de evento' directo a 'Lugar' (saltándose personas y fecha),
    el bot real debe respetarlo: tras el botón de tipo de evento, la siguiente pregunta debe
    ser la de lugar (step 4), no la de personas (step 2)."""
    cat_branch = Branch(id=30, code="CAT", name="Catering", color="#e11d48", active=True)
    db_session.add(cat_branch)
    db_session.commit()

    _seed_graph(db_session, conns_override=[
        # Salto deliberado: tipo de evento -> lugar directo (sin personas ni fecha).
        {"from": "corporate_event_type_question", "fromPort": 0, "to": "corporate_location_question"},
    ])

    phone = "50769995555"
    assert _post_text(client, phone, "wamid.SKIP01", "Quiero organizar un evento corporativo").status_code == 200
    contact = db_session.query(Contact).filter(Contact.phone.contains("69995555")).first()
    conv = db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()
    assert conv.corporate_intake_step == 1

    resp = _post_button(client, phone, "wamid.SKIP02", "event_type_meeting", "Reunión corporativa")
    assert resp.status_code == 200
    db_session.refresh(conv)
    # Con el grafo por defecto esto sería step == 2 (pregunta de personas). Reconectado, debe
    # saltar directo a step == 4 (pregunta de lugar).
    assert conv.corporate_intake_step == 4

    resp = _post_button(client, phone, "wamid.SKIP03", "event_loc_pickup", "Retiro en sucursal")
    assert resp.status_code == 200
    db_session.refresh(conv)
    assert conv.corporate_intake_step is None
    assert conv.automation_paused is True
    assert "Cantidad de personas" not in (conv.corporate_intake_notes or "")
    assert "Lugar de entrega" in conv.corporate_intake_notes


def test_broken_graph_connection_falls_back_safely(client, clayton_branch, db_session):
    """Si la conexión del nodo actual apunta a un nodo que ya no existe (el admin lo borró),
    el bot debe seguir el comportamiento de siempre en vez de trabarse o perder la conversación."""
    cat_branch = Branch(id=31, code="CAT", name="Catering", color="#e11d48", active=True)
    db_session.add(cat_branch)
    db_session.commit()

    _seed_graph(db_session, conns_override=[
        {"from": "corporate_event_type_question", "fromPort": 0, "to": "nodo_que_no_existe"},
    ])

    phone = "50769996666"
    assert _post_text(client, phone, "wamid.BROKEN01", "Quiero organizar un evento corporativo").status_code == 200
    contact = db_session.query(Contact).filter(Contact.phone.contains("69996666")).first()
    conv = db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()

    resp = _post_button(client, phone, "wamid.BROKEN02", "event_type_meeting", "Reunión corporativa")
    assert resp.status_code == 200
    db_session.refresh(conv)
    # Respaldo seguro: sigue el camino de siempre (pregunta de personas, step 2).
    assert conv.corporate_intake_step == 2
