"""
CRUD de sucursales para el panel de Administración (post-plan, no era parte del prompt de 22
secciones). El endpoint público GET /branches/ no se toca — sigue sin login y solo activas, para
no romper selectores existentes (Menú Digital, etc.). Todo lo nuevo es admin-only.
"""
from tests.conftest import auth_headers_for


def test_public_endpoint_still_unauthenticated_and_active_only(client, clayton_branch, db_session):
    from models.branch import Branch
    inactive = Branch(name="Cerrada", code="CER", active=False, accepts_delivery=False)
    db_session.add(inactive)
    db_session.commit()

    res = client.get("/api/branches/")
    assert res.status_code == 200
    names = [b["name"] for b in res.json()]
    assert "Clayton" in names
    assert "Cerrada" not in names


def test_agent_cannot_create_branch(client, clayton_agent, clayton_device):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    res = client.post("/api/branches/", json={"name": "Nueva", "code": "NEW"}, headers=headers)
    assert res.status_code == 403


def test_supervisor_cannot_create_branch(client, supervisor_user, clayton_device):
    headers = auth_headers_for(supervisor_user, clayton_device.device_id)
    res = client.post("/api/branches/", json={"name": "Nueva", "code": "NEW"}, headers=headers)
    assert res.status_code == 403


def test_admin_can_create_update_and_toggle_branch(client, admin_user):
    headers = auth_headers_for(admin_user)
    res_create = client.post("/api/branches/", json={"name": "Punta Pacifica", "code": "PP"}, headers=headers)
    assert res_create.status_code == 201, res_create.text
    branch_id = res_create.json()["id"]
    assert res_create.json()["active"] is True

    res_update = client.put(f"/api/branches/{branch_id}", json={"address": "Calle 123"}, headers=headers)
    assert res_update.status_code == 200, res_update.text
    assert res_update.json()["address"] == "Calle 123"
    assert res_update.json()["name"] == "Punta Pacifica"  # no tocado, sigue igual

    res_toggle = client.post(f"/api/branches/{branch_id}/toggle-active", headers=headers)
    assert res_toggle.status_code == 200
    assert res_toggle.json()["active"] is False

    # Ya no aparece en el endpoint público...
    res_public = client.get("/api/branches/")
    assert "Punta Pacifica" not in [b["name"] for b in res_public.json()]
    # ...pero sí en el admin.
    res_admin = client.get("/api/branches/admin", headers=headers)
    assert "Punta Pacifica" in [b["name"] for b in res_admin.json()]


def test_duplicate_name_or_code_rejected(client, admin_user, clayton_branch):
    headers = auth_headers_for(admin_user)
    res = client.post("/api/branches/", json={"name": "Clayton", "code": "OTRO"}, headers=headers)
    assert res.status_code == 400

    res2 = client.post("/api/branches/", json={"name": "Otra", "code": "CLY"}, headers=headers)
    assert res2.status_code == 400
