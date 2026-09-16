"""Pedido corporativo / evento: el bot pasa directo el número del equipo de catering.

Decisión del negocio: al elegir "Evento o empresa" no se le hacen preguntas al cliente, se lo
manda de una al número que coordina con Sol. Las 4 preguntas guiadas siguen existiendo detrás de
CORPORATE_INTAKE_ENABLED (ver el fixture `corporate_intake_on` en conftest.py, que es el que usan
las pruebas de ese camino).
"""
from config import settings
from conftest import TestingSessionLocal
from models.contact import Contact
from models.conversation import Conversation
from models.message import Message
from services.auto_responses import CATERING_PHONE_DISPLAY, CATERING_PHONE_WA_LINK, CORPORATE_EVENT_TYPE_QUESTION


def setup_env(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_MODE", "mock")
    monkeypatch.setattr("routers.webhooks.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr("routers.webhooks.notify_branch_new_message", lambda *a, **k: None)


def _post_interactive(client, phone, wamid, reply_id, title):
    return client.post("/api/webhooks/whatsapp", json={
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA_ID", "changes": [{
            "value": {"messaging_product": "whatsapp", "messages": [
                {"from": phone, "id": wamid, "timestamp": "1725500000", "type": "interactive",
                 "interactive": {"list_reply": {"id": reply_id, "title": title}}},
            ]},
            "field": "messages",
        }]}],
    })


def _conv_for(db_session, phone_tail):
    contact = db_session.query(Contact).filter(Contact.phone.contains(phone_tail)).first()
    return db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()


def test_event_option_sends_catering_number_without_asking_anything(client, db_session, monkeypatch):
    setup_env(monkeypatch)
    phone = "50769970001"
    assert _post_interactive(client, phone, "wamid.C01", "order_corporate", "Evento o empresa").status_code == 200

    conv = _conv_for(db_session, "69970001")
    to_client = db_session.query(Message).filter(
        Message.conversation_id == conv.id, Message.direction == "outgoing", Message.is_internal == False  # noqa: E712
    ).all()

    assert len(to_client) == 1, "debería ser un solo mensaje: el del número, sin preguntas"
    body = to_client[0].content
    assert CATERING_PHONE_DISPLAY in body
    assert CATERING_PHONE_WA_LINK in body
    assert CORPORATE_EVENT_TYPE_QUESTION not in body

    # No queda a medio camino de un cuestionario: si el cliente escribe después, el bot no está
    # esperando la respuesta de un paso corporativo.
    assert conv.corporate_intake_step is None


def test_event_option_leaves_an_internal_note_for_the_team(client, db_session, monkeypatch):
    setup_env(monkeypatch)
    phone = "50769970002"
    assert _post_interactive(client, phone, "wamid.C02", "order_corporate", "Evento o empresa").status_code == 200

    conv = _conv_for(db_session, "69970002")
    internal = db_session.query(Message).filter(
        Message.conversation_id == conv.id, Message.is_internal == True  # noqa: E712
    ).all()
    assert any("catering" in m.content.lower() for m in internal), "el equipo debe ver el prospecto en el panel"


def test_the_four_questions_still_work_when_the_switch_is_turned_back_on(
    client, db_session, monkeypatch, corporate_intake_on
):
    """Seguro contra el día que el negocio pida volver a tomar contexto antes de pasar a Sol."""
    setup_env(monkeypatch)
    phone = "50769970003"
    assert _post_interactive(client, phone, "wamid.C03", "order_corporate", "Evento o empresa").status_code == 200

    conv = _conv_for(db_session, "69970003")
    assert conv.corporate_intake_step == 1
    outgoing = db_session.query(Message).filter(
        Message.conversation_id == conv.id, Message.direction == "outgoing"
    ).all()
    assert any(CORPORATE_EVENT_TYPE_QUESTION in m.content for m in outgoing)
    assert not any(CATERING_PHONE_DISPLAY in m.content for m in outgoing)
