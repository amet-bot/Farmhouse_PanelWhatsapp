"""
Fase 4 (primer bloque) del plan Farmhouse Link: libro de movimientos de inventario, en paralelo a
la fórmula que ya calcula existencia al vuelo. Acá se prueba que cargamento, merma y conteo
generan movimientos con el signo correcto, y que el endpoint de comparación coincide con la
fórmula de siempre — sin que ninguno de los tres caminos haya cambiado su comportamiento propio.
"""
from tests.conftest import auth_headers_for


def _create_item(client, headers, name, unit="kg"):
    res = client.post("/api/inventory/items", json={"name": name, "unit": unit, "category": "Insumo"}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def test_shipment_generates_positive_movement_and_matches_formula(client, clayton_branch, clayton_agent, clayton_device):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    item = _create_item(client, headers, "Tomate")

    res = client.post(
        "/api/inventory/shipments",
        json={
            "branch_id": clayton_branch.id,
            "items": [{"inventory_item_id": item["id"], "quantity": "10", "unit_cost": "2.50"}],
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text

    res_cmp = client.get(f"/api/inventory/movements/compare?branch_id={clayton_branch.id}", headers=headers)
    assert res_cmp.status_code == 200, res_cmp.text
    row = next(r for r in res_cmp.json() if r["inventory_item_id"] == item["id"])
    assert float(row["on_hand_formula"]) == 10
    assert float(row["on_hand_movements"]) == 10
    assert row["matches"] is True


def test_waste_generates_negative_movement_and_matches_formula(client, clayton_branch, clayton_agent, clayton_device):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    item = _create_item(client, headers, "Lechuga")

    client.post(
        "/api/inventory/shipments",
        json={"branch_id": clayton_branch.id, "items": [{"inventory_item_id": item["id"], "quantity": "20", "unit_cost": "1.00"}]},
        headers=headers,
    )
    res_waste = client.post(
        "/api/inventory/waste",
        json={"branch_id": clayton_branch.id, "reason": "vencido", "items": [{"inventory_item_id": item["id"], "quantity": "5"}]},
        headers=headers,
    )
    assert res_waste.status_code == 201, res_waste.text

    res_cmp = client.get(f"/api/inventory/movements/compare?branch_id={clayton_branch.id}", headers=headers)
    row = next(r for r in res_cmp.json() if r["inventory_item_id"] == item["id"])
    assert float(row["on_hand_formula"]) == 15
    assert float(row["on_hand_movements"]) == 15
    assert row["matches"] is True


def test_count_adjustment_generates_signed_movement_and_matches_formula(client, clayton_branch, clayton_agent, clayton_device):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    item = _create_item(client, headers, "Queso")

    client.post(
        "/api/inventory/shipments",
        json={"branch_id": clayton_branch.id, "items": [{"inventory_item_id": item["id"], "quantity": "10", "unit_cost": "4.00"}]},
        headers=headers,
    )
    # Se contaron 8: faltaron 2 (diferencia negativa).
    res_count = client.post(
        "/api/inventory/counts",
        json={"branch_id": clayton_branch.id, "items": [{"inventory_item_id": item["id"], "counted_quantity": "8"}]},
        headers=headers,
    )
    assert res_count.status_code == 201, res_count.text

    res_cmp = client.get(f"/api/inventory/movements/compare?branch_id={clayton_branch.id}", headers=headers)
    row = next(r for r in res_cmp.json() if r["inventory_item_id"] == item["id"])
    assert float(row["on_hand_formula"]) == 8
    assert float(row["on_hand_movements"]) == 8
    assert row["matches"] is True


def test_only_mismatches_filter_hides_matching_rows(client, clayton_branch, clayton_agent, clayton_device):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    item = _create_item(client, headers, "Cebolla")
    client.post(
        "/api/inventory/shipments",
        json={"branch_id": clayton_branch.id, "items": [{"inventory_item_id": item["id"], "quantity": "3", "unit_cost": "1.00"}]},
        headers=headers,
    )

    res_cmp = client.get(f"/api/inventory/movements/compare?branch_id={clayton_branch.id}&only_mismatches=true", headers=headers)
    assert res_cmp.status_code == 200
    assert all(not r["matches"] for r in res_cmp.json()) or res_cmp.json() == []


def test_agent_cannot_compare_other_branch(client, clayton_agent, clayton_device, obarrio_branch):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    res = client.get(f"/api/inventory/movements/compare?branch_id={obarrio_branch.id}", headers=headers)
    assert res.status_code == 403
