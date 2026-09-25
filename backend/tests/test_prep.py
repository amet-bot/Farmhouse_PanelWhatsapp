"""
Prep por estación (Bowls), por sucursal: plantilla de ítems con par + checkpoints configurables,
y un checklist que se llena varias veces al día. Se prueba: crear plantilla (solo supervisor/
admin), llenar un checkpoint, que "ready" se calcule solo comparando contra el par, que volver a
enviar el mismo checkpoint+día actualiza en vez de duplicar, y el alcance por sucursal de siempre.
"""
from tests.conftest import auth_headers_for


def _create_template(client, headers, branch_id, checkpoints=None, items=None):
    payload = {
        "branch_id": branch_id,
        "name": "Bowls",
        "checkpoints": checkpoints or ["10am", "3pm", "8pm"],
        "items": items or [
            {"section": "Base", "name": "Kale picado", "unit_label": "cambro L", "par_target": "3", "notes": "cortar el día que se sirve"},
            {"section": "Toppings", "name": "Tomates cherry", "unit_label": "repuesto", "par_target": "6", "notes": "rayadas y listas para servir"},
        ],
    }
    res = client.post("/api/prep/templates", json=payload, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()


def test_supervisor_creates_template_with_custom_checkpoints(client, clayton_branch, supervisor_user, clayton_device):
    headers = auth_headers_for(supervisor_user, clayton_device.device_id)
    template = _create_template(client, headers, clayton_branch.id, checkpoints=["Congelador Grande", "Congelador chico"])
    assert template["checkpoints"] == ["Congelador Grande", "Congelador chico"]
    assert len(template["items"]) == 2


def test_agent_cannot_create_template(client, clayton_branch, clayton_agent, clayton_device):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    res = client.post(
        "/api/prep/templates",
        json={"branch_id": clayton_branch.id, "name": "Bowls", "checkpoints": ["10am"], "items": []},
        headers=headers,
    )
    assert res.status_code == 403


def test_agent_can_fill_checkpoint_and_ready_is_computed(client, clayton_branch, supervisor_user, clayton_agent, clayton_device):
    headers_sup = auth_headers_for(supervisor_user, clayton_device.device_id)
    template = _create_template(client, headers_sup, clayton_branch.id)
    kale_item = next(i for i in template["items"] if i["name"] == "Kale picado")

    headers_agent = auth_headers_for(clayton_agent, clayton_device.device_id)
    res = client.post(
        f"/api/prep/templates/{template['id']}/checks",
        json={"checkpoint": "10am", "entries": [{"template_item_id": kale_item["id"], "on_hand": "1"}]},
        headers=headers_agent,
    )
    assert res.status_code == 201, res.text
    entry = res.json()["entries"][0]
    assert entry["on_hand"] == "1.00"
    assert entry["ready"] is False  # 1 < par 3


def test_resubmitting_same_checkpoint_and_day_updates_instead_of_duplicating(client, clayton_branch, supervisor_user, clayton_device):
    headers = auth_headers_for(supervisor_user, clayton_device.device_id)
    template = _create_template(client, headers, clayton_branch.id)
    kale_item = next(i for i in template["items"] if i["name"] == "Kale picado")

    first = client.post(
        f"/api/prep/templates/{template['id']}/checks",
        json={"checkpoint": "10am", "entries": [{"template_item_id": kale_item["id"], "on_hand": "1"}]},
        headers=headers,
    ).json()
    second = client.post(
        f"/api/prep/templates/{template['id']}/checks",
        json={"checkpoint": "10am", "entries": [{"template_item_id": kale_item["id"], "on_hand": "3"}]},
        headers=headers,
    ).json()
    assert first["id"] == second["id"]
    assert second["entries"][0]["on_hand"] == "3.00"
    assert second["entries"][0]["ready"] is True

    res_list = client.get(f"/api/prep/templates/{template['id']}/checks", headers=headers)
    assert len(res_list.json()) == 1


def test_invalid_checkpoint_rejected(client, clayton_branch, supervisor_user, clayton_device):
    headers = auth_headers_for(supervisor_user, clayton_device.device_id)
    template = _create_template(client, headers, clayton_branch.id)
    kale_item = template["items"][0]
    res = client.post(
        f"/api/prep/templates/{template['id']}/checks",
        json={"checkpoint": "medianoche", "entries": [{"template_item_id": kale_item["id"], "on_hand": "1"}]},
        headers=headers,
    )
    assert res.status_code == 400


def test_agent_from_other_branch_cannot_see_or_fill_template(client, clayton_branch, supervisor_user, clayton_device, obarrio_agent, obarrio_device):
    headers_sup = auth_headers_for(supervisor_user, clayton_device.device_id)
    template = _create_template(client, headers_sup, clayton_branch.id)

    headers_oba = auth_headers_for(obarrio_agent, obarrio_device.device_id)
    res_get = client.get(f"/api/prep/templates/{template['id']}", headers=headers_oba)
    assert res_get.status_code == 403

    res_fill = client.post(
        f"/api/prep/templates/{template['id']}/checks",
        json={"checkpoint": "10am", "entries": [{"template_item_id": template["items"][0]["id"], "on_hand": "1"}]},
        headers=headers_oba,
    )
    assert res_fill.status_code == 403


def test_update_template_replaces_items(client, clayton_branch, supervisor_user, clayton_device):
    headers = auth_headers_for(supervisor_user, clayton_device.device_id)
    template = _create_template(client, headers, clayton_branch.id)

    res = client.put(
        f"/api/prep/templates/{template['id']}",
        json={
            "branch_id": clayton_branch.id, "name": "Bowls",
            "checkpoints": ["10am"],
            "items": [{"section": "Base", "name": "Quinoa cocida", "unit_label": "1/1", "par_target": "1"}],
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["name"] == "Quinoa cocida"
