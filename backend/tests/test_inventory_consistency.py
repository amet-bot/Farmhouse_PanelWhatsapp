"""
Existencias, conteo y libro de movimientos tienen que decir lo mismo: el consumo registrado resta
en las tres, borrarlo respeta los conteos posteriores y el análisis no lo descuenta dos veces.
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from models.inventory_item import InventoryItem
from models.inventory_movement import InventoryMovement
from routers import inventory as inv
from tests.conftest import auth_headers_for


@pytest.fixture
def setup(client, db_session, clayton_branch, clayton_agent, clayton_device, supervisor_user):
    item = InventoryItem(name="Pollo", unit="kg")
    db_session.add(item); db_session.commit(); db_session.refresh(item)
    h = auth_headers_for(clayton_agent, clayton_device.device_id)
    hs = auth_headers_for(supervisor_user, clayton_device.device_id)
    b = clayton_branch.id
    r = client.post("/api/inventory/shipments", json={"branch_id": b, "items": [{"inventory_item_id": item.id, "quantity": "20"}]}, headers=h)
    assert r.status_code == 201, r.text
    return client, item, b, h, hs


def _consumo(client, b, item, h, qty="5", **extra):
    return client.post("/api/inventory/consumption", json={"branch_id": b, "items": [{"inventory_item_id": item.id, "quantity": qty}], **extra}, headers=h)


def test_existencias_conteo_y_libro_coinciden_con_consumo(setup):
    client, item, b, h, hs = setup
    assert _consumo(client, b, item, h).status_code == 201
    fila = client.get(f"/api/inventory/stock?branch_id={b}", headers=h).json()[0]
    assert Decimal(fila["on_hand"]) == 15 and Decimal(fila["consumed"]) == 5
    cmp = client.get(f"/api/inventory/movements/compare?branch_id={b}", headers=h).json()[0]
    assert cmp["matches"] and Decimal(cmp["on_hand_movements"]) == 15
    conteo = client.post("/api/inventory/counts", json={"branch_id": b, "items": [{"inventory_item_id": item.id, "counted_quantity": "15"}]}, headers=h).json()
    assert Decimal(conteo["items"][0]["expected_quantity"]) == 15 and conteo["mismatched_count"] == 0


def test_solo_consumo_aparece_en_only_stocked(client, db_session, clayton_branch, clayton_agent, clayton_device):
    item = InventoryItem(name="Servilletas", unit="paquete")
    db_session.add(item); db_session.commit(); db_session.refresh(item)
    h = auth_headers_for(clayton_agent, clayton_device.device_id)
    assert _consumo(client, clayton_branch.id, item, h, "2").status_code == 201
    filas = client.get(f"/api/inventory/stock?branch_id={clayton_branch.id}&only_stocked=true", headers=h).json()
    assert [f["item_name"] for f in filas] == ["Servilletas"] and Decimal(filas[0]["on_hand"]) == -2


def test_borrar_consumo_quita_su_movimiento_y_respeta_conteo_posterior(setup, db_session):
    client, item, b, h, hs = setup
    rec = _consumo(client, b, item, h).json()
    assert db_session.query(InventoryMovement).filter_by(source_type="consumption", source_id=rec["id"]).count() == 1
    # Sin conteo posterior: se borra y el movimiento se va con él.
    assert client.delete(f"/api/inventory/consumption/{rec['id']}", headers=hs).status_code == 200
    assert db_session.query(InventoryMovement).filter_by(source_type="consumption").count() == 0
    # Con conteo posterior: no se borra.
    rec2 = _consumo(client, b, item, h, "3").json()
    client.post("/api/inventory/counts", json={"branch_id": b, "items": [{"inventory_item_id": item.id, "counted_quantity": "17"}]}, headers=h)
    assert client.delete(f"/api/inventory/consumption/{rec2['id']}", headers=hs).status_code == 409
    assert Decimal(client.get(f"/api/inventory/stock?branch_id={b}", headers=h).json()[0]["on_hand"]) == 17


def test_consumo_con_fecha_anterior_al_conteo_se_rechaza(setup):
    client, item, b, h, hs = setup
    client.post("/api/inventory/counts", json={"branch_id": b, "items": [{"inventory_item_id": item.id, "counted_quantity": "20"}]}, headers=h)
    ayer = (datetime.utcnow() - timedelta(days=1)).isoformat()
    assert _consumo(client, b, item, h, "4", occurred_at=ayer).status_code == 409
    assert _consumo(client, b, item, h, "4").status_code == 201


def test_analisis_no_descuenta_dos_veces_el_consumo_a_mano(setup, db_session, monkeypatch):
    client, item, b, h, hs = setup
    client.post("/api/inventory/counts", json={"branch_id": b, "items": [{"inventory_item_id": item.id, "counted_quantity": "20"}]}, headers=h)
    _consumo(client, b, item, h, "6")
    monkeypatch.setattr(inv, "_insumos_con_receta", lambda db, branch_id: {item.id})
    monkeypatch.setattr(inv, "_recetas_de_sucursal", lambda db, branch_id: ({}, {}))
    monkeypatch.setattr(inv, "_uso_por_ventas", lambda db, branch_id, desde, hasta, **kw: {item.id: Decimal("4")})
    c = client.post("/api/inventory/counts", json={"branch_id": b, "items": [{"inventory_item_id": item.id, "counted_quantity": "14"}]}, headers=h).json()
    linea = c["analysis"]["lines"][0]
    assert Decimal(linea["used_by_sales"]) == 0 and linea["status"] == "cuadra"
