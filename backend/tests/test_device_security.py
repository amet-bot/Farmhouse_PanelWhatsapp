import pytest
from tests.conftest import auth_headers_for
from models.contact import Contact
from models.conversation import Conversation

def test_revoked_or_disabled_device_cannot_operate(
    client, db_session, clayton_branch, clayton_agent, clayton_device, revoked_device
):
    """
    Requisito (b): Un dispositivo revocado/deshabilitado no puede autenticar ni operar.
    """
    contact = Contact(name="Cliente Prueba", phone="+50762223344")
    db_session.add(contact)
    db_session.commit()

    conv = Conversation(customer_id=contact.id, branch_id=clayton_branch.id, status="unassigned")
    db_session.add(conv)
    db_session.commit()

    # 1. Operar con dispositivo REVOCADO -> Debe dar 403
    headers_revoked = auth_headers_for(clayton_agent, revoked_device.device_id)
    res_revoked = client.get("/api/conversations/", headers=headers_revoked)
    assert res_revoked.status_code == 403
    assert "revocado" in res_revoked.json()["detail"].lower()

    # 2. Operar con dispositivo DESHABILITADO -> Debe dar 403
    clayton_device.status = "disabled"
    db_session.commit()

    headers_disabled = auth_headers_for(clayton_agent, clayton_device.device_id)
    res_disabled = client.get("/api/conversations/", headers=headers_disabled)
    assert res_disabled.status_code == 403
    assert "deshabilitado" in res_disabled.json()["detail"].lower()

    # 3. Operar SIN dispositivo (agente) -> Debe dar 403
    headers_no_dev = auth_headers_for(clayton_agent, None)
    res_no_dev = client.get("/api/conversations/", headers=headers_no_dev)
    assert res_no_dev.status_code == 403
