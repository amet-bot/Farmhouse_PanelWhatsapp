"""
Fase 2 del plan Farmhouse Link: catálogo de permisos por capacidad, ortogonal al alcance por
sucursal (eso lo cubren test_branch_isolation.py y test_branch_scope_fixes.py). Acá se prueba
1) que el catálogo resuelve lo esperado por rol, y 2) que los endpoints ya migrados
(users.manage, devices.manage) siguen bloqueando a quien no debe, con el mismo criterio de antes
(require_role(["admin"])) pero ahora vía permiso.
"""
from security.permissions import PERMISSIONS, has_permission, resolve_permissions
from tests.conftest import auth_headers_for


def test_admin_has_every_permission_in_the_catalog():
    assert resolve_permissions("admin") == PERMISSIONS


def test_agent_lacks_admin_only_capabilities():
    agent_perms = resolve_permissions("agent")
    for code in ("users.manage", "devices.manage", "integrations.manage", "customers.edit", "inventory.adjust"):
        assert code not in agent_perms, f"'{code}' no debería estar en los permisos de agente"
    assert "orders.create" in agent_perms
    assert "internal_chat.use" in agent_perms


def test_supervisor_has_more_than_agent_but_not_admin_only():
    supervisor_perms = resolve_permissions("supervisor")
    agent_perms = resolve_permissions("agent")
    assert agent_perms <= supervisor_perms  # todo lo del agente, y más
    assert "customers.edit" in supervisor_perms
    assert "reports.view" in supervisor_perms
    assert "users.manage" not in supervisor_perms
    assert "devices.manage" not in supervisor_perms


def test_unknown_role_has_no_permissions():
    assert resolve_permissions("lo-que-sea") == set()


def test_has_permission_helper_matches_resolve_permissions(clayton_agent, admin_user):
    assert has_permission(clayton_agent, "orders.create") is True
    assert has_permission(clayton_agent, "users.manage") is False
    assert has_permission(admin_user, "users.manage") is True


def test_login_and_me_expose_permissions_list(client, admin_user, clayton_agent):
    res = client.post(
        "/api/auth/login",
        json={"username": admin_user.username, "password": "Admin123!"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert res.status_code == 200, res.text
    perms = res.json()["user"]["permissions"]
    assert "users.manage" in perms
    assert "devices.manage" in perms

    headers = auth_headers_for(admin_user)
    res_me = client.get("/api/auth/me", headers=headers)
    assert res_me.status_code == 200
    assert "users.manage" in res_me.json()["permissions"]


def test_agent_still_forbidden_from_managing_users(client, clayton_agent, clayton_device):
    headers = auth_headers_for(clayton_agent, clayton_device.device_id)
    res = client.post(
        "/api/users/",
        json={"username": "nuevo_agente", "name": "Nuevo Agente", "password": "Agent123!", "role": "agent"},
        headers=headers,
    )
    assert res.status_code == 403


def test_supervisor_still_forbidden_from_managing_devices(client, supervisor_user, clayton_device, clayton_branch):
    headers = auth_headers_for(supervisor_user, clayton_device.device_id)
    res = client.post(
        "/api/devices/",
        json={"name": "Tablet Nueva", "device_type": "tablet", "branch_id": clayton_branch.id},
        headers=headers,
    )
    assert res.status_code == 403


def test_admin_can_still_manage_users_and_devices(client, admin_user, clayton_branch):
    headers = auth_headers_for(admin_user)
    res_user = client.post(
        "/api/users/",
        json={"username": "otro_agente", "name": "Otro Agente", "password": "Agent123!", "role": "agent", "branch_id": clayton_branch.id},
        headers=headers,
    )
    assert res_user.status_code == 200, res_user.text

    res_device = client.post(
        "/api/devices/",
        json={"name": "Tablet Admin Test", "device_type": "tablet", "branch_id": clayton_branch.id},
        headers=headers,
    )
    assert res_device.status_code == 200, res_device.text
