"""
Gerente de logística = supervisor SIN sucursal. Ve y opera todas las sucursales (abastecimiento,
tareas, Centro de operación, reportes) pero no administra usuarios, dispositivos, integraciones
ni respaldos. Estas pruebas fijan ese límite para que nadie lo mueva sin querer.
"""
import pytest

from models.user import User
from security.auth import get_password_hash
from security.permissions import has_permission
from tests.conftest import auth_headers_for


@pytest.fixture
def gerente(db_session):
    u = User(username="gerente.logistica", name="Gerente Logística", email="logistica@farmhouse.pa",
             password_hash=get_password_hash("Logistica123!"), role="supervisor", branch_id=None, active=True)
    db_session.add(u); db_session.commit(); db_session.refresh(u)
    return u


def test_el_gerente_opera_todas_las_sucursales(client, gerente, clayton_branch, obarrio_branch, clayton_device):
    # Como todo supervisor, entra desde un dispositivo registrado (de cualquier sucursal).
    h = auth_headers_for(gerente, clayton_device.device_id)
    ids = {b["id"] for b in client.get("/api/supply/stock", headers=h).json()["branches"]}
    assert {clayton_branch.id, obarrio_branch.id} <= ids
    assert {f["branch"]["id"] for f in client.get("/api/supply/setup", headers=h).json()["branches"]} >= {clayton_branch.id, obarrio_branch.id}
    assert client.get("/api/ops/overview", headers=h).status_code == 200

    # Manda tareas a cualquier sucursal.
    for b in (clayton_branch, obarrio_branch):
        assert client.post("/api/ops/tasks", json={"branch_id": b.id, "title": "Revisar cámara fría"}, headers=h).status_code == 201

    for permiso in ("inventory.adjust", "purchasing.approve", "reports.view", "inventory.transfer"):
        assert has_permission(gerente, permiso)


def test_el_gerente_no_administra_el_sistema(client, gerente, clayton_branch, clayton_device):
    h = auth_headers_for(gerente, clayton_device.device_id)
    for permiso in ("users.manage", "devices.manage", "integrations.manage", "system.backup"):
        assert not has_permission(gerente, permiso)
    assert client.post("/api/users/", json={"username": "x1", "name": "X", "password": "abcd1234", "role": "agent", "branch_id": clayton_branch.id}, headers=h).status_code == 403
    assert client.get("/api/system/backups", headers=h).status_code == 403
