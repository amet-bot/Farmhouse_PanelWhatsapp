"""
Fase 0 del plan Farmhouse Link: seis endpoints solo bloqueaban `role == "agent"` y dejaban pasar a
un supervisor de sucursal (branch_id fijo) a leer/escribir en otra sucursal. Cada prueba aquí monta
exactamente ese escenario — supervisor_user está fijo en Clayton (ver conftest.py) — y confirma que
ahora se comporta igual que un agente: 403 (o filtrado, en el caso de la lectura de usuarios).
"""
from tests.conftest import auth_headers_for
from models.contact import Contact
from models.conversation import Conversation


def _create_item(client, headers, name, unit="kg"):
    res = client.post("/api/inventory/items", json={"name": name, "unit": unit}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def test_supervisor_cannot_create_shipment_in_other_branch(
    client, clayton_branch, obarrio_branch, supervisor_user, clayton_device
):
    headers = auth_headers_for(supervisor_user, clayton_device.device_id)
    item = _create_item(client, headers, "Tomate")

    res = client.post(
        "/api/inventory/shipments",
        json={"branch_id": obarrio_branch.id, "items": [{"inventory_item_id": item["id"], "quantity": "5"}]},
        headers=headers,
    )
    assert res.status_code == 403


def test_supervisor_cannot_create_waste_in_other_branch(
    client, clayton_branch, obarrio_branch, supervisor_user, clayton_device
):
    headers = auth_headers_for(supervisor_user, clayton_device.device_id)
    item = _create_item(client, headers, "Lechuga")

    res = client.post(
        "/api/inventory/waste",
        json={
            "branch_id": obarrio_branch.id,
            "reason": "otro",
            "items": [{"inventory_item_id": item["id"], "quantity": "1"}],
        },
        headers=headers,
    )
    assert res.status_code == 403


def test_supervisor_cannot_create_count_in_other_branch(
    client, clayton_branch, obarrio_branch, supervisor_user, clayton_device
):
    headers = auth_headers_for(supervisor_user, clayton_device.device_id)
    item = _create_item(client, headers, "Aceite")

    res = client.post(
        "/api/inventory/counts",
        json={
            "branch_id": obarrio_branch.id,
            "items": [{"inventory_item_id": item["id"], "counted_quantity": "3"}],
        },
        headers=headers,
    )
    assert res.status_code == 403


def test_supervisor_cannot_create_order_in_other_branch(
    client, db_session, clayton_branch, obarrio_branch, supervisor_user, clayton_device
):
    contact = Contact(name="Cliente Clayton", phone="+50762223344")
    db_session.add(contact)
    db_session.commit()

    conv = Conversation(customer_id=contact.id, branch_id=clayton_branch.id, status="open")
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)

    headers = auth_headers_for(supervisor_user, clayton_device.device_id)
    res = client.post(
        "/api/orders/",
        json={
            "conversation_id": conv.id,
            "branch_id": obarrio_branch.id,
            "order_type": "delivery",
            "subtotal": "10.00",
        },
        headers=headers,
    )
    assert res.status_code == 403


def test_supervisor_cannot_heartbeat_device_in_other_branch(
    client, clayton_branch, obarrio_branch, supervisor_user, clayton_device, obarrio_device
):
    headers = auth_headers_for(supervisor_user, clayton_device.device_id)
    res = client.post(f"/api/devices/{obarrio_device.id}/heartbeat", headers=headers)
    assert res.status_code == 403


def test_supervisor_only_sees_own_branch_users(
    client, db_session, clayton_branch, obarrio_branch, supervisor_user, clayton_device, obarrio_agent
):
    headers = auth_headers_for(supervisor_user, clayton_device.device_id)
    res = client.get("/api/users/", headers=headers)
    assert res.status_code == 200
    branch_ids = {u["branch_id"] for u in res.json()}
    assert branch_ids == {clayton_branch.id}
    assert not any(u["id"] == obarrio_agent.id for u in res.json())

    # Ni siquiera pidiendo explícitamente la otra sucursal por query param se filtra por ella.
    res2 = client.get(f"/api/users/?branch_id={obarrio_branch.id}", headers=headers)
    assert res2.status_code == 200
    assert all(u["branch_id"] == clayton_branch.id for u in res2.json())
