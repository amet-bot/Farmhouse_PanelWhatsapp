"""
Regresiones de la auditoría (bloque 3): la bandeja de conversaciones y sus contadores.
"""
from datetime import datetime, timedelta

from tests.conftest import auth_headers_for
from models.contact import Contact
from models.conversation import Conversation
from models.message import Message


def _conv(db_session, branch, phone, status="open"):
    contact = Contact(name=f"Cliente {phone[-2:]}", phone=phone)
    db_session.add(contact)
    db_session.flush()
    conv = Conversation(customer_id=contact.id, branch_id=branch.id if branch else None, status=status)
    db_session.add(conv)
    db_session.flush()
    return conv


def _msg(db_session, conv, minutes_ago, direction="incoming", internal=False, deleted=False, content="hola"):
    m = Message(conversation_id=conv.id, direction=direction, sender_type="customer" if direction == "incoming" else "agent",
                content=content, is_internal=internal, created_at=datetime.utcnow() - timedelta(minutes=minutes_ago),
                deleted_at=datetime.utcnow() if deleted else None)
    db_session.add(m)
    db_session.flush()
    return m


def test_list_returns_only_last_messages_and_keeps_reminder(client, db_session, clayton_branch, admin_user):
    conv = _conv(db_session, clayton_branch, "+50760000021")
    _msg(db_session, conv, 30, content="primero")
    _msg(db_session, conv, 20, content="cliente espera")  # último mensaje del cliente, sin abrir
    _msg(db_session, conv, 10, direction="outgoing", internal=True, content="nota interna")
    _msg(db_session, conv, 5, content="borrado", deleted=True)
    db_session.commit()

    rows = client.get("/api/conversations/?status=todas", headers=auth_headers_for(admin_user)).json()
    row = next(r for r in rows if r["id"] == conv.id)
    contents = [m["content"] for m in row["messages"]]
    assert contents == ["cliente espera", "nota interna"]  # la vista previa usa el último
    assert row["orders"] == []
    # La nota interna posterior no cuenta como respuesta: sigue pendiente.
    assert row["needs_reminder"] is True


def test_detail_still_returns_history_and_does_not_delete_messages(client, db_session, clayton_branch, admin_user):
    conv = _conv(db_session, clayton_branch, "+50760000022")
    for i in range(55):
        _msg(db_session, conv, 100 - i, content=f"m{i}")
    db_session.commit()
    res = client.get(f"/api/conversations/{conv.id}", headers=auth_headers_for(admin_user))
    assert len(res.json()["messages"]) == 50
    db_session.expire_all()
    assert db_session.query(Message).filter(Message.conversation_id == conv.id).count() == 55


def test_counts_endpoint(client, db_session, clayton_branch, obarrio_branch, admin_user, clayton_agent, clayton_device):
    _conv(db_session, clayton_branch, "+50760000031", "open")
    _conv(db_session, clayton_branch, "+50760000032", "unassigned")
    _conv(db_session, clayton_branch, "+50760000033", "pending")
    _conv(db_session, clayton_branch, "+50760000034", "closed")
    _conv(db_session, obarrio_branch, "+50760000035", "open")
    db_session.commit()

    admin = client.get("/api/conversations/counts", headers=auth_headers_for(admin_user)).json()
    assert admin["abiertas"] == 3 and admin["no_asignadas"] == 1 and admin["pendientes"] == 1 and admin["todas"] == 5
    assert admin["abiertas_por_sucursal"] == {str(clayton_branch.id): 2, str(obarrio_branch.id): 1}
    # Los cuatro contadores por sucursal: las pestañas de la bandeja filtrada por una sucursal.
    assert admin["por_sucursal"][str(clayton_branch.id)] == {"abiertas": 2, "no_asignadas": 1, "pendientes": 1, "todas": 4}
    assert admin["por_sucursal"][str(obarrio_branch.id)] == {"abiertas": 1, "no_asignadas": 0, "pendientes": 0, "todas": 1}

    agent =client.get("/api/conversations/counts", headers=auth_headers_for(clayton_agent, clayton_device.device_id)).json()
    assert agent["todas"] == 4  # solo su sucursal
