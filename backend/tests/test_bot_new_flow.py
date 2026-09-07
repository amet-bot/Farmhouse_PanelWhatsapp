import pytest
from unittest.mock import AsyncMock
from config import settings
from conftest import TestingSessionLocal
from models.conversation import Conversation
from models.contact import Contact
from models.message import Message
from models.branch import Branch
from services.auto_responses import (
    MAIN_WELCOME_BODY, MAIN_MENU_OPTIONS, CORPORATE_INTAKE_CLOSING_MESSAGE,
    MANAGER_HELP_QUESTION, get_manager_assigned_message, get_manager_declined_message,
    BRANCH_SELECTION_VISIT_BODY, BRANCH_SELECTION_DELIVERY_BODY, BRANCH_SELECTION_PICKUP_BODY,
    get_branch_visit_message, MENU_LINK_WARM_CLOSING
)

@pytest.fixture(autouse=True)
def setup_webhook_env(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_MODE", "mock")
    monkeypatch.setattr("routers.webhooks.SessionLocal", TestingSessionLocal)
    def mock_push(*args, **kwargs):
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
    assert MAIN_WELCOME_BODY in msgs[-1].content


def test_initial_message_greets_customer_by_whatsapp_name(client, clayton_branch, db_session):
    # Cuando Meta manda el perfil de contacto (nombre real de WhatsApp), el saludo se
    # personaliza en vez de usar el genérico "¡Hola! Bienvenido a farmhouse."
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "contacts": [{"profile": {"name": "Ana"}, "wa_id": "50769998888"}],
                    "messages": [
                        {"from": "50769998888", "id": "wamid.TEST_NAME", "timestamp": "1725500000", "text": {"body": "Hola"}, "type": "text"}
                    ]
                },
                "field": "messages"
            }]
        }]
    }
    resp = client.post("/api/webhooks/whatsapp", json=payload)
    assert resp.status_code == 200

    contact = db_session.query(Contact).filter(Contact.phone.contains("69998888")).first()
    conv = db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()
    msgs = db_session.query(Message).filter(Message.conversation_id == conv.id, Message.direction == "outgoing").all()
    assert any("¡Hola, Ana! 👋 Soy el asistente de Farmhouse" in m.content for m in msgs)


def _post_bot_message(client, phone, wamid, *, text=None, button_id=None, button_title=None):
    if button_id:
        message = {
            "from": phone, "id": wamid, "timestamp": "1725500000", "type": "interactive",
            "interactive": {"button_reply": {"id": button_id, "title": button_title or button_id}},
        }
    else:
        message = {
            "from": phone, "id": wamid, "timestamp": "1725500000", "type": "text",
            "text": {"body": text or ""},
        }
    return client.post("/api/webhooks/whatsapp", json={
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA_ID", "changes": [{
            "value": {"messaging_product": "whatsapp", "messages": [message]},
            "field": "messages",
        }]}],
    })


def test_quick_order_button_opens_delivery_pickup_corporate_choices(client, clayton_branch, db_session):
    phone = "50769990011"
    assert _post_bot_message(
        client, phone, "wamid.NATURAL01", button_id="main_order", button_title="Hacer un pedido"
    ).status_code == 200

    contact = db_session.query(Contact).filter(Contact.phone.contains("69990011")).first()
    conv = db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()
    assert conv.delivery_type is None
    outgoing = db_session.query(Message).filter(
        Message.conversation_id == conv.id, Message.direction == "outgoing"
    ).all()
    assert any("¿Cómo quieres recibir tu pedido?" in msg.content for msg in outgoing)

    assert _post_bot_message(
        client, phone, "wamid.NATURAL02", button_id="order_delivery", button_title="Delivery"
    ).status_code == 200
    db_session.refresh(conv)
    assert conv.delivery_type == "delivery"
    outgoing = db_session.query(Message).filter(
        Message.conversation_id == conv.id, Message.direction == "outgoing"
    ).all()
    assert any("Delivery, entendido" in msg.content for msg in outgoing)


def test_customer_can_change_branch_in_natural_language(client, clayton_branch, db_session):
    phone = "50769990012"
    contact = Contact(name="Ana Cambio", phone=f"+{phone}")
    db_session.add(contact)
    db_session.commit()
    conv = Conversation(
        customer_id=contact.id, branch_id=clayton_branch.id,
        delivery_type="delivery", status="open",
    )
    db_session.add(conv)
    db_session.commit()

    assert _post_bot_message(client, phone, "wamid.NATURAL03", text="Quiero cambiar de sucursal").status_code == 200
    db_session.refresh(conv)
    assert conv.branch_id is None
    outgoing = db_session.query(Message).filter(
        Message.conversation_id == conv.id, Message.direction == "outgoing"
    ).all()
    assert any("puedes elegir otra sucursal" in msg.content for msg in outgoing)
    assert any("¿Desde cuál sucursal deseas pedir?" in msg.content for msg in outgoing)


def test_unknown_message_after_menu_never_leaves_customer_without_answer(client, clayton_branch, db_session):
    phone = "50769990013"
    contact = Contact(name="Cliente Duda", phone=f"+{phone}")
    db_session.add(contact)
    db_session.commit()
    conv = Conversation(
        customer_id=contact.id, branch_id=clayton_branch.id,
        delivery_type="pickup", status="open",
    )
    db_session.add(conv)
    db_session.commit()

    assert _post_bot_message(client, phone, "wamid.NATURAL04", text="Tengo una pregunta rara").status_code == 200
    outgoing = db_session.query(Message).filter(
        Message.conversation_id == conv.id, Message.direction == "outgoing"
    ).all()
    assert any("Mientras ves el menú" in msg.content for msg in outgoing)


def test_human_handoff_adds_internal_context_summary(client, clayton_branch, db_session):
    phone = "50769990014"
    contact = Contact(name="Cliente Humano", phone=f"+{phone}")
    db_session.add(contact)
    db_session.commit()
    conv = Conversation(
        customer_id=contact.id, branch_id=clayton_branch.id,
        delivery_type="delivery", payment_method="card", status="open",
    )
    db_session.add(conv)
    db_session.commit()

    assert _post_bot_message(client, phone, "wamid.NATURAL05", text="Quiero hablar con una persona").status_code == 200
    db_session.refresh(conv)
    assert conv.automation_paused is True
    internal = db_session.query(Message).filter(
        Message.conversation_id == conv.id, Message.is_internal == True
    ).all()
    assert any("Contexto recopilado" in msg.content and "Delivery" in msg.content and "Tarjeta" in msg.content for msg in internal)


def test_corporate_customer_can_answer_people_and_date_in_one_message(client, clayton_branch, db_session):
    cat_branch = Branch(id=21, code="CAT", name="Catering", color="#e11d48", active=True)
    db_session.add(cat_branch)
    db_session.commit()
    phone = "50769990015"

    assert _post_bot_message(client, phone, "wamid.NATURAL06", text="Necesito un evento para mi empresa").status_code == 200
    assert _post_bot_message(
        client, phone, "wamid.NATURAL07", button_id="event_type_meeting", button_title="Reunión corporativa"
    ).status_code == 200
    assert _post_bot_message(
        client, phone, "wamid.NATURAL08", text="25 personas, este viernes a las 12pm"
    ).status_code == 200

    contact = db_session.query(Contact).filter(Contact.phone.contains("69990015")).first()
    conv = db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()
    assert conv.corporate_intake_step == 4
    assert "Cantidad de personas" in conv.corporate_intake_notes
    assert "Fecha y hora (respuesta conjunta)" in conv.corporate_intake_notes
    outgoing = db_session.query(Message).filter(
        Message.conversation_id == conv.id, Message.direction == "outgoing"
    ).all()
    assert any("entiendo mucho mejor" in msg.content for msg in outgoing)
    assert any("¿dónde te gustaría recibir" in msg.content for msg in outgoing)


def test_typing_indicator_shown_before_bot_responds(client, clayton_branch, db_session, monkeypatch):
    # El bot marca el mensaje como leído y muestra "escribiendo..." antes de contestar (Punto de
    # "sentirse humano"): verificamos que se llame con el wamid del mensaje entrante correcto.
    from services.whatsapp_service import MockWhatsAppService
    calls = []

    async def fake_typing_indicator(self, wamid):
        calls.append(wamid)

    monkeypatch.setattr(MockWhatsAppService, "send_typing_indicator", fake_typing_indicator)

    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "value": {"messaging_product": "whatsapp", "messages": [
                    {"from": "50769997777", "id": "wamid.TEST_TYPING", "timestamp": "1725500000", "text": {"body": "Hola"}, "type": "text"}
                ]},
                "field": "messages"
            }]
        }]
    }
    resp = client.post("/api/webhooks/whatsapp", json=payload)
    assert resp.status_code == 200
    assert calls == ["wamid.TEST_TYPING"]


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
    assert any("Te esperamos en la sucursal de *Clayton*" in m.content for m in msgs)
    assert any("maps.google.com" in m.content for m in msgs)
    assert any(MANAGER_HELP_QUESTION in m.content for m in msgs)

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
    assert any("Tu pedido a domicilio saldrá de nuestra sucursal de *Clayton*" in m.content for m in msgs)
    assert any("maps.google.com" in m.content for m in msgs)
    assert any("/menu?" in m.content for m in msgs)
    assert any(MENU_LINK_WARM_CLOSING in m.content for m in msgs)


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
    assert any("Retirarás tu pedido en nuestra sucursal de *Obarrio*" in m.content for m in msgs)
    assert any("maps.google.com" in m.content for m in msgs)
    assert any("/menu?" in m.content for m in msgs)
    assert any(MENU_LINK_WARM_CLOSING in m.content for m in msgs)


def test_option_1_visit_view_menu_flow(client, clayton_branch, db_session):
    # Cliente selecciona Clayton para visitar y luego pide ver el menú antes de decidir algo más
    contact = Contact(name="Cliente Ve Menu", phone="+50769992244")
    db_session.add(contact)
    db_session.commit()
    conv = Conversation(customer_id=contact.id, branch_id=clayton_branch.id, delivery_type="visit", status="open")
    db_session.add(conv)
    db_session.commit()

    payload_view_menu = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "value": {"messaging_product": "whatsapp", "messages": [
                    {"from": "50769992244", "id": "wamid.TEST_VIEW_MENU", "timestamp": "1725500020", "interactive": {"button_reply": {"id": "view_menu", "title": "Ver el menú"}}, "type": "interactive"}
                ]},
                "field": "messages"
            }]
        }]
    }
    resp = client.post("/api/webhooks/whatsapp", json=payload_view_menu)
    assert resp.status_code == 200

    db_session.refresh(conv)
    assert conv.automation_paused is False

    msgs = db_session.query(Message).filter(Message.conversation_id == conv.id, Message.direction == "outgoing").all()
    assert any("/menu?" in m.content for m in msgs)
    assert any(MANAGER_HELP_QUESTION in m.content for m in msgs)


def test_option_4_corporate_flow(client, clayton_branch, db_session):
    # El flujo de "Pedido Corporativo / Evento" hace 4 preguntas guiadas (tipo de evento,
    # cantidad de personas, fecha, lugar) antes de asignar la conversación a Sol y pausar el bot.
    cat_branch = Branch(id=10, code="CAT", name="Catering", color="#e11d48", active=True)
    db_session.add(cat_branch)
    db_session.commit()

    def post_text(body, wamid):
        return client.post("/api/webhooks/whatsapp", json={
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "WABA_ID",
                "changes": [{
                    "value": {"messaging_product": "whatsapp", "messages": [
                        {"from": "50769994444", "id": wamid, "timestamp": "1725500000", "text": {"body": body}, "type": "text"}
                    ]},
                    "field": "messages"
                }]
            }]
        })

    def post_button(button_id, title, wamid):
        return client.post("/api/webhooks/whatsapp", json={
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "WABA_ID",
                "changes": [{
                    "value": {"messaging_product": "whatsapp", "messages": [
                        {"from": "50769994444", "id": wamid, "timestamp": "1725500000", "interactive": {"button_reply": {"id": button_id, "title": title}}, "type": "interactive"}
                    ]},
                    "field": "messages"
                }]
            }]
        })

    # Paso 0: elige la opción 4 -> se asigna a Catering y arranca la pregunta 1
    resp = post_text("Quiero organizar un evento corporativo", "wamid.CORP01")
    assert resp.status_code == 200

    contact = db_session.query(Contact).filter(Contact.phone.contains("69994444")).first()
    conv = db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()
    assert conv.branch_id == cat_branch.id
    assert conv.corporate_intake_step == 1
    assert conv.automation_paused is False

    # Paso 1: tipo de evento (botón)
    resp = post_button("event_type_meeting", "Reunión corporativa", "wamid.CORP02")
    assert resp.status_code == 200
    db_session.refresh(conv)
    assert conv.corporate_intake_step == 2
    assert "Tipo de evento" in conv.corporate_intake_notes

    # Paso 2: cantidad de personas (texto libre)
    resp = post_text("25 personas", "wamid.CORP03")
    assert resp.status_code == 200
    db_session.refresh(conv)
    assert conv.corporate_intake_step == 3
    assert "Cantidad de personas: 25 personas" in conv.corporate_intake_notes

    # Paso 3: fecha y hora (texto libre)
    resp = post_text("El viernes 12 a las 12pm", "wamid.CORP04")
    assert resp.status_code == 200
    db_session.refresh(conv)
    assert conv.corporate_intake_step == 4
    assert "Fecha y hora: El viernes 12 a las 12pm" in conv.corporate_intake_notes

    # Paso 4: lugar de entrega (botón) -> termina el intake, pausa el bot, deja resumen interno
    resp = post_button("event_loc_delivery", "Entrega en mi lugar", "wamid.CORP05")
    assert resp.status_code == 200
    db_session.refresh(conv)
    assert conv.corporate_intake_step is None
    assert conv.automation_paused is True
    assert "Lugar de entrega" in conv.corporate_intake_notes

    msgs = db_session.query(Message).filter(Message.conversation_id == conv.id).all()
    assert any(CORPORATE_INTAKE_CLOSING_MESSAGE in m.content for m in msgs if not m.is_internal)
    internal_summary = next((m for m in msgs if m.is_internal and "Resumen para Sol" in m.content), None)
    assert internal_summary is not None
    assert "Tipo de evento" in internal_summary.content
    assert "Cantidad de personas" in internal_summary.content
    assert "Fecha y hora" in internal_summary.content
    assert "Lugar de entrega" in internal_summary.content
