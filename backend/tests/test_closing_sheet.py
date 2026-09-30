"""
Hoja de cierre de turno: el encargado arma la lista, el operario escribe cuánto queda y el
sistema calcula lo gastado. Cada cierre es un conteo kind='closing' que fija la existencia y
alimenta el ritmo de uso del pedido sugerido.
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from models.audit import AuditEvent
from models.inventory_item import InventoryItem
from models.shipment import Shipment, ShipmentItem
from models.stock_count import StockCount
from models.supply import ItemBranchSetting
from routers import inventory as inv
from routers import supply
from tests.conftest import auth_headers_for


@pytest.fixture
def insumos(db_session, clayton_branch, supervisor_user):
    pollo = InventoryItem(name="Pollo", unit="kg", category="Proteínas", reference_cost=Decimal("3.00"))
    bolsa = InventoryItem(name="Bolsa con logo", unit="unidad", category="Packaging")
    db_session.add_all([pollo, bolsa]); db_session.commit()
    sh = Shipment(branch_id=clayton_branch.id, received_by_user_id=supervisor_user.id, received_at=datetime.utcnow() - timedelta(days=3))
    sh.items.append(ShipmentItem(inventory_item_id=pollo.id, quantity=Decimal("20"), unit_cost=Decimal("2.50")))
    sh.items.append(ShipmentItem(inventory_item_id=bolsa.id, quantity=Decimal("100"), unit_cost=Decimal("0.10")))
    db_session.add(sh); db_session.commit()
    return pollo, bolsa


def test_solo_un_encargado_arma_la_hoja_y_el_operario_la_ve_en_orden(client, db_session, clayton_branch, clayton_agent, clayton_device, supervisor_user, insumos):
    pollo, bolsa = insumos
    ha = auth_headers_for(clayton_agent, clayton_device.device_id)
    hs = auth_headers_for(supervisor_user, clayton_device.device_id)

    vacia = client.get("/api/inventory/closing-sheet", headers=ha).json()
    assert vacia["configured"] is False and vacia["items"] == [] and vacia["can_configure"] is False

    assert client.put("/api/inventory/closing-sheet/config", json={"branch_id": clayton_branch.id, "item_ids": [bolsa.id, pollo.id]}, headers=ha).status_code == 403
    assert client.put("/api/inventory/closing-sheet/config", json={"branch_id": clayton_branch.id, "item_ids": [9999]}, headers=hs).status_code == 404
    r = client.put("/api/inventory/closing-sheet/config", json={"branch_id": clayton_branch.id, "item_ids": [bolsa.id, pollo.id]}, headers=hs)
    assert r.status_code == 200 and r.json()["items"] == 2

    hoja = client.get("/api/inventory/closing-sheet", headers=ha).json()
    assert hoja["configured"] is True and [i["name"] for i in hoja["items"]] == ["Bolsa con logo", "Pollo"]
    assert hoja["items"][0]["last_counted_qty"] is None and hoja["last_closing"] is None
    por_id = {i["inventory_item_id"]: i for i in hoja["items"]}
    assert por_id[pollo.id]["unit_family"] == "peso" and float(por_id[pollo.id]["unit_base"]) == 1000 and por_id[pollo.id]["piece_size"] is None
    assert por_id[bolsa.id]["unit_family"] == "unidad" and float(por_id[bolsa.id]["unit_base"]) == 1
    assert db_session.query(AuditEvent).filter(AuditEvent.action == "closing_sheet.config").count() == 1

    # Reemplaza la lista entera: quitar la bolsa la saca de la hoja sin borrar su fila de ajustes.
    client.put("/api/inventory/closing-sheet/config", json={"branch_id": clayton_branch.id, "item_ids": [pollo.id]}, headers=hs)
    assert [i["name"] for i in client.get("/api/inventory/closing-sheet", headers=ha).json()["items"]] == ["Pollo"]
    fila = db_session.query(ItemBranchSetting).filter_by(branch_id=clayton_branch.id, inventory_item_id=bolsa.id).one()
    assert fila.on_closing_sheet is False and fila.sheet_position is None


def test_cerrar_turno_calcula_lo_gastado_y_fija_la_existencia(client, db_session, clayton_branch, clayton_agent, clayton_device, supervisor_user, insumos):
    pollo, bolsa = insumos
    ha = auth_headers_for(clayton_agent, clayton_device.device_id)
    hs = auth_headers_for(supervisor_user, clayton_device.device_id)
    client.put("/api/inventory/closing-sheet/config", json={"branch_id": clayton_branch.id, "item_ids": [pollo.id, bolsa.id]}, headers=hs)

    # Insumo fuera de la hoja: no se acepta.
    otro = InventoryItem(name="Aceite", unit="L"); db_session.add(otro); db_session.commit()
    assert client.post("/api/inventory/closing-sheet", json={"branch_id": clayton_branch.id, "lines": [{"inventory_item_id": otro.id, "counted_quantity": "1"}]}, headers=ha).status_code == 400

    r = client.post("/api/inventory/closing-sheet", json={"branch_id": clayton_branch.id, "notes": "turno tarde", "lines": [
        {"inventory_item_id": pollo.id, "counted_quantity": "15.5"}, {"inventory_item_id": bolsa.id, "counted_quantity": "80"},
    ]}, headers=ha)
    assert r.status_code == 201, r.text
    d = r.json()
    por_nombre = {l["item_name"]: l for l in d["lines"]}
    assert float(por_nombre["Pollo"]["used"]) == 4.5 and float(por_nombre["Pollo"]["used_cost"]) == 11.25 and float(por_nombre["Pollo"]["stock_after"]) == 15.5
    assert float(por_nombre["Bolsa con logo"]["used"]) == 20 and float(por_nombre["Bolsa con logo"]["used_cost"]) == 2.0
    assert float(d["used_cost"]) == 13.25 and d["is_first_count"] is True
    assert inv._existencia_map(db_session, clayton_branch.id, [pollo.id, bolsa.id]) == {pollo.id: Decimal("15.5"), bolsa.id: Decimal("80")}
    conteo = db_session.get(StockCount, d["id"])
    assert conteo.kind == "closing" and conteo.notes == "turno tarde"
    assert db_session.query(AuditEvent).filter(AuditEvent.action == "closing_sheet.create").count() == 1

    # La hoja ahora muestra lo del último cierre y lo que llegó después.
    sh = Shipment(branch_id=clayton_branch.id, received_by_user_id=supervisor_user.id, received_at=datetime.utcnow() + timedelta(seconds=1))
    sh.items.append(ShipmentItem(inventory_item_id=pollo.id, quantity=Decimal("10"), unit_cost=Decimal("2.60")))
    db_session.add(sh); db_session.commit()
    hoja = client.get("/api/inventory/closing-sheet", headers=ha).json()
    p = next(i for i in hoja["items"] if i["inventory_item_id"] == pollo.id)
    assert float(p["last_counted_qty"]) == 15.5 and float(p["received_since"]) == 10 and hoja["last_closing"]["items"] == 2 and hoja["last_closing"]["by"] == clayton_agent.name
    assert p["last_used"] is None   # primer conteo: su diferencia es el arranque, no un gasto

    # Segundo cierre: 15.5 + 10 llegados − 22 contados = 3.5 gastados. Aparece en el historial.
    r2 = client.post("/api/inventory/closing-sheet", json={"branch_id": clayton_branch.id, "lines": [{"inventory_item_id": pollo.id, "counted_quantity": "22"}]}, headers=ha).json()
    assert float(r2["lines"][0]["used"]) == 3.5 and r2["is_first_count"] is False
    p2 = next(i for i in client.get("/api/inventory/closing-sheet", headers=ha).json()["items"] if i["inventory_item_id"] == pollo.id)
    assert float(p2["last_used"]) == 3.5 and float(p2["last_counted_qty"]) == 22
    hist = client.get("/api/inventory/closing-sheet/history", headers=ha).json()
    assert len(hist) == 2 and float(hist[0]["lines"][0]["used"]) == 3.5

    # El ritmo de uso del pedido sugerido sale de los cierres: 4.5 + 3.5 = 8 kg en 14 días.
    ritmo = supply.usage_per_day(db_session, clayton_branch.id, [pollo.id, bolsa.id])
    assert ritmo[pollo.id] == Decimal("8") / Decimal("14") and ritmo[bolsa.id] == Decimal("20") / Decimal("14")


def test_el_operario_no_cierra_otra_sucursal(client, clayton_branch, obarrio_branch, clayton_agent, clayton_device, supervisor_user, insumos):
    pollo, _ = insumos
    ha = auth_headers_for(clayton_agent, clayton_device.device_id)
    assert client.post("/api/inventory/closing-sheet", json={"branch_id": obarrio_branch.id, "lines": [{"inventory_item_id": pollo.id, "counted_quantity": "1"}]}, headers=ha).status_code == 403
    # Un supervisor de Clayton tampoco arma la hoja de Obarrio.
    hs = auth_headers_for(supervisor_user, clayton_device.device_id)
    assert client.put("/api/inventory/closing-sheet/config", json={"branch_id": obarrio_branch.id, "item_ids": [pollo.id]}, headers=hs).status_code == 403
