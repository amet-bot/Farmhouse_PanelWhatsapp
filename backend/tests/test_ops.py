"""
Fase 4 (tercer bloque) del plan Farmhouse Link: Operación de Sucursal — solicitudes de insumos,
incidencias y tareas. Tres tablas chicas con su propio ciclo de vida, sin motor de flujos
genérico. Se prueba el CRUD simple de cada una y el mismo criterio de alcance por sucursal que
ya usa el resto del sistema.
"""
from tests.conftest import auth_headers_for


# ==========================================================================
# Solicitudes de insumos
# ==========================================================================
def test_agent_creates_and_lists_supply_request(client, clayton_branch, clayton_agent, clayton_device):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    res = client.post(
        "/api/ops/requests",
        json={"branch_id": clayton_branch.id, "item_name": "Servilletas", "quantity_hint": "3 paquetes"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "open"

    res_list = client.get(f"/api/ops/requests?branch_id={clayton_branch.id}", headers=headers)
    assert res_list.status_code == 200
    assert any(r["item_name"] == "Servilletas" for r in res_list.json())


def test_agent_cannot_request_supplies_for_another_branch(client, clayton_agent, clayton_device, obarrio_branch):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    res = client.post(
        "/api/ops/requests",
        json={"branch_id": obarrio_branch.id, "item_name": "Vasos"},
        headers=headers,
    )
    assert res.status_code == 403


def test_supply_request_status_transition_sets_resolved_fields(client, clayton_branch, clayton_agent, clayton_device):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    created = client.post(
        "/api/ops/requests",
        json={"branch_id": clayton_branch.id, "item_name": "Guantes"},
        headers=headers,
    ).json()

    res = client.post(f"/api/ops/requests/{created['id']}/status?status=fulfilled", headers=headers)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "fulfilled"
    assert body["resolved_by_user_id"] is not None
    assert body["resolved_at"] is not None


# ==========================================================================
# Incidencias
# ==========================================================================
def test_create_incident_and_resolve_it(client, clayton_branch, clayton_agent, clayton_device):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    res = client.post(
        "/api/ops/incidents",
        json={"branch_id": clayton_branch.id, "title": "Nevera dañada", "severity": "alta"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    incident_id = res.json()["id"]
    assert res.json()["status"] == "abierta"

    res_update = client.post(
        f"/api/ops/incidents/{incident_id}/status",
        json={"status": "resuelta", "resolution_notes": "Se llamó al técnico"},
        headers=headers,
    )
    assert res_update.status_code == 200, res_update.text
    body = res_update.json()
    assert body["status"] == "resuelta"
    assert body["resolution_notes"] == "Se llamó al técnico"
    assert body["resolved_by_user_id"] is not None


def test_invalid_severity_rejected(client, clayton_branch, clayton_agent, clayton_device):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    res = client.post(
        "/api/ops/incidents",
        json={"branch_id": clayton_branch.id, "title": "Algo raro", "severity": "extrema"},
        headers=headers,
    )
    assert res.status_code == 400


# ==========================================================================
# Tareas
# ==========================================================================
def test_create_assign_and_complete_task(client, clayton_branch, clayton_agent, clayton_device, supervisor_user):
    headers_agent = auth_headers_for(clayton_agent, clayton_device.device_id)
    res = client.post(
        "/api/ops/tasks",
        json={"branch_id": clayton_branch.id, "title": "Limpiar cámara fría", "assigned_to_user_id": supervisor_user.id},
        headers=headers_agent,
    )
    assert res.status_code == 201, res.text
    task = res.json()
    assert task["assigned_to_user_id"] == supervisor_user.id
    assert task["status"] == "pendiente"

    res_done = client.post(
        f"/api/ops/tasks/{task['id']}/status",
        json={"status": "hecha"},
        headers=headers_agent,
    )
    assert res_done.status_code == 200, res_done.text
    assert res_done.json()["status"] == "hecha"
    assert res_done.json()["completed_at"] is not None


def test_supervisor_local_cannot_create_task_for_other_branch(client, supervisor_user, obarrio_branch):
    headers = auth_headers_for(supervisor_user)
    res = client.post(
        "/api/ops/tasks",
        json={"branch_id": obarrio_branch.id, "title": "Tarea ajena"},
        headers=headers,
    )
    assert res.status_code == 403


def test_admin_sees_tasks_across_branches(client, admin_user, clayton_branch, obarrio_branch, clayton_agent, clayton_device):
    headers_agent = auth_headers_for(clayton_agent, clayton_device.device_id)
    client.post("/api/ops/tasks", json={"branch_id": clayton_branch.id, "title": "Tarea Clayton"}, headers=headers_agent)

    headers_admin = auth_headers_for(admin_user)
    res = client.get("/api/ops/tasks", headers=headers_admin)
    assert res.status_code == 200
    assert any(t["title"] == "Tarea Clayton" for t in res.json())
