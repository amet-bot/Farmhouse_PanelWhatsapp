"""
Fase 4 (cuarto bloque) del plan Farmhouse Link: bitácora de auditoría. Se prueba que las
escrituras que dijo el plan (usuarios, transferencias, mermas, conteos, cargamentos) dejan su
propio evento, sin cambiar el comportamiento ni la respuesta de esos endpoints.
"""
from models.audit import AuditEvent
from tests.conftest import auth_headers_for


def test_user_create_leaves_audit_event(client, db_session, admin_user, clayton_branch):
    headers = auth_headers_for(admin_user)
    res = client.post(
        "/api/users/",
        json={"username": "nuevo01", "name": "Nuevo Uno", "password": "Agent123!", "role": "agent", "branch_id": clayton_branch.id},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    new_user_id = res.json()["id"]

    event = db_session.query(AuditEvent).filter(
        AuditEvent.action == "user.create", AuditEvent.entity_id == new_user_id
    ).first()
    assert event is not None
    assert event.actor_user_id == admin_user.id


def test_shipment_create_leaves_audit_event(client, db_session, clayton_branch, clayton_agent, clayton_device):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    item = client.post("/api/inventory/items", json={"name": "Papa", "unit": "kg", "category": "Insumo"}, headers=headers).json()

    res = client.post(
        "/api/inventory/shipments",
        json={"branch_id": clayton_branch.id, "items": [{"inventory_item_id": item["id"], "quantity": "5", "unit_cost": "1.00"}]},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    shipment_id = res.json()["id"]

    event = db_session.query(AuditEvent).filter(
        AuditEvent.action == "shipment.create", AuditEvent.entity_id == shipment_id
    ).first()
    assert event is not None
    assert event.branch_id == clayton_branch.id


def test_transfer_actions_leave_audit_trail(
    client, db_session, clayton_branch, obarrio_branch, clayton_agent, clayton_device
):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    item = client.post("/api/inventory/items", json={"name": "Frijoles", "unit": "kg", "category": "Insumo"}, headers=headers).json()
    client.post(
        "/api/inventory/shipments",
        json={"branch_id": clayton_branch.id, "items": [{"inventory_item_id": item["id"], "quantity": "10", "unit_cost": "1.00"}]},
        headers=headers,
    )
    transfer = client.post(
        "/api/transfers/",
        json={"from_branch_id": clayton_branch.id, "to_branch_id": obarrio_branch.id, "items": [{"inventory_item_id": item["id"], "quantity": "2"}]},
        headers=headers,
    ).json()

    client.post(f"/api/transfers/{transfer['id']}/approve", json={}, headers=headers)
    client.post(f"/api/transfers/{transfer['id']}/dispatch", headers=headers)

    actions = {
        e.action for e in db_session.query(AuditEvent).filter(AuditEvent.entity_id == transfer["id"], AuditEvent.entity_type == "transfer").all()
    }
    assert actions == {"transfer.request", "transfer.approve", "transfer.dispatch"}
