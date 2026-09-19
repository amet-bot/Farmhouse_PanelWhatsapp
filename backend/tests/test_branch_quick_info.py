"""
"Ver horarios" / "Ver ubicación": las dos preguntas más comunes según el negocio, agregadas a la
lista "¿algo más?" que sigue al link del Menú Digital (en reemplazo de "Abrir el menú", que se
sentía redundante justo después de mandar ese mismo botón). Las dos mandan la misma info de la
sucursal ya asignada y vuelven a mostrar la misma lista.
"""
from config import settings
from conftest import TestingSessionLocal
from models.contact import Contact
from models.conversation import Conversation
from models.message import Message
from services.auto_responses import AFTER_MENU_HELP_QUESTION


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


def _reach_after_menu_list(client, db_session, phone, tail):
    """Delivery + sucursal, igual que en test_reduce_free_text_flow, para llegar al estado
    donde ya se mandó el link del Menú Digital y la lista "¿algo más?"."""
    assert _post_interactive(client, phone, f"wamid.{tail}.1", "order_delivery", "Delivery").status_code == 200
    assert _post_interactive(client, phone, f"wamid.{tail}.2", "branch_1", "Clayton").status_code == 200
    return _conv_for(db_session, tail)


def test_branch_hours_answers_with_branch_info_and_reshows_the_list(client, clayton_branch, db_session, monkeypatch):
    setup_env(monkeypatch)
    phone = "50769981001"
    conv = _reach_after_menu_list(client, db_session, phone, "69981001")

    resp = _post_interactive(client, phone, "wamid.hours", "branch_hours", "Ver horarios")
    assert resp.status_code == 200

    outgoing = db_session.query(Message).filter(
        Message.conversation_id == conv.id, Message.direction == "outgoing",
    ).order_by(Message.created_at, Message.id).all()

    # El mensaje de bienvenida a la sucursal (bloque 1 del turno) también trae dirección/horario
    # con otra apertura ("¡Excelente!... saldrá de..."), así que se busca específicamente la
    # apertura neutral de esta respuesta puntual para no confundirla con esa.
    info_msgs = [
        m for m in outgoing
        if m.content.startswith("Esto es lo que tenemos de nuestra sucursal")
        and "Clayton Mall, Local #4" in m.content and "10:30 AM" in m.content
    ]
    assert len(info_msgs) == 1, "debería responder con la dirección y el horario de la sucursal"

    # No se queda ahí: vuelve a mostrar la lista "¿algo más?" para no dejar la conversación
    # sin ninguna opción tocable.
    assert any(AFTER_MENU_HELP_QUESTION in m.content for m in outgoing[outgoing.index(info_msgs[0]):])


def test_branch_location_answers_with_the_same_info_as_branch_hours(client, clayton_branch, db_session, monkeypatch):
    setup_env(monkeypatch)
    phone = "50769981002"
    conv = _reach_after_menu_list(client, db_session, phone, "69981002")

    resp = _post_interactive(client, phone, "wamid.loc", "branch_location", "Ver ubicación")
    assert resp.status_code == 200

    outgoing = db_session.query(Message).filter(
        Message.conversation_id == conv.id, Message.direction == "outgoing",
    ).all()
    assert any("Clayton Mall, Local #4" in m.content and "maps.google.com" in m.content for m in outgoing)


def test_branch_info_request_does_not_change_delivery_state(client, clayton_branch, db_session, monkeypatch):
    """Preguntar horario/ubicación es una consulta de paso, no debe tocar el pedido en curso."""
    setup_env(monkeypatch)
    phone = "50769981003"
    conv = _reach_after_menu_list(client, db_session, phone, "69981003")

    _post_interactive(client, phone, "wamid.hours2", "branch_hours", "Ver horarios")
    db_session.refresh(conv)
    assert conv.delivery_type == "delivery"
    assert conv.branch_id == clayton_branch.id
    assert conv.automation_paused is False
