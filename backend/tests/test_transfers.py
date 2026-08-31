import pytest
from tests.conftest import auth_headers_for
from models.contact import Contact
from models.conversation import Conversation
from models.message import Message

def test_transfer_conversation_updates_branch_and_creates_audit_message(
    client, db_session, clayton_branch, obarrio_branch, clayton_agent, clayton_device
):
    """
    Requisito (c): Transferir una conversación cambia la sucursal y genera el mensaje de auditoría.
    """
    # 1. Crear contacto y conversación en Clayton
    contact = Contact(name="Cliente Transfer", phone="+50763334455")
    db_session.add(contact)
    db_session.commit()

    conv = Conversation(
        customer_id=contact.id,
        branch_id=clayton_branch.id,
        assigned_user_id=clayton_agent.id,
        status="open"
    )
    db_session.add(conv)
    db_session.commit()

    # 2. Transferir a Obarrio
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    transfer_payload = {
        "target_branch_id": obarrio_branch.id,
        "reason": "Cliente solicita retiro en sucursal Obarrio"
    }
    res_transfer = client.post(f"/api/conversations/{conv.id}/transfer", json=transfer_payload, headers=headers)
    assert res_transfer.status_code == 200
    updated_conv = res_transfer.json()
    assert updated_conv["branch_id"] == obarrio_branch.id

    # 3. Verificar en la base de datos que se generó un mensaje de auditoría interno
    audit_msg = db_session.query(Message).filter(
        Message.conversation_id == conv.id,
        Message.is_internal == True
    ).first()

    assert audit_msg is not None
    assert "Transferida de" in audit_msg.content or "transferida" in audit_msg.content.lower()
    assert "Obarrio" in audit_msg.content
