"""
Reporte de faltante sin explicar: lo que se fue según las hojas de cierre menos lo que justifican
las ventas × recetas, por sucursal y valorizado. El arranque no cuenta; sin receta no se compara.
"""
from datetime import datetime, timedelta
from decimal import Decimal

from models.inventory_item import InventoryItem
from models.shipment import Shipment, ShipmentItem
from routers import inventory as inv
from tests.conftest import auth_headers_for


def test_faltante_sin_explicar_por_sucursal(client, db_session, admin_user, clayton_branch, obarrio_branch, supervisor_user, clayton_device, monkeypatch):
    pollo = InventoryItem(name="Pollo", unit="kilogramo")
    bolsa = InventoryItem(name="Bolsa", unit="unidad")
    db_session.add_all([pollo, bolsa]); db_session.commit()
    sh = Shipment(branch_id=clayton_branch.id, received_by_user_id=admin_user.id, received_at=datetime.utcnow() - timedelta(days=3))
    sh.items.append(ShipmentItem(inventory_item_id=pollo.id, quantity=Decimal("20"), unit_cost=Decimal("2.50")))
    sh.items.append(ShipmentItem(inventory_item_id=bolsa.id, quantity=Decimal("100"), unit_cost=Decimal("0.10")))
    db_session.add(sh); db_session.commit()

    hs = auth_headers_for(supervisor_user, clayton_device.device_id)
    client.put("/api/inventory/closing-sheet/config", json={"branch_id": clayton_branch.id, "item_ids": [pollo.id, bolsa.id]}, headers=hs)
    # Primer cierre = arranque (no cuenta). Segundo: se fueron 8 kg de pollo y 30 bolsas.
    client.post("/api/inventory/closing-sheet", json={"branch_id": clayton_branch.id, "lines": [{"inventory_item_id": pollo.id, "counted_quantity": "20"}, {"inventory_item_id": bolsa.id, "counted_quantity": "100"}]}, headers=hs)
    # Las ventas por receta explican 5 kg de pollo; la bolsa no tiene receta.
    monkeypatch.setattr(inv, "_recetas_de_sucursal", lambda db, branch_id: ({}, {}))
    monkeypatch.setattr(inv, "_uso_por_ventas", lambda db, branch_id, desde, hasta, **kw: {pollo.id: Decimal("5")})
    client.post("/api/inventory/closing-sheet", json={"branch_id": clayton_branch.id, "lines": [{"inventory_item_id": pollo.id, "counted_quantity": "12"}, {"inventory_item_id": bolsa.id, "counted_quantity": "70"}]}, headers=hs)

    d = client.get("/api/reports/inventory/variance", headers=auth_headers_for(admin_user)).json()
    cly = next(b for b in d["branches"] if b["branch"]["id"] == clayton_branch.id)
    obr = next(b for b in d["branches"] if b["branch"]["id"] == obarrio_branch.id)
    assert cly["closings"] == 2 and cly["compared"] == 1 and float(cly["missing_cost"]) == 7.5
    por = {r["name"]: r for r in cly["rows"]}
    p = por["Pollo"]
    assert (float(p["real"]), float(p["expected"]), float(p["diff"]), p["status"], float(p["diff_cost"]), p["closings"]) == (8.0, 5.0, 3.0, "faltante", 7.5, 1)
    assert p["diff_pct"] == 60.0
    assert por["Bolsa"]["status"] == "sin_receta" and float(por["Bolsa"]["real"]) == 30 and por["Bolsa"]["expected"] is None
    assert obr["closings"] == 0 and obr["rows"] == []
    assert d["branches"][0]["branch"]["id"] == clayton_branch.id   # la de más faltante primero

    # Encargado local: solo su sucursal.
    propia = client.get("/api/reports/inventory/variance", headers=hs).json()
    assert [b["branch"]["id"] for b in propia["branches"]] == [clayton_branch.id]
