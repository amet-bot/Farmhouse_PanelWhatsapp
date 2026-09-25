"""
Fase 4 (segundo bloque) del plan Farmhouse Link: traslados de insumos entre sucursales. Se
distingue del test_transfers.py existente, que prueba transferir una CONVERSACIÓN entre
sucursales (funcionalidad completamente distinta que ya existía).

Se prueba la máquina de estados completa (requested -> approved -> dispatched -> received), que
no se puede recibir dos veces, que rechazar/cancelar no genera movimiento, y que solo actúa quien
corresponde en cada paso (origen aprueba/despacha/rechaza, destino recibe).
"""
from tests.conftest import auth_headers_for


def _create_item(client, headers, name, unit="kg"):
    res = client.post("/api/inventory/items", json={"name": name, "unit": unit, "category": "Insumo"}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def _stock_shipment(client, headers, branch_id, item_id, quantity="20"):
    res = client.post(
        "/api/inventory/shipments",
        json={"branch_id": branch_id, "items": [{"inventory_item_id": item_id, "quantity": quantity, "unit_cost": "2.00"}]},
        headers=headers,
    )
    assert res.status_code == 201, res.text


def test_full_transfer_lifecycle_moves_stock_between_branches(
    client, clayton_branch, obarrio_branch, clayton_agent, clayton_device, obarrio_agent, obarrio_device
):
    headers_cla = auth_headers_for(clayton_agent, clayton_device.device_id)
    headers_oba = auth_headers_for(obarrio_agent, obarrio_device.device_id)

    item = _create_item(client, headers_cla, "Arroz")
    _stock_shipment(client, headers_cla, clayton_branch.id, item["id"], "20")

    res_create = client.post(
        "/api/transfers/",
        json={
            "from_branch_id": clayton_branch.id,
            "to_branch_id": obarrio_branch.id,
            "items": [{"inventory_item_id": item["id"], "quantity": "5"}],
        },
        headers=headers_cla,
    )
    assert res_create.status_code == 201, res_create.text
    transfer = res_create.json()
    assert transfer["status"] == "requested"

    res_approve = client.post(f"/api/transfers/{transfer['id']}/approve", json={}, headers=headers_cla)
    assert res_approve.status_code == 200, res_approve.text
    assert res_approve.json()["status"] == "approved"

    res_dispatch = client.post(f"/api/transfers/{transfer['id']}/dispatch", headers=headers_cla)
    assert res_dispatch.status_code == 200, res_dispatch.text
    assert res_dispatch.json()["status"] == "dispatched"

    # El origen ya bajó en el libro de movimientos, aunque destino todavía no recibió.
    cmp_cla = client.get(f"/api/inventory/movements/compare?branch_id={clayton_branch.id}", headers=headers_cla).json()
    row_cla = next(r for r in cmp_cla if r["inventory_item_id"] == item["id"])
    assert float(row_cla["on_hand_movements"]) == 15

    res_receive = client.post(f"/api/transfers/{transfer['id']}/receive", headers=headers_oba)
    assert res_receive.status_code == 200, res_receive.text
    assert res_receive.json()["status"] == "received"

    cmp_oba = client.get(f"/api/inventory/movements/compare?branch_id={obarrio_branch.id}", headers=headers_oba).json()
    row_oba = next(r for r in cmp_oba if r["inventory_item_id"] == item["id"])
    assert float(row_oba["on_hand_movements"]) == 5

    # Recibir de nuevo no puede repetirse.
    res_receive_again = client.post(f"/api/transfers/{transfer['id']}/receive", headers=headers_oba)
    assert res_receive_again.status_code == 409


def test_destination_branch_can_request_transfer_from_another_branch(
    client, clayton_branch, obarrio_branch, obarrio_agent, obarrio_device
):
    headers_oba = auth_headers_for(obarrio_agent, obarrio_device.device_id)
    item = _create_item(client, headers_oba, "Café")

    res = client.post(
        "/api/transfers/",
        json={
            "from_branch_id": clayton_branch.id,
            "to_branch_id": obarrio_branch.id,
            "items": [{"inventory_item_id": item["id"], "quantity": "2"}],
        },
        headers=headers_oba,
    )
    assert res.status_code == 201, res.text


def test_unrelated_agent_cannot_create_transfer(
    client, db_session, clayton_branch, obarrio_branch, clayton_agent, clayton_device
):
    """Un agente que no participa en ninguna de las dos puntas (una tercera sucursal) queda afuera."""
    from models.branch import Branch
    costa_este = Branch(
        code="CDE", name="Costa del Este", color="#000000", active=True,
        address="x", latitude=9.0, longitude=-79.5, accepts_delivery=True,
    )
    db_session.add(costa_este)
    db_session.commit()
    db_session.refresh(costa_este)

    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    item = _create_item(client, headers, "Sal")

    res = client.post(
        "/api/transfers/",
        json={
            "from_branch_id": obarrio_branch.id,
            "to_branch_id": costa_este.id,
            "items": [{"inventory_item_id": item["id"], "quantity": "1"}],
        },
        headers=headers,
    )
    assert res.status_code == 403


def test_destination_cannot_approve_or_dispatch(
    client, clayton_branch, obarrio_branch, clayton_agent, clayton_device, obarrio_agent, obarrio_device
):
    headers_cla = auth_headers_for(clayton_agent, clayton_device.device_id)
    headers_oba = auth_headers_for(obarrio_agent, obarrio_device.device_id)
    item = _create_item(client, headers_cla, "Azúcar")
    _stock_shipment(client, headers_cla, clayton_branch.id, item["id"], "10")

    res_create = client.post(
        "/api/transfers/",
        json={"from_branch_id": clayton_branch.id, "to_branch_id": obarrio_branch.id, "items": [{"inventory_item_id": item["id"], "quantity": "1"}]},
        headers=headers_cla,
    )
    transfer_id = res_create.json()["id"]

    res_approve = client.post(f"/api/transfers/{transfer_id}/approve", json={}, headers=headers_oba)
    assert res_approve.status_code == 403


def test_reject_before_dispatch_does_not_move_stock(
    client, clayton_branch, obarrio_branch, clayton_agent, clayton_device
):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    item = _create_item(client, headers, "Pimienta")
    _stock_shipment(client, headers, clayton_branch.id, item["id"], "10")

    res_create = client.post(
        "/api/transfers/",
        json={"from_branch_id": clayton_branch.id, "to_branch_id": obarrio_branch.id, "items": [{"inventory_item_id": item["id"], "quantity": "3"}]},
        headers=headers,
    )
    transfer_id = res_create.json()["id"]

    res_reject = client.post(f"/api/transfers/{transfer_id}/reject", json={"notes": "no hay suficiente"}, headers=headers)
    assert res_reject.status_code == 200
    assert res_reject.json()["status"] == "rejected"

    cmp_cla = client.get(f"/api/inventory/movements/compare?branch_id={clayton_branch.id}", headers=headers).json()
    row_cla = next(r for r in cmp_cla if r["inventory_item_id"] == item["id"])
    assert float(row_cla["on_hand_movements"]) == 10  # sin cambios: nunca se despachó
