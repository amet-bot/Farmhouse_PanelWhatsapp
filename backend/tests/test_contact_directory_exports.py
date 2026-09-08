from datetime import datetime, timezone

from models.contact import Contact
from models.conversation import Conversation
from models.message import Message
from models.order import Order
from conftest import auth_headers_for


def _archived_customer(db_session, branch):
    now = datetime.now(timezone.utc)
    contact = Contact(
        name="Ana Exportación",
        phone="+50760009999",
        notes="Prefiere mensajes por la tarde",
        address="Ciudad de Panamá",
        created_at=now,
        last_interaction=now,
    )
    db_session.add(contact)
    db_session.flush()
    conversation = Conversation(
        customer_id=contact.id,
        branch_id=branch.id,
        status="closed",
        created_at=now,
        updated_at=now,
        deleted_at=now,
    )
    db_session.add(conversation)
    db_session.flush()
    db_session.add(Message(
        conversation_id=conversation.id,
        direction="incoming",
        sender_type="customer",
        content="Quiero cotizar un pedido",
        is_internal=False,
        status="delivered",
        created_at=now,
        deleted_at=now,
    ))
    db_session.add(Order(
        order_code="FH-EXPORT-001",
        conversation_id=conversation.id,
        branch_id=branch.id,
        order_type="delivery",
        status="entregado",
        subtotal=20,
        total=20,
        created_at=now,
    ))
    db_session.commit()
    return contact, conversation


def test_directory_preserves_contact_stats_after_chat_is_archived(
    client, db_session, clayton_branch, admin_user
):
    contact, conversation = _archived_customer(db_session, clayton_branch)

    response = client.get("/api/contacts/directory", headers=auth_headers_for(admin_user))

    assert response.status_code == 200
    row = next(item for item in response.json() if item["id"] == contact.id)
    assert row["conversation_count"] == 1
    assert row["active_conversation_count"] == 0
    assert row["archived_conversation_count"] == 1
    assert row["order_count"] == 1
    assert row["latest_conversation_id"] == conversation.id
    assert row["latest_active_conversation_id"] is None
    assert row["latest_branch_name"] == "Clayton"


def test_contact_and_chat_exports_are_downloadable_and_include_archived_history(
    client, db_session, clayton_branch, admin_user
):
    contact, _ = _archived_customer(db_session, clayton_branch)
    headers = auth_headers_for(admin_user)

    csv_response = client.get("/api/contacts/export.csv", headers=headers)
    assert csv_response.status_code == 200
    assert "attachment" in csv_response.headers["content-disposition"]
    assert csv_response.content.startswith(b"\xef\xbb\xbf")
    assert contact.phone in csv_response.text

    json_response = client.get("/api/contacts/chats.json", headers=headers)
    assert json_response.status_code == 200
    assert "attachment" in json_response.headers["content-disposition"]
    payload = json_response.json()
    assert payload["format"] == "farmhouse-conversations-v1"
    exported = next(item for item in payload["contacts"] if item["id"] == contact.id)
    assert exported["conversations"][0]["archived"] is True
    assert exported["conversations"][0]["messages"][0]["content"] == "Quiero cotizar un pedido"
    assert exported["conversations"][0]["messages"][0]["archived"] is True


def test_agent_directory_and_exports_remain_isolated_by_branch(
    client, db_session, clayton_branch, obarrio_branch, clayton_agent, clayton_device
):
    clayton_contact, _ = _archived_customer(db_session, clayton_branch)
    now = datetime.now(timezone.utc)
    other_contact = Contact(name="Otra Sucursal", phone="+50761110000", created_at=now, last_interaction=now)
    db_session.add(other_contact)
    db_session.flush()
    db_session.add(Conversation(
        customer_id=other_contact.id,
        branch_id=obarrio_branch.id,
        status="open",
        created_at=now,
        updated_at=now,
    ))
    db_session.commit()
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)

    directory = client.get("/api/contacts/directory", headers=headers)
    assert directory.status_code == 200
    assert [item["id"] for item in directory.json()] == [clayton_contact.id]

    exported = client.get("/api/contacts/chats.json", headers=headers).json()
    assert [item["id"] for item in exported["contacts"]] == [clayton_contact.id]

