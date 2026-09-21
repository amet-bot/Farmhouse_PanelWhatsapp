from tests.conftest import auth_headers_for


def _create_item(client, headers, name, unit="kg", category="Insumo"):
    res = client.post(
        "/api/inventory/items",
        json={"name": name, "unit": unit, "category": category},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_create_item_and_autocomplete_finds_it(client, clayton_agent, clayton_device):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    item = _create_item(client, headers, "Tomate")
    assert item["name"] == "Tomate"
    assert item["unit"] == "kg"

    res = client.get("/api/inventory/items?q=toma", headers=headers)
    assert res.status_code == 200
    assert "Tomate" in [i["name"] for i in res.json()]


def test_creating_same_item_twice_returns_existing(client, clayton_agent, clayton_device):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    first = _create_item(client, headers, "Harina")
    second = _create_item(client, headers, "harina") # coincide sin importar mayúsculas
    assert first["id"] == second["id"]


def test_agent_can_register_shipment_for_own_branch(client, clayton_branch, clayton_agent, clayton_device):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    item = _create_item(client, headers, "Pechuga de pollo", unit="lb")

    res = client.post(
        "/api/inventory/shipments",
        json={
            "branch_id": clayton_branch.id,
            "supplier": "Distribuidora ABC",
            "items": [{"inventory_item_id": item["id"], "quantity": "10", "unit_cost": "3.25"}],
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["branch_id"] == clayton_branch.id
    assert data["branch_name"] == "Clayton"
    assert data["received_by_name"] == "Agente Clayton"
    assert len(data["items"]) == 1
    assert data["items"][0]["item_name"] == "Pechuga de pollo"
    assert data["total_cost"] == "32.50"


def test_agent_cannot_register_shipment_for_other_branch(
    client, clayton_branch, obarrio_branch, clayton_agent, clayton_device
):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    item = _create_item(client, headers, "Lechuga")

    res = client.post(
        "/api/inventory/shipments",
        json={
            "branch_id": obarrio_branch.id,
            "items": [{"inventory_item_id": item["id"], "quantity": "5"}],
        },
        headers=headers,
    )
    assert res.status_code == 403


def test_agent_only_sees_shipments_from_own_branch_admin_sees_all(
    client, clayton_branch, obarrio_branch, clayton_agent, obarrio_agent,
    clayton_device, obarrio_device, admin_user
):
    headers_clayton = auth_headers_for(clayton_agent, clayton_device.device_id)
    headers_obarrio = auth_headers_for(obarrio_agent, obarrio_device.device_id)
    headers_admin = auth_headers_for(admin_user)

    item = _create_item(client, headers_clayton, "Aceite de oliva", unit="litro")

    res1 = client.post("/api/inventory/shipments", json={
        "branch_id": clayton_branch.id,
        "items": [{"inventory_item_id": item["id"], "quantity": "2"}],
    }, headers=headers_clayton)
    assert res1.status_code == 201

    res2 = client.post("/api/inventory/shipments", json={
        "branch_id": obarrio_branch.id,
        "items": [{"inventory_item_id": item["id"], "quantity": "3"}],
    }, headers=headers_obarrio)
    assert res2.status_code == 201

    res_clayton = client.get("/api/inventory/shipments", headers=headers_clayton)
    assert res_clayton.status_code == 200
    assert {s["branch_id"] for s in res_clayton.json()} == {clayton_branch.id}

    res_admin = client.get("/api/inventory/shipments", headers=headers_admin)
    assert res_admin.status_code == 200
    assert {s["branch_id"] for s in res_admin.json()} == {clayton_branch.id, obarrio_branch.id}
