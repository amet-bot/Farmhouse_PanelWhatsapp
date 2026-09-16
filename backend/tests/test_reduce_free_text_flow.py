"""
Fase 4 de "Flujo visual" / mejora del bot central: reduce cuánto texto libre necesita escribir
el cliente para navegar de inicio a cierre. Estas pruebas cubren lo nuevo: el hueco cerrado
después del link del Menú Digital, la fila "empezar de nuevo" tocable, las listas rápidas de
cantidad/fecha del intake corporativo, y el flujo completo de "pedir y pagar por chat".
"""
from config import settings
from conftest import TestingSessionLocal
from models.contact import Contact
from models.conversation import Conversation
from models.message import Message
from models.branch import Branch
from services.auto_responses import AFTER_MENU_HELP_QUESTION, CHAT_ORDER_INTRO_QUESTION, CHAT_ORDER_PAYMENT_QUESTION


def setup_env(monkeypatch):
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


def _post_interactive(client, phone, wamid, reply_id, title, kind="list_reply"):
    return client.post("/api/webhooks/whatsapp", json={
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA_ID", "changes": [{
            "value": {"messaging_product": "whatsapp", "messages": [
                {"from": phone, "id": wamid, "timestamp": "1725500000", "type": "interactive",
                 "interactive": {kind: {"id": reply_id, "title": title}}},
            ]},
            "field": "messages",
        }]}],
    })


def _outgoing(db_session, conv_id):
    return db_session.query(Message).filter(Message.conversation_id == conv_id, Message.direction == "outgoing").all()


def test_digital_menu_link_is_followed_by_tappable_options_same_turn(client, clayton_branch, db_session, monkeypatch):
    setup_env(monkeypatch)
    phone = "50769980001"
    assert _post_interactive(client, phone, "wamid.M01", "order_delivery", "Delivery").status_code == 200
    assert _post_interactive(client, phone, "wamid.M02", f"branch_{clayton_branch.id}", "Clayton").status_code == 200

    contact = db_session.query(Contact).filter(Contact.phone.contains("69980001")).first()
    conv = db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()
    outgoing = _outgoing(db_session, conv.id)
    # Antes de esta fase, el link del Menú Digital quedaba como último mensaje del turno sin
    # ninguna opción tocable a continuación. Ahora el mismo turno cierra con la lista "¿algo más?".
    assert any(AFTER_MENU_HELP_QUESTION in m.content for m in outgoing)


def test_nav_restart_row_resets_conversation_like_typing_cancelar(client, clayton_branch, db_session, monkeypatch):
    setup_env(monkeypatch)
    phone = "50769980002"
    assert _post_interactive(client, phone, "wamid.R01", "order_delivery", "Delivery").status_code == 200
    contact = db_session.query(Contact).filter(Contact.phone.contains("69980002")).first()
    conv = db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()
    assert conv.delivery_type == "delivery"

    assert _post_interactive(client, phone, "wamid.R02", "nav_restart", "🔄 Empezar de nuevo").status_code == 200
    db_session.refresh(conv)
    assert conv.delivery_type is None
    assert conv.branch_id is None


def test_corporate_headcount_and_date_quick_pick_rows(client, db_session, monkeypatch):
    setup_env(monkeypatch)
    cat_branch = Branch(id=20, code="CAT", name="Catering", color="#e11d48", active=True)
    db_session.add(cat_branch)
    db_session.commit()

    phone = "50769980003"
    assert _post_text(client, phone, "wamid.C01", "Quiero organizar un evento corporativo").status_code == 200
    contact = db_session.query(Contact).filter(Contact.phone.contains("69980003")).first()
    conv = db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()
    assert conv.corporate_intake_step == 1

    assert _post_interactive(client, phone, "wamid.C02", "event_type_meeting", "Reunión corporativa", kind="button_reply").status_code == 200
    db_session.refresh(conv)
    assert conv.corporate_intake_step == 2

    # Cantidad de personas: tocar un rango en vez de escribir un número.
    assert _post_interactive(client, phone, "wamid.C03", "hc_11_30", "11 a 30 personas").status_code == 200
    db_session.refresh(conv)
    assert conv.corporate_intake_step == 3
    assert "Cantidad de personas: 11 a 30 personas" in conv.corporate_intake_notes

    # Fecha: tocar una fecha rápida en vez de escribir la fecha.
    assert _post_interactive(client, phone, "wamid.C04", "date_tomorrow", "Mañana").status_code == 200
    db_session.refresh(conv)
    assert conv.corporate_intake_step == 4
    assert "Fecha y hora: Mañana" in conv.corporate_intake_notes

    assert _post_interactive(client, phone, "wamid.C05", "event_loc_pickup", "Retiro en sucursal", kind="button_reply").status_code == 200
    db_session.refresh(conv)
    assert conv.corporate_intake_step is None
    assert conv.automation_paused is True


def test_corporate_headcount_escape_hatch_to_free_text(client, db_session, monkeypatch):
    """Tocar 'Escribir cantidad exacta' no avanza el paso: vuelve a pedir la respuesta en texto,
    y esa respuesta escrita sigue funcionando exactamente como antes de esta fase."""
    setup_env(monkeypatch)
    cat_branch = Branch(id=21, code="CAT", name="Catering", color="#e11d48", active=True)
    db_session.add(cat_branch)
    db_session.commit()

    phone = "50769980004"
    assert _post_text(client, phone, "wamid.E01", "Quiero organizar un evento corporativo").status_code == 200
    assert _post_interactive(client, phone, "wamid.E02", "event_type_meeting", "Reunión corporativa", kind="button_reply").status_code == 200

    contact = db_session.query(Contact).filter(Contact.phone.contains("69980004")).first()
    conv = db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()
    assert conv.corporate_intake_step == 2

    assert _post_interactive(client, phone, "wamid.E03", "hc_type_exact", "Escribir cantidad exacta").status_code == 200
    db_session.refresh(conv)
    assert conv.corporate_intake_step == 2  # sigue en el mismo paso, esperando texto

    assert _post_text(client, phone, "wamid.E04", "42 personas").status_code == 200
    db_session.refresh(conv)
    assert conv.corporate_intake_step == 3
    assert "Cantidad de personas: 42 personas" in conv.corporate_intake_notes


def test_chat_order_and_pay_without_leaving_whatsapp(client, clayton_branch, db_session, monkeypatch):
    setup_env(monkeypatch)
    phone = "50769980005"
    assert _post_interactive(client, phone, "wamid.P01", "order_pickup", "Retiro en local").status_code == 200
    assert _post_interactive(client, phone, "wamid.P02", f"branch_{clayton_branch.id}", "Clayton").status_code == 200

    contact = db_session.query(Contact).filter(Contact.phone.contains("69980005")).first()
    conv = db_session.query(Conversation).filter(Conversation.customer_id == contact.id).first()

    # Toca "Pedir y pagar por chat" en vez de abrir el Menú Digital.
    assert _post_interactive(client, phone, "wamid.P03", "chat_order_start", "Pedir y pagar por chat").status_code == 200
    db_session.refresh(conv)
    assert conv.awaiting_chat_order_description is True
    outgoing = _outgoing(db_session, conv.id)
    assert any(CHAT_ORDER_INTRO_QUESTION in m.content for m in outgoing)

    # Describe su pedido en texto libre (inevitable: es lo que quiere pedir).
    assert _post_text(client, phone, "wamid.P04", "2 bowls de pollo y una limonada").status_code == 200
    db_session.refresh(conv)
    assert conv.awaiting_chat_order_description is False
    outgoing = _outgoing(db_session, conv.id)
    assert any(CHAT_ORDER_PAYMENT_QUESTION in m.content for m in outgoing)
    assert any("2 bowls de pollo y una limonada" in m.content for m in db_session.query(Message).filter(Message.conversation_id == conv.id).all())

    # Elige el método de pago tocando la lista, no escribiéndolo.
    assert _post_interactive(client, phone, "wamid.P05", "pay_yappy", "Yappy").status_code == 200
    db_session.refresh(conv)
    assert conv.payment_method == "yappy"
    assert conv.automation_paused is True
