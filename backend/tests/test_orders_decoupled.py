"""
Fase 3 (recortada) del plan Farmhouse Link: los pedidos ya no dependen obligatoriamente de una
conversación de WhatsApp. Acá se prueba 1) que el camino de siempre (con conversation_id) sigue
funcionando exactamente igual, 2) que ahora se puede crear un pedido sin conversation_id validando
sucursal directo, y 3) que external_reference es idempotente (409 si se repite).
"""
from models.contact import Contact
from models.conversation import Conversation
from tests.conftest import auth_headers_for


def _make_conversation(db_session, branch):
    contact = Contact(name="Cliente Clayton", phone="+50762223344")
    db_session.add(contact)
    db_session.commit()
    conv = Conversation(customer_id=contact.id, branch_id=branch.id, status="open")
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)
    return conv


def test_order_with_conversation_still_works_like_before(client, db_session, clayton_branch, clayton_agent, clayton_device):
    conv = _make_conversation(db_session, clayton_branch)
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    res = client.post(
        "/api/orders/",
        json={
            "conversation_id": conv.id,
            "branch_id": clayton_branch.id,
            "order_type": "delivery",
            "subtotal": "10.00",
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["conversation_id"] == conv.id
    assert body["source"] == "whatsapp"
    assert body["external_reference"] is None


def test_order_without_conversation_validates_branch_directly(client, clayton_agent, clayton_device, clayton_branch):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    res = client.post(
        "/api/orders/",
        json={
            "branch_id": clayton_branch.id,
            "order_type": "takeout",
            "subtotal": "5.00",
            "source": "manual",
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["conversation_id"] is None
    assert body["source"] == "manual"


def test_agent_without_conversation_still_blocked_from_other_branch(client, clayton_agent, clayton_device, obarrio_branch):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    res = client.post(
        "/api/orders/",
        json={
            "branch_id": obarrio_branch.id,
            "order_type": "takeout",
            "subtotal": "5.00",
        },
        headers=headers,
    )
    assert res.status_code == 403


def test_duplicate_external_reference_returns_409(client, admin_user, clayton_branch):
    headers = auth_headers_for(admin_user)
    payload = {
        "branch_id": clayton_branch.id,
        "order_type": "takeout",
        "subtotal": "5.00",
        "external_reference": "EXT-0001",
    }
    res1 = client.post("/api/orders/", json=payload, headers=headers)
    assert res1.status_code == 200, res1.text

    res2 = client.post("/api/orders/", json=payload, headers=headers)
    assert res2.status_code == 409
