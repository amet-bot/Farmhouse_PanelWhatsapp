"""
Borrar un cargamento cargado por error: quien lo cargó durante 24 horas, o supervisor/admin. La
existencia vuelve atrás y la auditoría guarda qué se borró. No se borra si después se contó.
"""
from datetime import datetime, timedelta

from models.audit import AuditEvent
from models.inventory_movement import InventoryMovement
from models.shipment import Shipment
from tests.conftest import auth_headers_for


def _h(user, device=None):
    return auth_headers_for(user, device.device_id if device else None)


def _cargamento(client, h, branch_id, nombre="Harina", qty="10"):
    item = client.post("/api/inventory/items", json={"name": nombre, "unit": "kg"}, headers=h).json()
    res = client.post("/api/inventory/shipments", json={
        "branch_id": branch_id,
        "items": [{"inventory_item_id": item["id"], "quantity": qty, "unit_cost": "1.50"}],
    }, headers=h)
    assert res.status_code == 201, res.text
    return res.json(), item


def _stock(client, h, branch_id, item_id):
    filas = client.get(f"/api/inventory/stock?branch_id={branch_id}", headers=h).json()
    fila = next((r for r in filas if r["inventory_item_id"] == item_id), None)
    return float(fila["on_hand"]) if fila else 0.0


def test_quien_lo_cargo_lo_borra_y_la_existencia_vuelve_atras(client, db_session, clayton_branch, clayton_agent, clayton_device):
    h = _h(clayton_agent, clayton_device)
    cargamento, item = _cargamento(client, h, clayton_branch.id)
    assert _stock(client, h, clayton_branch.id, item["id"]) == 10

    res = client.delete(f"/api/inventory/shipments/{cargamento['id']}?motivo=Lo cargué dos veces", headers=h)
    assert res.status_code == 204, res.text
    assert _stock(client, h, clayton_branch.id, item["id"]) == 0
    assert db_session.query(Shipment).count() == 0
    assert db_session.query(InventoryMovement).filter(InventoryMovement.source_type == "shipment").count() == 0
    evento = db_session.query(AuditEvent).filter(AuditEvent.action == "shipment.delete").one()
    assert "dos veces" in evento.metadata_json


def test_pasadas_24_horas_solo_lo_borra_un_supervisor(client, db_session, clayton_branch, clayton_agent,
                                                     clayton_device, supervisor_user):
    cargamento, _ = _cargamento(client, _h(clayton_agent, clayton_device), clayton_branch.id)
    rec = db_session.get(Shipment, cargamento["id"])
    rec.created_at = datetime.utcnow() - timedelta(hours=25)
    db_session.commit()
    res = client.delete(f"/api/inventory/shipments/{cargamento['id']}", headers=_h(clayton_agent, clayton_device))
    assert res.status_code == 403 and "24 horas" in res.json()["detail"]
    assert client.delete(f"/api/inventory/shipments/{cargamento['id']}",
                         headers=_h(supervisor_user, clayton_device)).status_code == 204


def test_no_se_borra_si_despues_se_conto(client, db_session, clayton_branch, supervisor_user, clayton_device):
    h = _h(supervisor_user, clayton_device)
    cargamento, item = _cargamento(client, h, clayton_branch.id, nombre="Azúcar")
    rec = db_session.get(Shipment, cargamento["id"])
    rec.created_at = datetime.utcnow() - timedelta(minutes=5)   # el conteo viene después
    db_session.commit()
    client.post("/api/inventory/counts", json={
        "branch_id": clayton_branch.id, "items": [{"inventory_item_id": item["id"], "counted_quantity": "10"}],
    }, headers=h)
    res = client.delete(f"/api/inventory/shipments/{cargamento['id']}", headers=h)
    assert res.status_code == 409 and "Azúcar" in res.json()["detail"]
    assert _stock(client, h, clayton_branch.id, item["id"]) == 10    # nada se tocó


def test_la_merma_tampoco_se_borra_si_despues_se_conto(client, db_session, clayton_branch, supervisor_user, clayton_device):
    from models.waste import WasteRecord
    h = _h(supervisor_user, clayton_device)
    _, item = _cargamento(client, h, clayton_branch.id, nombre="Sal")
    merma = client.post("/api/inventory/waste", json={
        "branch_id": clayton_branch.id, "reason": "vencido",
        "items": [{"inventory_item_id": item["id"], "quantity": "1"}],
    }, headers=h).json()
    rec = db_session.get(WasteRecord, merma["id"])
    rec.created_at = datetime.utcnow() - timedelta(minutes=5)
    db_session.commit()
    client.post("/api/inventory/counts", json={
        "branch_id": clayton_branch.id, "items": [{"inventory_item_id": item["id"], "counted_quantity": "9"}],
    }, headers=h)
    assert client.delete(f"/api/inventory/waste/{merma['id']}", headers=h).status_code == 409


def test_otra_sucursal_no_puede_borrarlo(client, clayton_branch, clayton_agent, clayton_device, obarrio_agent, obarrio_device):
    cargamento, _ = _cargamento(client, _h(clayton_agent, clayton_device), clayton_branch.id, nombre="Arroz")
    assert client.delete(f"/api/inventory/shipments/{cargamento['id']}",
                         headers=_h(obarrio_agent, obarrio_device)).status_code == 403
