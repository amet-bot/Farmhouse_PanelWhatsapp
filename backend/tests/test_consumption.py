"""
Registro de consumo: el equipo anota lo que se usó, descuenta la existencia, y cuando hay
consumo a mano el sistema deja de estimar por ventas ese insumo. Borrar: 24 h o encargado.
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from models.audit import AuditEvent
from models.consumption import ConsumptionRecord
from models.inventory_item import InventoryItem
from models.shipment import Shipment, ShipmentItem
from models.stock_count import StockCount, StockCountItem
from routers import inventory as inv
from tests.conftest import auth_headers_for


@pytest.fixture
def pollo(db_session, clayton_branch, supervisor_user):
    item = InventoryItem(name="Pollo", unit="kg", category="Proteínas", reference_cost=Decimal("3.00"))
    db_session.add(item); db_session.commit(); db_session.refresh(item)
    sh = Shipment(branch_id=clayton_branch.id, received_by_user_id=supervisor_user.id, received_at=datetime.utcnow() - timedelta(days=2))
    sh.items.append(ShipmentItem(inventory_item_id=item.id, quantity=Decimal("20"), unit_cost=Decimal("2.50")))
    db_session.add(sh); db_session.commit()
    return item


def test_registrar_consumo_descuenta_la_existencia(client, db_session, clayton_branch, clayton_agent, clayton_device, pollo):
    h = auth_headers_for(clayton_agent, clayton_device.device_id)
    r = client.post("/api/inventory/consumption", json={"branch_id": clayton_branch.id, "notes": "turno mañana", "items": [{"inventory_item_id": pollo.id, "quantity": "5"}]}, headers=h)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["items"][0]["item_name"] == "Pollo" and float(d["items"][0]["unit_cost"]) == 2.5 and float(d["items"][0]["cost"]) == 12.5
    assert float(d["items"][0]["stock_after"]) == 15.0 and float(d["total_cost"]) == 12.5 and d["can_delete"] is True
    assert inv._existencia_map(db_session, clayton_branch.id, [pollo.id])[pollo.id] == Decimal("15")
    assert db_session.query(AuditEvent).filter(AuditEvent.action == "consumption.create").count() == 1

    lista = client.get(f"/api/inventory/consumption?branch_id={clayton_branch.id}", headers=h).json()
    assert len(lista) == 1 and lista[0]["notes"] == "turno mañana"
    resumen = client.get(f"/api/inventory/consumption/summary?branch_id={clayton_branch.id}", headers=h).json()
    assert resumen["rows"][0]["name"] == "Pollo" and float(resumen["rows"][0]["quantity"]) == 5.0 and float(resumen["total_cost"]) == 12.5


def test_no_se_registra_en_otra_sucursal_ni_insumo_inexistente(client, clayton_branch, obarrio_branch, clayton_agent, clayton_device, pollo):
    h = auth_headers_for(clayton_agent, clayton_device.device_id)
    assert client.post("/api/inventory/consumption", json={"branch_id": obarrio_branch.id, "items": [{"inventory_item_id": pollo.id, "quantity": "1"}]}, headers=h).status_code == 403
    assert client.post("/api/inventory/consumption", json={"branch_id": clayton_branch.id, "items": [{"inventory_item_id": 9999, "quantity": "1"}]}, headers=h).status_code == 404
    assert client.post("/api/inventory/consumption", json={"branch_id": clayton_branch.id, "items": [{"inventory_item_id": pollo.id, "quantity": "1"}, {"inventory_item_id": pollo.id, "quantity": "2"}]}, headers=h).status_code == 400


def test_borrar_solo_dentro_de_24h_o_encargado(client, db_session, clayton_branch, clayton_agent, clayton_device, supervisor_user, pollo):
    ha = auth_headers_for(clayton_agent, clayton_device.device_id)
    rec = client.post("/api/inventory/consumption", json={"branch_id": clayton_branch.id, "items": [{"inventory_item_id": pollo.id, "quantity": "2"}]}, headers=ha).json()
    viejo = db_session.get(ConsumptionRecord, rec["id"])
    viejo.created_at = datetime.utcnow() - timedelta(hours=30)
    db_session.commit()
    assert client.delete(f"/api/inventory/consumption/{rec['id']}", headers=ha).status_code == 403
    hs = auth_headers_for(supervisor_user, clayton_device.device_id)
    assert client.delete(f"/api/inventory/consumption/{rec['id']}", headers=hs).status_code == 200
    assert inv._existencia_map(db_session, clayton_branch.id, [pollo.id])[pollo.id] == Decimal("20")


def test_con_consumo_a_mano_no_se_estima_por_ventas(db_session, clayton_branch, supervisor_user, pollo, monkeypatch):
    """Un insumo con receta y conteo: sin consumo a mano se le resta lo vendido; con consumo a
    mano desde el conteo, manda el registro y no se descuenta dos veces."""
    conteo = StockCount(branch_id=clayton_branch.id, counted_by_user_id=supervisor_user.id, counted_at=datetime.utcnow() - timedelta(days=1))
    conteo.items.append(StockCountItem(inventory_item_id=pollo.id, expected_quantity=Decimal("20"), counted_quantity=Decimal("20"), difference=Decimal("0")))
    db_session.add(conteo); db_session.commit()
    monkeypatch.setattr(inv, "_insumos_con_receta", lambda db, branch_id: {pollo.id})
    monkeypatch.setattr(inv, "_recetas_de_sucursal", lambda db, branch_id: ({}, {}))
    monkeypatch.setattr(inv, "_uso_por_ventas", lambda db, branch_id, desde, hasta, **kw: {pollo.id: Decimal("4")})

    assert inv._existencia_map(db_session, clayton_branch.id, [pollo.id])[pollo.id] == Decimal("16")   # 20 − 4 estimados

    rec = ConsumptionRecord(branch_id=clayton_branch.id, recorded_by_user_id=supervisor_user.id, occurred_at=datetime.utcnow())
    from models.consumption import ConsumptionItem
    rec.items.append(ConsumptionItem(inventory_item_id=pollo.id, quantity=Decimal("6"), unit_cost=Decimal("2.5")))
    db_session.add(rec); db_session.commit()
    assert inv._existencia_map(db_session, clayton_branch.id, [pollo.id])[pollo.id] == Decimal("14")   # 20 − 6 a mano, sin los 4 estimados
