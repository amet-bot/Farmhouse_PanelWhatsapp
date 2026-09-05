import pytest
from unittest.mock import AsyncMock
from config import settings
from conftest import TestingSessionLocal
from models.conversation import Conversation
from models.contact import Contact
from models.message import Message
from models.branch import Branch
from services.auto_responses import (
    MAIN_WELCOME_BODY, MAIN_MENU_OPTIONS, CORPORATE_WELCOME_MESSAGE,
    MANAGER_HELP_QUESTION, get_manager_assigned_message, get_manager_declined_message,
    BRANCH_SELECTION_VISIT_BODY, BRANCH_SELECTION_DELIVERY_BODY, BRANCH_SELECTION_PICKUP_BODY,
    get_branch_visit_message
)

@pytest.fixture(autouse=True)
def setup_webhook_env(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_MODE", "mock")
    monkeypatch.setattr("routers.webhooks.SessionLocal", TestingSessionLocal)
    async def mock_push(*args, **kwargs):
        pass
    monkeypatch.setattr("routers.webhooks.notify_branch_new_message", mock_push)

def test_initial_any_message_triggers_main_welcome_menu(client, clayton_branch, db_session):
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "value": {"messaging_product": "whatsapp", "messages": [
                    {"from": "50769991111", "id": "wamid.TEST01", "timestamp": "1725500000", "text": {"body": "Hola buenas tardes"}, "type": "text"}
                ]},
                "field": "messages"
            }]
        }]
    }
    resp = client.post("/api/webhooks/whatsapp", json=payload)
    assert resp.status_code == 200

    contact = db_session.query(Contact).filter(Contact.phone.contains("69991111")).first()
    assert contact is not None
    conv = db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()
    assert conv is not None
    assert conv.branch_id is None
    assert conv.delivery_type is None

    msgs = db_session.query(Message).filter(Message.conversation_id == conv.id, Message.direction == "outgoing").all()
    assert len(msgs) >= 1
    assert "Hola Bienvenido a farmhouse, como te podemos ayudar hoy?" in msgs[-1].content


def test_option_1_visit_branches_and_manager_yes_flow(client, clayton_branch, db_session):
    # Paso 1: Cliente escribe '1' o 'visitar'
    payload_opt1 = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "value": {"messaging_product": "whatsapp", "messages": [
                    {"from": "50769992222", "id": "wamid.TEST02", "timestamp": "1725500000", "text": {"body": "1"}, "type": "text"}
                ]},
                "field": "messages"
            }]
        }]
    }
    resp = client.post("/api/webhooks/whatsapp", json=payload_opt1)
    assert resp.status_code == 200

    contact = db_session.query(Contact).filter(Contact.phone.contains("69992222")).first()
    conv = db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()
    assert conv.delivery_type == "visit"
    assert conv.branch_id is None

    # Paso 2: Cliente selecciona Clayton
    payload_branch = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "value": {"messaging_product": "whatsapp", "messages": [
                    {"from": "50769992222", "id": "wamid.TEST03", "timestamp": "1725500010", "text": {"body": "Clayton"}, "type": "text"}
                ]},
                "field": "messages"
            }]
        }]
    }
    resp2 = client.post("/api/webhooks/whatsapp", json=payload_branch)
    assert resp2.status_code == 200

    db_session.refresh(conv)
    assert conv.branch_id == clayton_branch.id

    msgs = db_session.query(Message).filter(Message.conversation_id == conv.id, Message.direction == "outgoing").all()
    assert any("Excelente te esperamos en la sucursal de Clayton" in m.content for m in msgs)
    assert any("Te podemos ayudar en algo mas?" in m.content for m in msgs)

    # Paso 3: Cliente elige hablar con gerente (1)
    payload_manager = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "value": {"messaging_product": "whatsapp", "messages": [
                    {"from": "50769992222", "id": "wamid.TEST03_B", "timestamp": "1725500020", "interactive": {"button_reply": {"id": "manager_yes", "title": "Hablar con gerente"}}, "type": "interactive"}
                ]},
                "field": "messages"
            }]
        }]
    }
    resp3 = client.post("/api/webhooks/whatsapp", json=payload_manager)
    assert resp3.status_code == 200

    db_session.refresh(conv)
    assert conv.automation_paused is True

    msgs_after = db_session.query(Message).filter(Message.conversation_id == conv.id, Message.direction == "outgoing").all()
    assert any("gerente de nuestra sucursal de *Clayton*" in m.content for m in msgs_after)


def test_option_1_visit_branches_and_manager_no_flow(client, clayton_branch, db_session):
    # Cliente selecciona Clayton y luego responde que no necesita nada más
    contact = Contact(name="Cliente No Gerente", phone="+50769992233")
    db_session.add(contact)
    db_session.commit()
    conv = Conversation(customer_id=contact.id, branch_id=clayton_branch.id, delivery_type="visit", status="open")
    db_session.add(conv)
    db_session.commit()

    payload_no = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "value": {"messaging_product": "whatsapp", "messages": [
                    {"from": "50769992233", "id": "wamid.TEST_NO", "timestamp": "1725500020", "text": {"body": "No gracias nos vemos pronto"}, "type": "text"}
                ]},
                "field": "messages"
            }]
        }]
    }
    resp = client.post("/api/webhooks/whatsapp", json=payload_no)
    assert resp.status_code == 200

    db_session.refresh(conv)
    assert conv.automation_paused is False

    msgs = db_session.query(Message).filter(Message.conversation_id == conv.id, Message.direction == "outgoing").all()
    assert any("Que tengas un excelente día" in m.content for m in msgs)


def test_option_2_delivery_flow(client, clayton_branch, db_session):
    payload_opt2 = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "value": {"messaging_product": "whatsapp", "messages": [
                    {"from": "50769993333", "id": "wamid.TEST04", "timestamp": "1725500000", "interactive": {"list_reply": {"id": "opt_delivery", "title": "2. Pedido a domicilio"}}, "type": "interactive"}
                ]},
                "field": "messages"
            }]
        }]
    }
    resp = client.post("/api/webhooks/whatsapp", json=payload_opt2)
    assert resp.status_code == 200

    contact = db_session.query(Contact).filter(Contact.phone.contains("69993333")).first()
    conv = db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()
    assert conv.delivery_type == "delivery"

    # Selecciona Clayton
    payload_branch = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "value": {"messaging_product": "whatsapp", "messages": [
                    {"from": "50769993333", "id": "wamid.TEST05", "timestamp": "1725500010", "interactive": {"list_reply": {"id": f"branch_{clayton_branch.id}", "title": "Clayton"}}, "type": "interactive"}
                ]},
                "field": "messages"
            }]
        }]
    }
    resp2 = client.post("/api/webhooks/whatsapp", json=payload_branch)
    assert resp2.status_code == 200

    db_session.refresh(conv)
    assert conv.branch_id == clayton_branch.id

    msgs = db_session.query(Message).filter(Message.conversation_id == conv.id, Message.direction == "outgoing").all()
    assert any("/menu?" in m.content for m in msgs)


def test_option_3_pickup_flow(client, obarrio_branch, db_session):
    payload_opt3 = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "value": {"messaging_product": "whatsapp", "messages": [
                    {"from": "50769995555", "id": "wamid.TEST07", "timestamp": "1725500000", "interactive": {"list_reply": {"id": "opt_pickup", "title": "3. Retirar en local"}}, "type": "interactive"}
                ]},
                "field": "messages"
            }]
        }]
    }
    resp = client.post("/api/webhooks/whatsapp", json=payload_opt3)
    assert resp.status_code == 200

    contact = db_session.query(Contact).filter(Contact.phone.contains("69995555")).first()
    conv = db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()
    assert conv.delivery_type == "pickup"

    # Selecciona Obarrio
    payload_branch = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "value": {"messaging_product": "whatsapp", "messages": [
                    {"from": "50769995555", "id": "wamid.TEST08", "timestamp": "1725500010", "interactive": {"list_reply": {"id": f"branch_{obarrio_branch.id}", "title": "Obarrio"}}, "type": "interactive"}
                ]},
                "field": "messages"
            }]
        }]
    }
    resp2 = client.post("/api/webhooks/whatsapp", json=payload_branch)
    assert resp2.status_code == 200

    db_session.refresh(conv)
    assert conv.branch_id == obarrio_branch.id

    msgs = db_session.query(Message).filter(Message.conversation_id == conv.id, Message.direction == "outgoing").all()
    assert any("/menu?" in m.content for m in msgs)


def test_option_4_corporate_flow(client, clayton_branch, db_session):
    cat_branch = Branch(id=10, code="CAT", name="Catering", color="#e11d48", active=True)
    db_session.add(cat_branch)
    db_session.commit()

    payload_opt4 = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "value": {"messaging_product": "whatsapp", "messages": [
                    {"from": "50769994444", "id": "wamid.TEST06", "timestamp": "1725500000", "text": {"body": "Quiero organizar un evento corporativo"}, "type": "text"}
                ]},
                "field": "messages"
            }]
        }]
    }
    resp = client.post("/api/webhooks/whatsapp", json=payload_opt4)
    assert resp.status_code == 200

    contact = db_session.query(Contact).filter(Contact.phone.contains("69994444")).first()
    conv = db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()
    assert conv.automation_paused is True
    assert conv.branch_id == cat_branch.id

    msgs = db_session.query(Message).filter(Message.conversation_id == conv.id, Message.direction == "outgoing").all()
    assert CORPORATE_WELCOME_MESSAGE in msgs[-1].content