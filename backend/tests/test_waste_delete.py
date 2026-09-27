"""
Borrar una merma cargada por error: quien la cargó dentro de las 24 horas, o un supervisor/admin.
La existencia vuelve a quedar como si nunca se hubiera cargado y la auditoría guarda qué se borró.
"""
from datetime import datetime, timedelta

from models.audit import AuditEvent
from models.inventory_movement import InventoryMovement
from models.user import User
from models.waste import WasteRecord, WastePhoto
from security.auth import get_password_hash
from tests.conftest import auth_headers_for

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _headers(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def _merma(client, headers, branch_id, qty="2"):
    item = client.post("/api/inventory/items", json={"name": "Mango", "unit": "kg"}, headers=headers).json()
    res = client.post("/api/inventory/waste", json={
        "branch_id": branch_id, "reason": "vencido",
        "items": [{"inventory_item_id": item["id"], "quantity": qty}],
    }, headers=headers)
    assert res.status_code == 201, res.text
    return res.json(), item


def test_quien_la_cargo_la_puede_borrar_y_todo_vuelve_atras(client, db_session, clayton_branch,
                                                             clayton_agent, clayton_device):
    h = _headers(clayton_agent, clayton_device)
    merma, item = _merma(client, h, clayton_branch.id)
    client.post(f"/api/inventory/waste/{merma['id']}/photos", files={"file": ("x.jpg", JPEG, "image/jpeg")}, headers=h)

    res = client.delete(f"/api/inventory/waste/{merma['id']}?motivo=La cargué dos veces", headers=h)
    assert res.status_code == 204, res.text

    assert client.get("/api/inventory/waste", headers=h).json() == []
    assert db_session.query(WasteRecord).count() == 0
    assert db_session.query(WastePhoto).count() == 0
    assert db_session.query(InventoryMovement).filter(InventoryMovement.source_type == "waste").count() == 0

    stock = client.get(f"/api/inventory/stock?branch_id={clayton_branch.id}", headers=h).json()
    fila = next((r for r in stock if r["inventory_item_id"] == item["id"]), None)
    assert fila is None or float(fila["wasted"]) == 0

    evento = db_session.query(AuditEvent).filter(AuditEvent.action == "waste.delete").one()
    assert evento.entity_id == merma["id"]
    assert "dos veces" in evento.metadata_json


def test_otro_agente_no_puede_borrarla(client, db_session, clayton_branch, clayton_agent, clayton_device):
    merma, _ = _merma(client, _headers(clayton_agent, clayton_device), clayton_branch.id)
    otro = User(name="Otro Clayton", username="otro_clayton", email="otro.clayton@farmhouse.pa",
                password_hash=get_password_hash("x-Test-123"), role="agent", branch_id=clayton_branch.id,
                active=True)
    db_session.add(otro)
    db_session.commit()

    res = client.delete(f"/api/inventory/waste/{merma['id']}", headers=_headers(otro, clayton_device))
    assert res.status_code == 403
    assert db_session.query(WasteRecord).count() == 1


def test_pasadas_24_horas_solo_la_borra_un_supervisor(client, db_session, clayton_branch, clayton_agent,
                                                      clayton_device, supervisor_user):
    merma, _ = _merma(client, _headers(clayton_agent, clayton_device), clayton_branch.id)
    rec = db_session.get(WasteRecord, merma["id"])
    rec.created_at = datetime.utcnow() - timedelta(hours=25)
    db_session.commit()

    res = client.delete(f"/api/inventory/waste/{merma['id']}", headers=_headers(clayton_agent, clayton_device))
    assert res.status_code == 403
    assert "24 horas" in res.json()["detail"]

    res = client.delete(f"/api/inventory/waste/{merma['id']}", headers=_headers(supervisor_user, clayton_device))
    assert res.status_code == 204


def test_no_se_borra_una_merma_de_otra_sucursal(client, db_session, clayton_branch, clayton_agent, clayton_device,
                                                obarrio_agent, obarrio_device):
    merma, _ = _merma(client, _headers(clayton_agent, clayton_device), clayton_branch.id)
    res = client.delete(f"/api/inventory/waste/{merma['id']}", headers=_headers(obarrio_agent, obarrio_device))
    assert res.status_code == 403
    assert db_session.query(WasteRecord).count() == 1


def test_admin_borra_cualquiera(client, clayton_branch, clayton_agent, clayton_device, admin_user):
    merma, _ = _merma(client, _headers(clayton_agent, clayton_device), clayton_branch.id)
    assert client.delete(f"/api/inventory/waste/{merma['id']}", headers=_headers(admin_user)).status_code == 204
    assert client.delete(f"/api/inventory/waste/{merma['id']}", headers=_headers(admin_user)).status_code == 404
