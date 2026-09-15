"""
Recordatorio de respuesta pendiente: si el último mensaje de una conversación es del
cliente, pasaron >= 5 minutos y nadie de la sucursal la ha abierto desde entonces, el panel
debe poder detectarlo vía needs_reminder (listado y detalle), y abrir la conversación debe
apagar la alerta.
"""
from datetime import datetime, timedelta, timezone

from conftest import auth_headers_for
from models.contact import Contact
from models.conversation import Conversation
from models.message import Message


def _make_conversation(db_session, branch, assigned_user=None, status="open"):
    contact = Contact(
        name="Cliente Prueba",
        phone="+50760001111",
        created_at=datetime.now(timezone.utc),
        last_interaction=datetime.now(timezone.utc),
    )
    db_session.add(contact)
    db_session.commit()
    db_session.refresh(contact)

    conv = Conversation(
        customer_id=contact.id,
        branch_id=branch.id,
        assigned_user_id=assigned_user.id if assigned_user else None,
        status=status,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)
    return conv


def _add_message(db_session, conv, direction, created_at, is_internal=False, content="Hola"):
    msg = Message(
        conversation_id=conv.id,
        direction=direction,
        sender_type="customer" if direction == "incoming" else "agent",
        content=content,
        is_internal=is_internal,
        created_at=created_at,
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)
    return msg


def test_recent_incoming_message_does_not_need_reminder(db_session, clayton_branch, clayton_agent):
    conv = _make_conversation(db_session, clayton_branch, clayton_agent)
    _add_message(db_session, conv, "incoming", datetime.utcnow())
    db_session.refresh(conv)
    assert conv.needs_reminder is False


def test_stale_unopened_incoming_message_needs_reminder(db_session, clayton_branch, clayton_agent, clayton_device, client):
    conv = _make_conversation(db_session, clayton_branch, clayton_agent)
    _add_message(db_session, conv, "incoming", datetime.utcnow() - timedelta(minutes=6))
    db_session.refresh(conv)
    assert conv.needs_reminder is True

    # También debe verse reflejado en el listado que consume el panel.
    resp = client.get(
        "/api/conversations/?status=todas",
        headers=auth_headers_for(clayton_agent, clayton_device.device_id),
    )
    assert resp.status_code == 200
    body = next(c for c in resp.json() if c["id"] == conv.id)
    assert body["needs_reminder"] is True


def test_opening_conversation_clears_reminder(db_session, clayton_branch, clayton_agent, clayton_device, client):
    conv = _make_conversation(db_session, clayton_branch, clayton_agent)
    _add_message(db_session, conv, "incoming", datetime.utcnow() - timedelta(minutes=10))
    db_session.refresh(conv)
    assert conv.needs_reminder is True

    resp = client.get(
        f"/api/conversations/{conv.id}",
        headers=auth_headers_for(clayton_agent, clayton_device.device_id),
    )
    assert resp.status_code == 200
    assert resp.json()["needs_reminder"] is False

    db_session.refresh(conv)
    assert conv.needs_reminder is False
    assert conv.last_opened_at is not None


def test_agent_reply_clears_reminder_even_without_opening(db_session, clayton_branch, clayton_agent):
    conv = _make_conversation(db_session, clayton_branch, clayton_agent)
    _add_message(db_session, conv, "incoming", datetime.utcnow() - timedelta(minutes=10))
    _add_message(db_session, conv, "outgoing", datetime.utcnow())
    db_session.refresh(conv)
    assert conv.needs_reminder is False


def test_internal_note_does_not_count_as_a_reply(db_session, clayton_branch, clayton_agent):
    conv = _make_conversation(db_session, clayton_branch, clayton_agent)
    _add_message(db_session, conv, "incoming", datetime.utcnow() - timedelta(minutes=10))
    _add_message(db_session, conv, "outgoing", datetime.utcnow(), is_internal=True, content="nota interna")
    db_session.refresh(conv)
    assert conv.needs_reminder is True


def test_closed_conversation_never_needs_reminder(db_session, clayton_branch, clayton_agent):
    conv = _make_conversation(db_session, clayton_branch, clayton_agent, status="closed")
    _add_message(db_session, conv, "incoming", datetime.utcnow() - timedelta(minutes=30))
    db_session.refresh(conv)
    assert conv.needs_reminder is False
